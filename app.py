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
from typing import Optional, List, Tuple, Dict, Any

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
REQUIRED_HEADERS = [
    "country","city","lat","lon","project_name","years","status",
    "data_types","description","contact","access","url",
    "submitter_email","is_edit","edit_target","edit_request",
    "approved","created_at"
]
MESSAGE_HEADERS = ["name","email","message","approved","created_at"]

@st.cache_resource(show_spinner=False)
def _gs_client():
    try:
        creds_info = st.secrets.get("gcp_service_account")
        if not creds_info:
            return None, "Please configure gcp_service_account in secrets."
        scopes = ["https://www.googleapis.com/auth/spreadsheets",
                  "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
        client = gspread.authorize(creds)
        return client, None
    except Exception as e:
        return None, f"Google Sheets auth error: {e}"

def _open_or_create_worksheet(ws_name: str, init_headers: Optional[List[str]] = None):
    """Open a worksheet by name; if missing, create it and optionally write headers."""
    client, err = _gs_client()
    if err or client is None:
        return None, err or "Client unavailable."
    ss_id = st.secrets.get("SHEETS_SPREADSHEET_ID")
    if not ss_id:
        return None, "Please set SHEETS_SPREADSHEET_ID in secrets."
    try:
        ss = client.open_by_key(ss_id)
    except Exception as e:
        return None, f"Failed to open spreadsheet: {e}"

    try:
        ws = ss.worksheet(ws_name)
        return ws, None
    except gspread.exceptions.WorksheetNotFound:
        try:
            ncols = max(10, len(init_headers) if init_headers else 10)
            ws = ss.add_worksheet(title=ws_name, rows=200, cols=ncols)
            if init_headers:
                ws.update('A1', [init_headers])
            return ws, None
        except Exception as e:
            return None, f"Could not create worksheet '{ws_name}': {e}"
    except Exception as e:
        return None, f"Failed to open worksheet '{ws_name}': {e}"

def _gs_open_worksheet(ws_name: str):
    # Mantido para compatibilidade, mas preferimos _open_or_create_worksheet nos atalho abaixo
    return _open_or_create_worksheet(ws_name, None)

def _ws_projects():
    ws_name = st.secrets.get("SHEETS_WORKSHEET_NAME") or "projects"
    return _open_or_create_worksheet(ws_name, REQUIRED_HEADERS)

def _ws_messages():
    ws_name = st.secrets.get("SHEETS_MESSAGES_WS") or "messages"
    return _open_or_create_worksheet(ws_name, MESSAGE_HEADERS)

def _col_letter(idx0: int) -> str:
    n = idx0 + 1
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(r + 65) + s
    return s

def ensure_lat_lon_text_columns():
    """Force 'lat' and 'lon' as Plain text (TEXT) in the PROJECTS sheet."""
    ws, err = _ws_projects()
    if err or ws is None:
        return False, err or "Worksheet unavailable."
    try:
        header = ws.row_values(1)
        if not header:
            return False, "Projects sheet header (row 1) is empty."
        hdr_lower = [h.strip().lower() for h in header]
        if "lat" not in hdr_lower or "lon" not in hdr_lower:
            return False, "Projects header must contain 'lat' and 'lon'."
        lat_idx = hdr_lower.index("lat")
        lon_idx = hdr_lower.index("lon")
        lat_col = _col_letter(lat_idx)
        lon_col = _col_letter(lon_idx)
        ws.format(f"{lat_col}:{lat_col}", {"numberFormat": {"type": "TEXT"}})
        ws.format(f"{lon_col}:{lon_col}", {"numberFormat": {"type": "TEXT"}})
        return True, "lat/lon columns set to TEXT."
    except Exception as e:
        return False, f"Failed to format lat/lon columns: {e}"

def ensure_headers(required_headers=REQUIRED_HEADERS):
    """Ensure the PROJECTS header has all required columns."""
    ws, err = _ws_projects()
    if err or ws is None:
        return False, err or "Worksheet unavailable."
    try:
        header = ws.row_values(1)
        header = [h.strip() for h in header] if header else []
        missing = [h for h in required_headers if h not in header]
        if missing:
            new_header = header + missing
            ws.update('A1', [new_header])
        return True, "Projects header OK."
    except Exception as e:
        return False, f"Failed to adjust projects header: {e}"

def ensure_message_headers():
    """Ensure the MESSAGES sheet header exists."""
    ws, err = _ws_messages()
    if err or ws is None:
        return False, err or "Messages worksheet unavailable."
    try:
        header = ws.row_values(1)
        header = [h.strip() for h in header] if header else []
        missing = [h for h in MESSAGE_HEADERS if h not in header]
        if missing or not header:
            new_header = header + missing
            ws.update('A1', [new_header])
        return True, "Messages header OK."
    except Exception as e:
        return False, f"Failed to adjust messages header: {e}"

def try_send_email_via_emailjs(template_params: dict) -> tuple[bool, str]:
    svc = st.secrets.get("EMAILJS_SERVICE_ID")
    tpl = st.secrets.get("EMAILJS_TEMPLATE_ID")
    key = st.secrets.get("EMAILJS_PUBLIC_KEY")
    if not (svc and tpl and key):
        return False, "EmailJS is not configured."
    try:
        resp = requests.post(
            "https://api.emailjs.com/api/v1.0/email/send",
            json={"service_id": svc, "template_id": tpl, "user_id": key, "template_params": template_params},
            timeout=12,
        )
        return (True, "Notification email sent.") if resp.status_code == 200 else (False, f"EmailJS {resp.status_code}.")
    except Exception as e:
        return False, f"Email error: {e}"

def _parse_number_loose(x):
    """Robust number parser for lat/lon."""
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

# =============================================================================
# 2) LOAD COUNTRIES CSV (LOCAL ONLY)
# =============================================================================
COUNTRY_CSV_PATH = Path(__file__).parent / "country-coord.csv"

@st.cache_data(show_spinner=False)
def load_country_centers():
    """Read country-coord.csv and return {country: (lat, lon)} and DF."""
    df = pd.read_csv(COUNTRY_CSV_PATH, dtype=str, encoding="utf-8", on_bad_lines="skip")
    df.columns = [c.strip().lower() for c in df.columns]
    c_country = "country"
    c_lat = "latitude (average)"
    c_lon = "longitude (average)"
    if c_country not in df.columns or c_lat not in df.columns or c_lon not in df.columns:
        raise RuntimeError("CSV must contain: 'Country', 'Latitude (average)', 'Longitude (average)'.")
    df["lat"] = df[c_lat].apply(_parse_number_loose)
    df["lon"] = df[c_lon].apply(_parse_number_loose)
    df = df.dropna(subset=["lat", "lon"])
    mapping = {row[c_country]: (float(row["lat"]), float(row["lon"])) for _, row in df.iterrows()}
    return mapping, df

COUNTRY_CENTER_FULL, _df_countries = load_country_centers()

# =============================================================================
# 3) HEADER
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
            Living catalogue of projects and datasets (spatial / quantitative / qualitative) produced by the IDEAMAPS network and partners.
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# =============================================================================
# 4) LOAD APPROVED PROJECTS + MAP
# =============================================================================
@st.cache_data(show_spinner=False)
def load_approved_projects():
    """Load approved==TRUE, normalize lat/lon."""
    ws, err = _ws_projects()
    if err or ws is None:
        return pd.DataFrame(), False, err
    try:
        rows = ws.get_all_records()
        df = pd.DataFrame(rows)
        if df.empty:
            return pd.DataFrame(), False, "Projects sheet is empty."
        for c in REQUIRED_HEADERS:
            if c not in df.columns:
                df[c] = ""
        df = df[df["approved"].astype(str).str.upper().eq("TRUE")].copy()
        if "lat" in df.columns:
            df["lat"] = df["lat"].apply(_parse_number_loose)
        if "lon" in df.columns:
            df["lon"] = df["lon"].apply(_parse_number_loose)
        return df, True, None
    except Exception as e:
        return pd.DataFrame(), False, f"Error reading projects: {e}"

# — botão de refresh DEPOIS da função (evita NameError)
if st.sidebar.button("🔄 Check updates"):
    load_approved_projects.clear()
    load_country_centers.clear()
    ensure_headers()
    ensure_lat_lon_text_columns()
    ensure_message_headers()
    st.session_state["_last_refresh"] = datetime.utcnow().isoformat()
    st.rerun()

df_projects, from_sheets, debug_msg = load_approved_projects()
if not from_sheets and debug_msg:
    st.caption(f"⚠️ {debug_msg}")

def _as_float(x):
    v = _parse_number_loose(x)
    return float(v) if v is not None else None

def _clean_url(u):
    s = (u or "").strip()
    return s if (s.startswith("http://") or s.startswith("https://")) else s

if not df_projects.empty:
    if "lat" in df_projects.columns:
        df_projects["lat"] = df_projects["lat"].apply(_as_float)
    if "lon" in df_projects.columns:
        df_projects["lon"] = df_projects["lon"].apply(_as_float)
    df_map = df_projects.dropna(subset=["lat", "lon"]).copy()

    if df_map.empty:
        st.info("No valid points to plot (missing lat/lon).")
    else:
        groups = df_map.groupby(["country", "lat", "lon"], as_index=False)

        m = folium.Map(
            location=[df_map["lat"].mean(), df_map["lon"].mean()],
            zoom_start=2,
            tiles="CartoDB dark_matter"
        )

        for (country, lat, lon), g in groups:
            proj_dict = {}
            for _, r in g.iterrows():
                pname = str(r.get("project_name", "")).strip() or "(unnamed project)"
                city = str(r.get("city", "")).strip()
                url = _clean_url(r.get("url", ""))
                if pname not in proj_dict:
                    proj_dict[pname] = {"cities": set(), "urls": set()}
                if city:
                    proj_dict[pname]["cities"].add(city)
                if url:
                    proj_dict[pname]["urls"].add(url)

            lines = [
                "<div style='font-size:0.9rem; color:#0f172a;'>",
                f"<b>{country}</b>",
                "<ul style='padding-left:1rem; margin:0;'>"
            ]
            for pname, info in proj_dict.items():
                cities_txt = ", ".join(sorted(info["cities"])) if info["cities"] else "—"
                url_html = ""
                if info["urls"]:
                    u_any = sorted(info["urls"])[0]
                    url_html = (
                        " — "
                        f"<a href='{u_any}' target='_blank' "
                        "style='color:#2563eb; text-decoration:none;'>link</a>"
                    )
                lines.append(f"<li><b>{pname}</b> — {cities_txt}{url_html}</li>")
            lines.append("</ul></div>")
            html_block = "".join(lines)

            any_active = any(str(x).lower() == "active" for x in g["status"].tolist())
            color = "#38bdf8" if any_active else "#facc15"

            folium.CircleMarker(
                location=[lat, lon],
                radius=6,
                color=color,
                fill=True,
                fill_opacity=0.9,
                tooltip=folium.Tooltip(
                    html_block, sticky=True, direction='top',
                    style="background:#ffffff; color:#0f172a; border:1px solid #cbd5e1; border-radius:8px; padding:8px;"
                ),
                popup=folium.Popup(html_block, max_width=380),
            ).add_to(m)

        st_folium(m, height=520, width=None)
else:
    st.info("No approved projects found.")

st.markdown("---")

# =============================================================================
# 5) BROWSE & LOAD FOR EDIT (NO INLINE EDIT)
# =============================================================================
st.subheader("Browse existing entries")
if df_projects.empty:
    st.info("No data to display yet.")
else:
    # chave única legível para seleção
    def _mk_key(row: pd.Series) -> str:
        return " || ".join([
            str(row.get("project_name","")).strip() or "(unnamed)",
            str(row.get("country","")).strip() or "—",
            str(row.get("city","")).strip() or "—",
        ])

    table_cols = ["project_name","country","city","years","data_types","contact","access","url","lat","lon"]
    table_cols = [c for c in table_cols if c in df_projects.columns]
    df_view = df_projects[table_cols].copy()
    df_view["__key__"] = df_projects.apply(_mk_key, axis=1)

    # Tabela somente leitura
    st.dataframe(
        df_view.drop(columns=["__key__"]),
        use_container_width=True,
        hide_index=True
    )

    # Seletor de linha para edição
    options = df_view["__key__"].tolist()
    selected_key = st.selectbox(
        "Select a row to edit (loads into the submission form below)",
        options=[""] + options,
        index=0
    )

    # Botão: carregar no formulário para edição
    if selected_key and st.button("✎ Edit this row"):
        # linha escolhida na visão
        sel = df_view[df_view["__key__"] == selected_key].iloc[0].to_dict()

        # índice correspondente no df_projects (para acessar colunas não exibidas)
        idx_sel = df_view.index[df_view["__key__"] == selected_key][0]

        # guarda o "before" completo no session_state para compor o EDIT
        st.session_state["_edit_mode"]  = True
        st.session_state["_before_row"] = df_projects.loc[idx_sel].to_dict()

        # prefill do formulário
        st.session_state["prefill_project_name"] = str(sel.get("project_name",""))
        st.session_state["countries_sel"] = [str(sel.get("country",""))] if sel.get("country","") else []
        st.session_state["city_list"] = []
        if sel.get("country","") and str(sel.get("city","")).strip():
            st.session_state["city_list"] = [f"{sel['country']} — {sel['city']}"]

        st.session_state["prefill_years"]       = str(sel.get("years",""))
        st.session_state["prefill_data_types"]  = str(sel.get("data_types",""))
        st.session_state["prefill_description"] = str(df_projects.loc[idx_sel].get("description",""))
        st.session_state["prefill_contact"]     = str(sel.get("contact",""))
        st.session_state["prefill_access"]      = str(sel.get("access",""))
        st.session_state["prefill_url"]         = str(sel.get("url",""))
        st.session_state["prefill_lat"]         = sel.get("lat", "")
        st.session_state["prefill_lon"]         = sel.get("lon", "")

        st.info("Loaded into the submission form below. Make your changes and submit to queue an **edit**.")
        st.rerun()


# =============================================================================
# 6) ADD / EDIT PROJECT (ALWAYS GOES TO REVIEW QUEUE)
# =============================================================================
st.header("Add / Edit project (goes to review queue)")

if "city_list" not in st.session_state:
    st.session_state.city_list = []
if "_edit_mode" not in st.session_state:
    st.session_state["_edit_mode"] = False
if "_before_row" not in st.session_state:
    st.session_state["_before_row"] = {}

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

countries_options = sorted(COUNTRY_CENTER_FULL.keys())
st.session_state.countries_sel = st.multiselect(
    "Implementation countries (one or more)",
    options=countries_options,
    default=st.session_state.get("countries_sel", []),
    help="Select all countries where this project is implemented."
)
options_for_city = st.session_state.get("countries_sel", [])

with st.form("add_project_form", clear_on_submit=False):
    # aviso de modo edição
    if st.session_state["_edit_mode"]:
        st.markdown("🟦 **Editing an existing entry** — your submission will be queued as an **edit** to the catalogue.")

    new_name = st.text_input(
        "Project name",
        value=st.session_state.get("prefill_project_name",""),
        placeholder="e.g., IDEAMAPS Lagos / Urban Deprivation Mapping"
    )
    submitter_email = st.text_input("Submitter email (required for review)", placeholder="name@org.org")

    st.markdown("**Cities covered**")
    st.caption("Tip: add a city to one selected country or add to **all** selected countries. Use commas to add multiple cities at once.")

    colc1, colc2, colc3 = st.columns([2, 2, 1])
    with colc1:
        selected_country_for_city = st.selectbox(
            "Select implementation country for the city",
            options=options_for_city,
            index=0 if options_for_city else None,
            disabled=not bool(options_for_city),
            key="country_for_city",
        )
    with colc2:
        city_to_add = st.text_input("City (accepts multiple, separated by commas)", key="city_to_add")
    with colc3:
        st.write("")
        add_one = st.form_submit_button("➕ Add to this country", use_container_width=True, disabled=not bool(options_for_city))

    if add_one and selected_country_for_city and city_to_add.strip():
        for c in [c.strip() for c in city_to_add.split(",") if c.strip()]:
            _add_city_entry(selected_country_for_city, c)

    add_all = st.form_submit_button(
        "➕ Add to ALL selected countries",
        use_container_width=True,
        disabled=not (options_for_city and city_to_add.strip())
    )
    if add_all:
        cities_bulk = [c.strip() for c in city_to_add.split(",") if c.strip()]
        for ctry in options_for_city:
            for c in cities_bulk:
                _add_city_entry(ctry, c)

    # lista com botões Remover
    if st.session_state.city_list:
        st.caption("Cities added (country — city):")
        to_remove_idx = None
        for i, item in enumerate(st.session_state.city_list):
            c1, c2 = st.columns([6,1])
            with c1: st.write(f"- {item}")
            with c2:
                if st.form_submit_button("Remove", key=f"rm_{i}"):
                    to_remove_idx = i
        if to_remove_idx is not None:
            st.session_state.city_list.pop(to_remove_idx)
            st.rerun()

        if st.checkbox("Clear all cities"):
            st.session_state.city_list = []

    # sem STATUS no formulário
    new_years  = st.text_input("Years (e.g. 2022–2024)", value=st.session_state.get("prefill_years",""))
    new_types  = st.text_area("Data types (Spatial? Quantitative? Qualitative?)", value=st.session_state.get("prefill_data_types",""))
    new_desc   = st.text_area("Short description", value=st.session_state.get("prefill_description",""))
    new_contact= st.text_input("Contact / Responsible institution", value=st.session_state.get("prefill_contact",""))
    new_access = st.text_input("Access / License / Ethics", value=st.session_state.get("prefill_access",""))
    new_url    = st.text_input("Project URL (optional)", value=st.session_state.get("prefill_url",""))
    submitted  = st.form_submit_button("Submit for review")

def append_submission_to_sheet(payload: dict) -> tuple[bool, str]:
    """Append a row to the PROJECTS sheet (plain text for lat/lon)."""
    ws, err = _ws_projects()
    if err or ws is None:
        return False, err
    try:
        ensure_headers(); ensure_lat_lon_text_columns()

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
            "status": payload.get("status", ""),  # ficará vazio nas novas submissões
            "data_types": payload.get("data_types", ""),
            "description": payload.get("description", ""),
            "contact": payload.get("contact", ""),
            "access": payload.get("access", ""),
            "url": payload.get("url", ""),
            "submitter_email": payload.get("submitter_email", ""),
            "is_edit": "TRUE" if payload.get("is_edit") else "FALSE",
            "edit_target": payload.get("edit_target", ""),
            "edit_request": payload.get("edit_request", ""),
            "approved": "FALSE",
            "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
        header = ws.row_values(1)
        values = [row.get(col, "") for col in header] if header else list(row.values())
        ws.append_row(values, value_input_option="RAW")
        return True, "Saved to Google Sheets."
    except Exception as e:
        return False, f"Write error: {e}"

def _summarize_changes(before: dict, after: dict) -> str:
    cols = ["country","city","lat","lon","project_name","years","status","data_types","description","contact","access","url"]
    changes = []
    for c in cols:
        bv = before.get(c, "")
        av = after.get(c, "")
        if str(bv) != str(av):
            bv_s = "" if (bv is None or (isinstance(bv, float) and pd.isna(bv))) else str(bv)
            av_s = "" if (av is None or (isinstance(av, float) and pd.isna(av))) else str(av)
            if len(bv_s) > 120: bv_s = bv_s[:117]+"…"
            if len(av_s) > 120: av_s = av_s[:117]+"…"
            changes.append(f"{c}: '{bv_s}' → '{av_s}'")
    return "; ".join(changes) if changes else "No visible change"

if submitted:
    # validações
    if not st.session_state.get("countries_sel"):
        st.warning("Please select at least one implementation country.")
    elif not (st.session_state.city_list or st.session_state.get("countries_sel")):
        st.warning("Please add at least one (country — city) pair or select countries.")
    elif not submitter_email.strip():
        st.warning("Please provide a submitter email.")
    elif not new_name.strip():
        st.warning("Please provide a Project name.")
    else:
        total_rows, ok_all, msg_any = 0, True, None
        entries_preview = []

        # gera pares país—cidade; se não houver cidade, cria registro por país sem cidade
        pairs = st.session_state.city_list[:] if st.session_state.city_list else [f"{c} — " for c in st.session_state.get("countries_sel", [])]

        # “before” para compor resumo de edição, se for o caso
        before = st.session_state.get("_before_row", {}) if st.session_state.get("_edit_mode") else {}

        for pair in pairs:
            if "—" not in pair:
                continue
            country, city = [p.strip() for p in pair.split("—", 1)]
            lat, lon = (None, None)
            if country:
                lat, lon = COUNTRY_CENTER_FULL.get(country, (None, None))

            after_payload = {
                "country": country,
                "city": city,
                "lat": lat,
                "lon": lon,
                "project_name": new_name,
                "years": new_years,
                "status": "",  # sem status no formulário
                "data_types": new_types,
                "description": new_desc,
                "contact": new_contact,
                "access": new_access,
                "url": new_url,
                "submitter_email": submitter_email,
            }

            # se está em modo edição, marca como edição e cria resumo de mudanças
            if st.session_state.get("_edit_mode"):
                edit_target = before.get("project_name", new_name)
                edit_request = _summarize_changes(before, after_payload)
                payload = {**after_payload, "is_edit": True, "edit_target": edit_target, "edit_request": edit_request}
            else:
                payload = {**after_payload, "is_edit": False, "edit_target": "", "edit_request": "New submission"}

            ok_sheet, msg_sheet = append_submission_to_sheet(payload)
            ok_all &= ok_sheet
            msg_any = msg_sheet
            total_rows += 1
            entries_preview.append(payload)

        if ok_all:
            st.success(f"✅ {'Edit queued' if st.session_state.get('_edit_mode') else 'Submission saved'} ({total_rows} row(s) added).")
            # limpa estado de edição e prefills
            for k in list(st.session_state.keys()):
                if str(k).startswith("prefill_"):
                    st.session_state[k] = ""
            st.session_state["_edit_mode"] = False
            st.session_state["_before_row"] = {}
            st.session_state["city_list"] = []
        else:
            st.warning(f"⚠️ Some rows failed: {msg_any}")

        ok_mail, msg_mail = try_send_email_via_emailjs({
            "project_name": new_name,
            "entries": ", ".join([f"{p['country']} — {p['city']}" for p in entries_preview]),
            "status": "",  # sem status
            "years": new_years,
            "url": new_url,
            "submitter_email": submitter_email,
            "is_edit": "yes" if st.session_state.get("_edit_mode") else "no",
            "edit_target": st.session_state.get("_before_row", {}).get("project_name",""),
        })
        if ok_mail:
            st.info("📨 Notification email sent.")
        else:
            st.caption(msg_mail)

        st.markdown("**Submission preview (first rows):**")
        st.code(entries_preview[:3], language="python")

st.markdown("---")

# =============================================================================
# 7) COMMUNITY MESSAGE BOARD (SUBMIT → APPROVAL → DISPLAY)
# =============================================================================
st.header("Community message board")

with st.form("message_form"):
    msg_name = st.text_input("Your full name", placeholder="First Last")
    msg_email = st.text_input("Email (optional)", placeholder="name@org.org")
    msg_text = st.text_area("Your message", placeholder="Write your note to the IDEAMAPS team / partners...")
    send_msg = st.form_submit_button("Send message for approval")

def append_message_to_sheet(name: str, email: str, message: str) -> tuple[bool, str]:
    ws, err = _ws_messages()
    if err or ws is None:
        return False, err or "Messages worksheet unavailable."
    try:
        ensure_message_headers()
        row = {
            "name": name.strip(),
            "email": email.strip(),
            "message": message.strip(),
            "approved": "FALSE",
            "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
        header = ws.row_values(1)
        values = [row.get(col, "") for col in header] if header else list(row.values())
        ws.append_row(values, value_input_option="RAW")
        return True, "Message queued for approval."
    except Exception as e:
        return False, f"Write error: {e}"

if send_msg:
    if not msg_name.strip():
        st.warning("Please provide your full name.")
    elif not msg_text.strip():
        st.warning("Please write a message.")
    else:
        ok, msg = append_message_to_sheet(msg_name, msg_email or "", msg_text)
        if ok:
            st.success("✅ Message sent for approval.")
        else:
            st.warning(f"⚠️ {msg}")

@st.cache_data(show_spinner=False)
def load_approved_messages():
    ws, err = _ws_messages()
    if err or ws is None:
        return pd.DataFrame(), False, err
    try:
        rows = ws.get_all_records()
        df = pd.DataFrame(rows)
        if df.empty:
            return pd.DataFrame(), True, None
        for c in MESSAGE_HEADERS:
            if c not in df.columns:
                df[c] = ""
        df = df[df["approved"].astype(str).str.upper().eq("TRUE")].copy()
        return df.sort_values("created_at", ascending=False), True, None
    except Exception as e:
        return pd.DataFrame(), False, f"Error reading messages: {e}"

st.subheader("Recent public messages")
df_msgs, ok_msgs, err_msgs = load_approved_messages()
if not ok_msgs:
    st.caption(f"⚠️ {err_msgs}")
elif df_msgs.empty:
    st.info("No public messages yet.")
else:
    for _, r in df_msgs.head(12).iterrows():
        name = r.get("name","").strip() or "Anonymous"
        email = r.get("email","").strip()
        created = r.get("created_at","")
        body = r.get("message","").strip()
        st.markdown(
            f"""
            <div style="border:1px solid #334155;background:#0b1220;border-radius:12px;padding:12px;margin-bottom:10px;">
              <div style="color:#e5e7eb;font-weight:600;">{name}
                <span style="color:#64748b;font-weight:400;font-size:0.85rem;">{(' • '+email) if email else ''}</span>
              </div>
              <div style="color:#94a3b8;font-size:0.75rem;margin-top:2px;">{created}</div>
              <div style="color:#cbd5e1;margin-top:8px;">{body}</div>
            </div>
            """,
            unsafe_allow_html=True
        )

