# app.py
import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from datetime import datetime
import re
from pathlib import Path
import requests
import gspread
from google.oauth2.service_account import Credentials

# =============================================================================
# 0) CONFIGURAÇÕES DE PÁGINA
# =============================================================================
st.set_page_config(
    page_title="IDEAMAPS Global Metadata Explorer",
    layout="wide",
    page_icon="🌍",
)

# =============================================================================
# 1) CONEXÃO COM GOOGLE SHEETS / EMAILJS
# =============================================================================
@st.cache_resource(show_spinner=False)
def _gs_worksheet():
    try:
        ss_id = st.secrets.get("SHEETS_SPREADSHEET_ID")
        ws_name = st.secrets.get("SHEETS_WORKSHEET_NAME")
        creds_info = st.secrets.get("gcp_service_account")
        if not (ss_id and ws_name and creds_info):
            return None, "⚠️ Falta configurar SHEETS_SPREADSHEET_ID / WORKSHEET_NAME / gcp_service_account."
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
        client = gspread.authorize(creds)
        ws = client.open_by_key(ss_id).worksheet(ws_name)
        return ws, None
    except Exception as e:
        return None, f"Erro de conexão com Google Sheets: {e}"

@st.cache_data(show_spinner=False)
def load_approved_projects():
    ws, err = _gs_worksheet()
    if err or ws is None:
        return pd.DataFrame(), False, err
    try:
        rows = ws.get_all_records()
        df = pd.DataFrame(rows)
        if df.empty:
            return pd.DataFrame(), False, "Planilha vazia."
        if "approved" in df.columns:
            df = df[df["approved"].astype(str).str.upper().eq("TRUE")]
        return df, True, None
    except Exception as e:
        return pd.DataFrame(), False, f"Erro lendo planilha: {e}"

