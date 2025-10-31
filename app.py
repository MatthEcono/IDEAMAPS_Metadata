# app.py
import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from datetime import datetime
from pathlib import Path
import re
import requests
import gspread
from google.oauth2.service_account import Credentials

# =============================================================================
# 0) PAGE CONFIG
# =============================================================================
st.set_page_config(
    page_title="IDEAMAPS Global Metadata Explorer",
    layout="wide",
    page_icon="🌍",
)

# =============================================================================
# 1) GOOGLE SHEETS / EMAILJS
# =============================================================================
@st.cache_resource(show_spinner=False)
def _gs_worksheet():
    try:
        ss_id = st.secrets.get("SHEETS_SPREADSHEET_ID")
        ws_name = st.secrets.get("SHEETS_WORKSHEET_NAME")
        creds_info = st.secrets.get("gcp_service_account")
        if not (ss_id and ws_name and creds_info):
            return None, "⚠️ Configure SHEETS_SPREADSHEET_ID, SHEETS_WORKSHEET_NAME e gcp_service_account em secrets."
        scopes = ["https://www.googleapis.com/auth/spreadsheets",
                  "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
        client = gspread.authorize(creds)
        ws = client.open_by_key(ss_id).worksheet(ws_name)
        return ws, None
    except Exception as e:
        return None, f"Erro de conexão com Google Sheets: {e}"

def _col_letter(idx0: int) -> str:
    n = idx0 + 1
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(r + 65) + s
    return s

def ensure_lat_lon_text_columns():
    """Força colunas 'lat' e 'lon' como Plain text (TEXT) no Sheets."""
    ws, err = _gs_worksheet()
    if err or ws is None:
        return False, err or "Worksheet indisponível."
    try:
        header = ws.row_values(1)
        if not header:
            return False, "Sem cabeçalho na planilha."
        hdr_lower = [h.strip().lower() for h in header]
        if "lat" not in hdr_lower or "lon" not in hdr_lower:
            return False, "Cabeçalho precisa conter 'lat' e 'lon'."
        lat_idx = hdr_lower.index("lat")
        lon_idx = hdr_lower.index("lon")
        lat_col = _col_letter(lat_idx)
        lon_col = _col_letter(lon_idx)
        ws.format(f"{lat_col}:{lat_col}", {"numberFormat": {"type": "TEXT"}})
        ws.format(f"{lon_col}:{lon_col}", {"numberFormat": {"type": "TEXT"}})
        return True, "Colunas lat/lon formatadas como TEXT."
    except Exception as e:
        return False, f"Falha ao formatar colunas: {e}"

def _parse_number_loose(x):
    """
    Parser tolerante:
      - remove aspas simples/duplas e espaços
      - trata última vírgula/ponto como separador decimal
      - remove separadores de milhar
    """
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    s = str(x).strip().strip("'").strip('"')
    if not s:
        return None
    if ("," in s) or ("." in s):
        last_comma = s.rfind(",")
        last_dot   = s.rfind(".")
        last_sep   = max(last_comma, last_dot)
        if last_sep >= 0:
            int_part  = re.sub(r"[^\d\-\+]", "", s[:last_sep]) or "0"
            frac_part = re.sub(r"\D", "", s[last_sep+1:]) or "0"
            try:
                return float(f"{int_part}.{frac_part}")
            except Exception:
                pass
    raw = re.sub(r"[^\d\-\+]", "", s)
    try:
        return float(raw)
    except Exception:
        try:
            return float(s.replace(",", "."))
        except Exception:
            return None

@st.cache_data(show_spinner=False)
def load_approved_projects():
    """Carrega approved==TRUE e normaliza lat/lon (robusto a aspas/locale)."""
    ws, err = _gs_worksheet()
    if err or ws is None:
        return pd.DataFrame(), False, err
    try:
        rows = ws.get_all_records()
        df = pd.DataFrame(rows)
        if df.empty:
            return pd.DataFrame(), False, "Planilha vazia."
        if "approved" in df.columns:
            df = df[df["approved"].astype(str).str.upper().eq("TRUE")].copy()
        if "lat" in df.columns:
            df["lat"] = df["lat"].apply(_parse_number_loose)
        if "lon" in df.columns:
            df["lon"] = df["lon"].apply(_parse_number_loose)
        return df, True, None
    except Exception as e:
        return pd.DataFrame(), False, f"Erro lendo planilha: {e}"

def append_submission_to_sheet(payload: dict) -> tuple[bool, str]:
    """
    Escreve uma linha no Sheets (Plain text para lat/lon, sem aspas).
    """
    ws, err = _gs_worksheet()
    if err or ws is None:
        return False, err
    try:
        ensure_lat_lon_text_columns()

        def _fmt_num_str(v):
            try:
                return f"{float(v):.6f}"
            except Exception:
                return ""

        row = {
            "country": payload.get("country", ""),
            "city": payload.get("city", ""),
            "lat": _fmt_num_str(payload.get("lat", "")),
            "lon": _fmt_num_str(payload.get("lon", "")),
            "project_name": payload.get("project_name", ""),
            "years": payload.get("years", ""),
            "status": payload.get("status", ""),
            "data_types": payload.get("data_types", ""),
            "description": payload.get("description", ""),
            "contact": payload.get("contact", ""),
            "access": payload.get("access", ""),
            "url": payload.get("url", ""),
            "approved": "FALSE",
            "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
        header = ws.row_values(1)
        values = [row.get(col, "") for col in header] if header else list(row.values())
        ws.append_row(values, value_input_option="RAW")
        return True, "Salvo no Google Sheets."
    except Exception as e:
        return False, f"Erro ao gravar no Sheets: {e}"

def try_send_email_via_emailjs(template_params: dict) -> tuple[bool, str]:
    svc = st.secrets.get("EMAILJS_SERVICE_ID")
    tpl = st.secrets.get("EMAILJS_TEMPLATE_ID")
    key = st.secrets.get("EMAILJS_PUBLIC_KEY")
    if not (svc and tpl and key):
        return False, "EmailJS não configurado."
    try:
        resp = requests.post(
            "https://api.emailjs.com/api/v1.0/email/send",
            json={"service_id": svc, "template_id": tpl, "user_id": key, "template_params": template_params},
            timeout=12,
        )
        return (True, "Email enviado com sucesso.") if resp.status_code == 200 else (False, f"EmailJS {resp.status_code}.")
    except Exception as e:
        return False, f"Erro no envio de e-mail: {e}"

# =============================================================================
# 2) PAISES: CSV LOCAL (SEM FALLBACK)
# =============================================================================
COUNTRY_CSV_PATH = Path(__file__).parent / "country-coord.csv"

@st.cache_data(show_spinner=False)
def load_country_centers():
    """Lê country-coord.csv e retorna {country: (lat, lon)} e DF."""
    df = pd.read_csv(COUNTRY_CSV_PATH, dtype=str, encoding="utf-8", on_bad_lines="skip")
    df.columns = [c.strip().lower() for c in df.columns]
    c_country = "country"
    c_lat = "latitude (average)"
    c_lon = "longitude (average)"
    if c_country not in df.columns or c_lat not in df.columns or c_lon not in df.columns:
        raise RuntimeError("CSV precisa conter colunas: 'Country', 'Latitude (average)', 'Longitude (average)'.")
    df["lat"] = df[c_lat].apply(_parse_number_loose)
    df["lon"] = df[c_lon].apply(_parse_number_loose)
    df = df.dropna(subset=["lat", "lon"])
    mapping = {row[c_country]: (float(row["lat"]), float(row["lon"])) for _, row in df.iterrows()}
    return mapping, df

COUNTRY_CENTER_FULL, _df_countries = load_country_centers()

# =============================================================================
# 3) HEADER + REFRESH
# =============================================================================
st.markdown(
    """
    <div style="background: linear-gradient(90deg,#0f172a,#1e293b,#0f172a);
        padding:1.2rem 1.5rem;border-radius:0.75rem;border:1px solid #334155;
        margin-bottom:1rem;">
        <div style="color:#fff;font-size:1.2rem;font-weight:600;">
            IDEAMAPS Global Metadata Explorer 🌍
        </div>
        <div style="color:#94a3b8;font-size:0.85rem;line-height:1.3;">
            Catálogo vivo de projetos e datasets (spatial / quantitative / qualitative)
            produzidos pela rede IDEAMAPS e parceiros.
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

if st.sidebar.button("🔄 Checar atualizações"):
    load_approved_projects.clear()
    load_country_centers.clear()
    ensure_lat_lon_text_columns()
    st.session_state["_last_refresh"] = datetime.utcnow().isoformat()
    st.rerun()

# =============================================================================
# 4) CARREGAR PROJETOS + MAPA
# =============================================================================
df_projects, from_sheets, debug_msg = load_approved_projects()
if not from_sheets and debug_msg:
    st.caption(f"⚠️ {debug_msg}")

def _as_float(x):
    v = _parse_number_loose(x)
    return float(v) if v is not None else None

if not df_projects.empty:
    if "lat" in df_projects.columns: df_projects["lat"] = df_projects["lat"].apply(_as_float)
    if "lon" in df_projects.columns: df_projects["lon"] = df_projects["lon"].apply(_as_float)
    df_map = df_projects.dropna(subset=["lat", "lon"]).copy()

    if df_map.empty:
        st.info("Não há pontos válidos para plotar (lat/lon ausentes).")
    else:
        m = folium.Map(
            location=[df_map["lat"].mean(), df_map["lon"].mean()],
            zoom_start=2,
            tiles="CartoDB dark_matter"
        )
        for _, row in df_map.iterrows():
            color = "#38bdf8" if str(row.get("status","")).lower() == "active" else "#facc15"
            folium.CircleMarker(
                location=[row["lat"], row["lon"]],
                radius=6,
                color=color,
                fill=True,
                fill_opacity=0.85,
                tooltip=f"{row.get('project_name','')} — {row.get('city','')} ({row.get('country','')})",
            ).add_to(m)
        st_folium(m, height=500, width=None)
else:
    st.info("Nenhum projeto aprovado encontrado no momento.")

# Tabela + downloads
if not df_projects.empty:
    st.subheader("Data on map (downloadable)")
    cols_show = ["country","city","lat","lon","project_name","years","status","url"]
    show_cols = [c for c in cols_show if c in df_projects.columns]
    tbl = (df_projects[show_cols]
           .copy()
           .sort_values(["country","city","project_name"], na_position="last")
           .reset_index(drop=True))
    if "lat" in tbl.columns:
        tbl["lat"] = tbl["lat"].apply(lambda x: f"{float(x):.6f}" if pd.notna(x) else "")
    if "lon" in tbl.columns:
        tbl["lon"] = tbl["lon"].apply(lambda x: f"{float(x):.6f}" if pd.notna(x) else "")
    st.dataframe(tbl, use_container_width=True)

    csv_bytes = tbl.to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ Download CSV", data=csv_bytes, file_name="ideamaps_on_map.csv", mime="text/csv")

    html_bytes = tbl.to_html(index=False, escape=False).encode("utf-8")
    st.download_button("⬇️ Download HTML", data=html_bytes, file_name="ideamaps_on_map.html", mime="text/html")

st.markdown("---")

# =============================================================================
# 5) FORMULÁRIO: COUNTRY (fora do form) → CITY (no form)
# =============================================================================
st.header("Add new project (goes to review queue)")

if "city_list" not in st.session_state:
    st.session_state.city_list = []

def get_country_center(name: str):
    tpl = COUNTRY_CENTER_FULL.get(name)
    if tpl:
        try: return float(tpl[0]), float(tpl[1])
        except Exception: return None, None
    return None, None

def _add_city_entry(country, city):
    if country and city:
        pair = f"{country} — {city}"
        if pair not in st.session_state.city_list:
            st.session_state.city_list.append(pair)

# Countries (fora do form para atualizar options ao vivo)
countries_options = sorted(COUNTRY_CENTER_FULL.keys())
st.session_state.countries_sel = st.multiselect(
    "Countries (one or more)",
    options=countries_options,
    default=st.session_state.get("countries_sel", []),
    help="Select all countries covered by this project."
)
options_for_city = st.session_state.get("countries_sel", [])

# Form principal
with st.form("add_project_form", clear_on_submit=False):
    new_name = st.text_input("Project name", placeholder="e.g., IDEAMAPS Lagos / Urban Deprivation Mapping")

    colc1, colc2, colc3 = st.columns([2, 2, 1])
    with colc1:
        selected_country_for_city = st.selectbox(
            "Select country for this city",
            options=options_for_city,
            index=0 if options_for_city else None,
            disabled=not bool(options_for_city),
            key="country_for_city",
        )
    with colc2:
        city_to_add = st.text_input("City (type name)", key="city_to_add")
    with colc3:
        st.write("")
        add_one = st.form_submit_button("➕ Add city", use_container_width=True, disabled=not bool(options_for_city))

    if add_one and selected_country_for_city and city_to_add.strip():
        _add_city_entry(selected_country_for_city, city_to_add.strip())

    add_all = st.form_submit_button(
        "➕ Add city to all selected countries",
        use_container_width=True,
        disabled=not (options_for_city and city_to_add.strip())
    )
    if add_all:
        for ctry in options_for_city:
            _add_city_entry(ctry, city_to_add.strip())

    if st.session_state.city_list:
        st.caption("Cities added (country — city):")
        for item in st.session_state.city_list:
            st.write(f"- {item}")
        if st.checkbox("Clear all cities"):
            st.session_state.city_list = []

    new_years  = st.text_input("Years (e.g. 2022–2024)")
    new_status = st.selectbox("Status", ["Active", "Legacy", "Completed", "Planning"])
    new_types  = st.text_area("Data types (Spatial? Quantitative? Qualitative?)")
    new_desc   = st.text_area("Short description")
    new_contact= st.text_input("Contact / Responsible institution")
    new_access = st.text_input("Access / License / Ethics")
    new_url    = st.text_input("Project URL (optional)")

    submitted = st.form_submit_button("Submit for review")

if submitted:
    if not new_name.strip():
        st.warning("Please provide a Project name.")
    elif not st.session_state.get("countries_sel"):
        st.warning("Please select at least one country.")
    elif not st.session_state.city_list:
        st.warning("Please add at least one (country — city) pair.")
    else:
        total_rows, ok_all, msg_any = 0, True, None
        entries_preview = []

        for pair in st.session_state.city_list:
            if "—" not in pair:
                continue
            country, city = [p.strip() for p in pair.split("—", 1)]
            lat, lon = get_country_center(country)

            payload = {
                "country": country,
                "city": city,
                "lat": lat,
                "lon": lon,
                "project_name": new_name,
                "years": new_years,
                "status": new_status,
                "data_types": new_types,
                "description": new_desc,
                "contact": new_contact,
                "access": new_access,
                "url": new_url,
            }

            ok_sheet, msg_sheet = append_submission_to_sheet(payload)
            ok_all &= ok_sheet
            msg_any = msg_sheet
            total_rows += 1
            entries_preview.append(payload)

        if ok_all:
            st.success(f"✅ Submission saved ({total_rows} row(s) added).")
            st.session_state.city_list = []
        else:
            st.warning(f"⚠️ Some rows failed: {msg_any}")

        ok_mail, msg_mail = try_send_email_via_emailjs({
            "project_name": new_name,
            "entries": ", ".join([f"{p['country']} — {p['city']}" for p in entries_preview]),
            "status": new_status,
            "years": new_years,
            "url": new_url,
        })
        if ok_mail:
            st.info("📨 Notification email sent.")
        else:
            st.caption(msg_mail)

        st.markdown("**Submission preview (first rows):**")
        st.code(entries_preview[:3], language="python")