def append_submission_to_sheet(payload: dict) -> tuple[bool, str]:
    ws, err = _gs_worksheet()
    if err or ws is None:
        return False, err
    try:
        def _fmt_txt(v):
            try:
                return f"'{float(v):.6f}'"
            except Exception:
                return f"'{str(v)}'"

        row = {
            "country": payload.get("country", ""),
            "city": payload.get("city", ""),
            "lat": _fmt_txt(payload.get("lat", "")),
            "lon": _fmt_txt(payload.get("lon", "")),
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
        if resp.status_code == 200:
            return True, "Email enviado com sucesso."
        return False, f"EmailJS retornou {resp.status_code}."
    except Exception as e:
        return False, f"Erro no envio de e-mail: {e}"

# =============================================================================
# 2) LEITURA DO CSV DE PAÍSES
# =============================================================================
COUNTRY_CSV_PATH = Path(__file__).parent / "country-coord.csv"

@st.cache_data(show_spinner=False)
def load_country_centers():
    df = pd.read_csv(COUNTRY_CSV_PATH, dtype=str, encoding="utf-8", on_bad_lines="skip")
    df.columns = [c.strip().lower() for c in df.columns]

    def _num_locale_safe(x):
        if pd.isna(x):
            return None
        t = str(x).strip().replace(",", ".")
        try:
            return float(t)
        except Exception:
            return None

    df["lat"] = df["latitude (average)"].apply(_num_locale_safe)
    df["lon"] = df["longitude (average)"].apply(_num_locale_safe)
    df = df.dropna(subset=["lat", "lon"])
    mapping = {row["country"]: (row["lat"], row["lon"]) for _, row in df.iterrows()}
    return mapping, df

COUNTRY_CENTER_FULL, _df_countries = load_country_centers()

# =============================================================================
# 3) HEADER E BOTÃO DE ATUALIZAÇÃO
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
    st.session_state["_last_refresh"] = datetime.utcnow().isoformat()
    st.rerun()

# =============================================================================
# 4) CARREGA DADOS DE PROJETOS E MAPA
# =============================================================================
df_projects, from_sheets, debug_msg = load_approved_projects()
if not from_sheets:
    st.info("ℹ️ Usando fallback local (planilha não disponível).")
    if debug_msg:
        st.caption(debug_msg)

if not df_projects.empty:
    df_projects = df_projects.dropna(subset=["lat", "lon"])
    m = folium.Map(location=[0, 0], zoom_start=2, tiles="CartoDB dark_matter")

    for _, row in df_projects.iterrows():
        try:
            lat, lon = float(row["lat"]), float(row["lon"])
        except Exception:
            continue
        color = "#38bdf8" if str(row.get("status","")).lower() == "active" else "#facc15"
        folium.CircleMarker(
            location=[lat, lon],
            radius=6,
            color=color,
            fill=True,
            fill_opacity=0.85,
            tooltip=f"{row.get('project_name','')} ({row.get('country','')})",
        ).add_to(m)
    st_folium(m, height=500, width=None)
else:
    st.info("Nenhum projeto aprovado encontrado no momento.")

# =============================================================================
# 5) FORMULÁRIO DE SUBMISSÃO (PAÍS↔CIDADE)
# =============================================================================
st.header("Add new project (goes to review queue)")

if "city_list" not in st.session_state:
    st.session_state.city_list = []

def get_country_center(name: str):
    tpl = COUNTRY_CENTER_FULL.get(name)
    if tpl:
        return float(tpl[0]), float(tpl[1])
    return None, None

def _add_city_entry(country, city):
    """Adiciona par (country, city) à sessão."""
    if country and city:
        pair = f"{country} — {city}"
        if pair not in st.session_state.city_list:
            st.session_state.city_list.append(pair)

with st.form("add_project_form", clear_on_submit=False):
    new_name = st.text_input("Project name", placeholder="e.g., IDEAMAPS Lagos / Urban Deprivation Mapping")

    countries_options = sorted(COUNTRY_CENTER_FULL.keys())
    selected_countries = st.multiselect("Countries (one or more)", options=countries_options, default=[])

    colc1, colc2, colc3 = st.columns([2, 2, 1])
    with colc1:
        selected_country_for_city = st.selectbox("Select country for this city", options=selected_countries)
    with colc2:
        city_to_add = st.text_input("City (type name)")
    with colc3:
        st.write("")
        if st.form_submit_button("➕ Add city", use_container_width=True):
            _add_city_entry(selected_country_for_city, city_to_add)

    if st.session_state.city_list:
        st.caption("Cities added:")
        for item in st.session_state.city_list:
            st.write(f"- {item}")
        if st.checkbox("Clear all cities"):
            st.session_state.city_list = []

    new_years = st.text_input("Years (e.g. 2022–2024)")
    new_status = st.selectbox("Status", ["Active", "Legacy", "Completed", "Planning"])
    new_types = st.text_area("Data types (Spatial? Quantitative? Qualitative?)")
    new_desc = st.text_area("Short description")
    new_contact = st.text_input("Contact / Responsible institution")
    new_access = st.text_input("Access / License / Ethics")
    new_url = st.text_input("Project URL (optional)")

    submitted = st.form_submit_button("Submit for review")

if submitted:
    if not new_name.strip():
        st.warning("Please provide a Project name.")
    elif not st.session_state.city_list:
        st.warning("Please add at least one (country–city) pair.")
    else:
        total_rows, ok_all, msg_any = 0, True, None

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

        if ok_all:
            st.success(f"✅ Submission saved ({total_rows} row(s) added).")
            st.session_state.city_list = []
        else:
            st.warning(f"⚠️ Some rows failed: {msg_any}")

        ok_mail, msg_mail = try_send_email_via_emailjs({
            "project_name": new_name,
            "entries": ", ".join(st.session_state.city_list),
            "status": new_status,
            "years": new_years,
            "url": new_url,
        })
        if ok_mail:
            st.info("📨 Notification email sent.")
        else:
            st.caption(msg_mail)

        st.markdown("**Submission preview:**")
        st.code({
            "project_name": new_name,
            "entries": st.session_state.city_list,
            "years": new_years,
            "status": new_status,
        }, language="python")
