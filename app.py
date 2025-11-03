# app.py
import base64
from pathlib import Path
from typing import Optional, List, Tuple

import gspread
import pandas as pd
import re
import requests
import streamlit as st
from PIL import Image
from datetime import datetime
from google.oauth2.service_account import Credentials
import folium
from streamlit_folium import st_folium

# =============================================================================
# 0) PAGE CONFIG + LOGO
# =============================================================================
APP_DIR = Path(__file__).parent
LOGO_PATH = APP_DIR / "ideamaps.png"

_logo_img = None
_logo_b64 = None
if LOGO_PATH.exists():
    try:
        _logo_img = Image.open(LOGO_PATH)
        with open(LOGO_PATH, "rb") as f:
            _logo_b64 = base64.b64encode(f.read()).decode("utf-8")
    except Exception:
        _logo_img, _logo_b64 = None, None

st.set_page_config(
    page_title="IDEAMAPS Global Metadata Explorer",
    page_icon=_logo_img if _logo_img is not None else "🌍",
    layout="wide",
)

if _logo_b64:
    st.markdown(
        f"""
        <link rel="icon" href="data:image/png;base64,{_logo_b64}">
        <link rel="apple-touch-icon" href="data:image/png;base64,{_logo_b64}">
        """,
        unsafe_allow_html=True,
    )

# =============================================================================
# 1) CONSTANTES / SHEETS / EMAILJS
# =============================================================================
REQUIRED_HEADERS = [
    "country","city","lat","lon","project_name","years","status",
    "data_types","description","contact","access","url",
    "submitter_email","is_edit","edit_target","edit_request",
    "approved","created_at"
]

MESSAGES_SHEET_NAME = "Public Messages"
MESSAGE_HEADERS = ["name","email","message","approved","created_at"]

# OUTPUTS (planilha separada)
OUTPUTS_SHEET_NAME = st.secrets.get("SHEETS_OUTPUTS_WORKSHEET_NAME", "outputs")
OUTPUTS_HEADERS = [
    "project",                 # taxonomia (lista fixa + other)
    "linked_project_name",     # nome exatamente como no catálogo (para vincular)
    "output_title",
    "output_type",
    "output_type_other",       # free text quando Other
    "output_data_type",        # só quando Dataset
    "output_url",
    "output_country",
    "output_city",
    "output_year",
    "output_desc",
    "output_contact",
    "output_email",
    "project_url",
    "submitter_email",
    "is_edit","edit_target","edit_request",
    "approved","created_at",
]

PROJECT_TAXONOMY = [
    "IDEAMAPS Networking Grant",
    "IDEAMAPSudan",
    "SLUMAP",
    "Data4HumanRights",
    "IDEAMAPS Data Ecosystem",
    "Night Watch",
    "ONEKANA",
    "Space4All",
    "IDEAtlas",
    "DEPRIMAP",
    "URBE Latem",
    "Other: ______",
]

OUTPUT_TYPES = [
    "Dataset",
    "Code / App / Tool",
    "Document",
    "Academic Paper",
    "Other: ________",
]

DATA_TYPES_FOR_DATASET = [
    "Spatial (eg shapefile)",
    "Qualitative (eg audio recording)",
    "Quantitative (eg survey results)",
]

SELECT_PLACEHOLDER = "— Select —"

# =============================================================================
# 2) Google Sheets helpers
# =============================================================================
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

def _ws_projects():
    ws_name = st.secrets.get("SHEETS_WORKSHEET_NAME") or "projects"
    return _open_or_create_worksheet(ws_name, REQUIRED_HEADERS)

def _ws_messages():
    return _open_or_create_worksheet(MESSAGES_SHEET_NAME, MESSAGE_HEADERS)

def _ws_outputs():
    return _open_or_create_worksheet(OUTPUTS_SHEET_NAME, OUTPUTS_HEADERS)

# =============================================================================
# 3) Utils
# =============================================================================
def try_send_email_via_emailjs(template_params: dict) -> Tuple[bool, str]:
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
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    s = str(x).strip().strip("'").strip('"')
    if not s:
        return None
    if ("," in s) or ("." in s):
        last_comma = s.rfind(","); last_dot = s.rfind(".")
        last_sep = max(last_comma, last_dot)
        if last_sep >= 0:
            int_part  = re.sub(r"[^\d\-\+]", "", s[:last_sep]) or "0"
            frac_part = re.sub(r"\D", "", s[last_sep+1:]) or "0"
            try: return float(f"{int_part}.{frac_part}")
            except Exception: pass
    raw = re.sub(r"[^\d\-\+]", "", s)
    try: return float(raw)
    except Exception:
        try: return float(s.replace(",", "."))
        except Exception: return None

def _as_float(x):
    v = _parse_number_loose(x)
    return float(v) if v is not None else None

def _clean_url(u):
    s = (u or "").strip()
    return s if (s.startswith("http://") or s.startswith("https://")) else s

def _countries_with_global_first(names: List[str]) -> List[str]:
    if "Global" in names:
        return ["Global"] + sorted([n for n in names if n != "Global"])
    return ["Global"] + sorted(names)

# =============================================================================
# 4) Países (CSV local)
# =============================================================================
COUNTRY_CSV_PATH = APP_DIR / "country-coord.csv"

@st.cache_data(show_spinner=False)
def load_country_centers():
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
COUNTRY_NAMES = list(COUNTRY_CENTER_FULL.keys())

# =============================================================================
# 5) Header + Background
# =============================================================================
header_html = f"""
<div style="
  display:flex; align-items:center; gap:16px;
  background: linear-gradient(90deg,#0f172a,#1e293b,#0f172a);
  border:1px solid #334155; border-radius:14px;
  padding:16px; margin-bottom:16px;">
  <div style="
    width:56px; height:56px; border-radius:12px; overflow:hidden;
    background:#0b1220; display:flex; align-items:center; justify-content:center;
    flex:0 0 auto; border:1px solid #334155;">
    {("<img src='data:image/png;base64," + _logo_b64 + "' style='width:100%;height:100%;object-fit:cover;'/>") if _logo_b64 else "🌍"}
  </div>
  <div style="display:flex; flex-direction:column;">
    <div style="color:#fff; font-weight:700; font-size:1.25rem; line-height:1.2;">
      IDEAMAPS Global Metadata Explorer
    </div>
    <div style="color:#cbd5e1; font-size:0.95rem;">
      Living catalogue of projects and outputs from the IDEAMAPS Network.
    </div>
  </div>
</div>
"""
st.markdown(header_html, unsafe_allow_html=True)

st.markdown("""
<div style="
  border:1px solid #334155; background:#0b1220; border-radius:14px;
  padding:16px; margin:4px 0 16px 0; color:#cbd5e1; line-height:1.55; font-size:0.95rem;">
<p>
The IDEAMAPS Network brings together diverse “slum” mapping traditions to co-produce
new ways of understanding and addressing urban inequalities. Network projects connect
data scientists, communities, local governments, and other stakeholders through
feedback loops that produce routine, accurate, and comparable citywide maps of area
deprivations and assets. These outputs support upgrading, advocacy, monitoring, and
other efforts to improve urban conditions.
</p>
<p>
This form gathers information on datasets, code, apps, training materials, community
profiles, policy briefs, academic papers, and other outputs from IDEAMAPS and related
projects. The resulting inventory will help members identify existing resources,
strengthen collaboration, and develop new analyses and initiatives that build on the
Network’s collective work.
</p>
<p>
<b>Call to Action:</b> If you or your team have produced relevant data, tools, or materials,
please share them here. Your contributions will expand the Network’s shared evidence
base and create new opportunities for collaboration.
</p>
</div>
""", unsafe_allow_html=True)

if _logo_img is not None:
    st.sidebar.image(_logo_img, caption="IDEAMAPS", use_container_width=True)

# =============================================================================
# 6) Projects: load + map + browse
# =============================================================================
def ensure_project_headers():
    ws, err = _ws_projects()
    if err or ws is None:
        return False, err or "Worksheet unavailable."
    try:
        header = ws.row_values(1)
        header = [h.strip() for h in header] if header else []
        missing = [h for h in REQUIRED_HEADERS if h not in header]
        if missing:
            new_header = header + missing
            ws.update('A1', [new_header])
        return True, "Projects header OK."
    except Exception as e:
        return False, f"Failed to adjust projects header: {e}"

def ensure_outputs_headers():
    ws, err = _ws_outputs()
    if err or ws is None:
        return False, err or "Outputs worksheet unavailable."
    try:
        header = ws.row_values(1)
        header = [h.strip() for h in header] if header else []
        missing = [h for h in OUTPUTS_HEADERS if h not in header]
        if missing:
            ws.update('A1', [header + missing])
        return True, "Outputs header OK."
    except Exception as e:
        return False, f"Failed to adjust outputs header: {e}"

def ensure_lat_lon_text_columns():
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
        def _col_letter(idx0: int) -> str:
            n = idx0 + 1; s = ""
            while n:
                n, r = divmod(n - 1, 26)
                s = chr(r + 65) + s
            return s
        lat_col = _col_letter(lat_idx)
        lon_col = _col_letter(lon_idx)
        ws.format(f"{lat_col}:{lat_col}", {"numberFormat": {"type": "TEXT"}})
        ws.format(f"{lon_col}:{lon_col}", {"numberFormat": {"type": "TEXT"}})
        return True, "lat/lon columns set to TEXT."
    except Exception as e:
        return False, f"Failed to format lat/lon columns: {e}"

@st.cache_data(show_spinner=False)
def load_approved_projects():
    ws, err = _ws_projects()
    if err or ws is None:
        return pd.DataFrame(), False, err
    try:
        rows = ws.get_all_records()
        df = pd.DataFrame(rows)
        if df.empty:
            return pd.DataFrame(), False, "Projects sheet is empty."
        for c in REQUIRED_HEADERS:
            if c not in df.columns: df[c] = ""
        df = df[df["approved"].astype(str).str.upper().eq("TRUE")].copy()
        if "lat" in df.columns: df["lat"] = df["lat"].apply(_as_float)
        if "lon" in df.columns: df["lon"] = df["lon"].apply(_as_float)
        return df, True, None
    except Exception as e:
        return pd.DataFrame(), False, f"Error reading projects: {e}"

if st.sidebar.button("🔄 Check updates"):
    load_approved_projects.clear()
    load_country_centers.clear()
    ensure_project_headers()
    ensure_outputs_headers()
    ensure_lat_lon_text_columns()
    st.session_state["_last_refresh"] = datetime.utcnow().isoformat()
    st.rerun()

df_projects, ok_proj, debug_msg = load_approved_projects()
if not ok_proj and debug_msg:
    st.caption(f"⚠️ {debug_msg}")

# Map
if not df_projects.empty and df_projects[["lat","lon"]].dropna().shape[0] > 0:
    groups = df_projects.dropna(subset=["lat","lon"]).groupby(["country","lat","lon"], as_index=False)
    m = folium.Map(
        location=[df_projects["lat"].dropna().mean(), df_projects["lon"].dropna().mean()],
        zoom_start=2, tiles="CartoDB dark_matter"
    )
    for (country, lat, lon), g in groups:
        proj_dict = {}
        for _, r in g.iterrows():
            pname = str(r.get("project_name", "")).strip() or "(unnamed project)"
            city = str(r.get("city", "")).strip()
            url = _clean_url(r.get("url", ""))
            proj_dict.setdefault(pname, {"cities": set(), "urls": set()})
            if city: proj_dict[pname]["cities"].add(city)
            if url:  proj_dict[pname]["urls"].add(url)
        lines = ["<div style='font-size:0.9rem; color:#0f172a;'>", f"<b>{country}</b>", "<ul style='padding-left:1rem; margin:0;'>"]
        for pname, info in proj_dict.items():
            cities_txt = ", ".join(sorted(info["cities"])) if info["cities"] else "—"
            url_html = ""
            if info["urls"]:
                u_any = sorted(info["urls"])[0]
                url_html = " — " + f"<a href='{u_any}' target='_blank' style='color:#2563eb; text-decoration:none;'>link</a>"
            lines.append(f"<li><b>{pname}</b> — {cities_txt}{url_html}</li>")
        lines.append("</ul></div>")
        html_block = "".join(lines)
        folium.CircleMarker(
            location=[lat, lon], radius=6, color="#38bdf8", fill=True, fill_opacity=0.9,
            tooltip=folium.Tooltip(html_block, sticky=True, direction='top',
                                   style="background:#ffffff; color:#0f172a; border:1px solid #cbd5e1; border-radius:8px; padding:8px;"),
            popup=folium.Popup(html_block, max_width=380),
        ).add_to(m)
    st_folium(m, height=520, width=None)
else:
    st.info("No approved projects with valid lat/lon to map yet.")

st.markdown("---")
st.subheader("Browse existing projects")
if df_projects.empty:
    st.info("No data to display yet.")
else:
    show_cols = ["project_name","country","city","years","data_types","contact","access","url","lat","lon"]
    show_cols = [c for c in show_cols if c in df_projects.columns]
    st.dataframe(df_projects[show_cols].reset_index(drop=True), use_container_width=True, hide_index=True)

st.markdown("---")

# =============================================================================
# 7) APPEND helpers (projects/outputs)
# =============================================================================
def append_project_row(payload: dict) -> Tuple[bool, str]:
    ws, err = _ws_projects()
    if err or ws is None:
        return False, err
    try:
        ensure_project_headers(); ensure_lat_lon_text_columns()
        def _fmt_num_str(v):
            try: return f"{float(v):.6f}"
            except Exception: return ""
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
        return True, "Saved project to Google Sheets."
    except Exception as e:
        return False, f"Write error: {e}"

def append_output_row(payload: dict) -> Tuple[bool, str]:
    ws, err = _ws_outputs()
    if err or ws is None:
        return False, err
    try:
        ensure_outputs_headers()
        row = {
            "project": payload.get("project",""),
            "linked_project_name": payload.get("linked_project_name",""),
            "output_title": payload.get("output_title",""),
            "output_type": payload.get("output_type",""),
            "output_type_other": payload.get("output_type_other",""),
            "output_data_type": payload.get("output_data_type",""),
            "output_url": payload.get("output_url",""),
            "output_country": payload.get("output_country",""),
            "output_city": payload.get("output_city",""),
            "output_year": payload.get("output_year",""),
            "output_desc": payload.get("output_desc",""),
            "output_contact": payload.get("output_contact",""),
            "output_email": payload.get("output_email",""),
            "project_url": payload.get("project_url",""),
            "submitter_email": payload.get("submitter_email",""),
            "is_edit": "TRUE" if payload.get("is_edit") else "FALSE",
            "edit_target": payload.get("edit_target",""),
            "edit_request": payload.get("edit_request",""),
            "approved": "FALSE",
            "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
        header = ws.row_values(1)
        values = [row.get(col, "") for col in header] if header else list(row.values())
        ws.append_row(values, value_input_option="RAW")
        return True, "Saved output to Google Sheets."
    except Exception as e:
        return False, f"Write error: {e}"

# =============================================================================
# 8) FORMULÁRIO ÚNICO (PROJECT or OUTPUT)
# =============================================================================
st.header("Add / Edit Entry (goes to review queue)")

# cache para países no dropdown de Output
countries_first = _countries_with_global_first(COUNTRY_NAMES)

# 8.1 tipo de entrada (rádio = bolinhas)
entry_kind = st.radio(
    "What would you like to add?",
    options=["Project", "Output"],
    index=0,
    horizontal=True,
    key="entry_kind_radio",
)


# Estado limpo a cada submission
if "city_list" not in st.session_state:
    st.session_state.city_list = []

with st.form("UNIFIED_FORM", clear_on_submit=False):
    submitter_email = st.text_input("Submitter email (required for review)", placeholder="name@org.org")

    if entry_kind == "Project":
        # ---- Campos de Projeto (iguais aos antigos, mas resumidos) ----
        countries_sel = st.multiselect("Implementation countries (one or more)", sorted(COUNTRY_NAMES))
        st.markdown("**Cities covered**")
        colc1, colc2, colc3 = st.columns([2, 2, 1])
        with colc1:
            selected_country_for_city = st.selectbox(
                "Select implementation country for the city",
                options=[SELECT_PLACEHOLDER] + countries_sel if countries_sel else [SELECT_PLACEHOLDER],
                index=0,
                disabled=not bool(countries_sel),
                key="country_for_city_unified",
            )
        with colc2:
            city_to_add = st.text_input("City (accepts multiple, separated by commas)", key="city_to_add_unified")
        with colc3:
            st.write("")
            add_city = st.form_submit_button("➕ Add", use_container_width=True, disabled=not countries_sel)

        if add_city:
            if not selected_country_for_city or selected_country_for_city == SELECT_PLACEHOLDER:
                st.warning("Please select a valid country.")
            elif not (city_to_add or "").strip():
                st.warning("Please type a city.")
            else:
                for c in [c.strip() for c in city_to_add.split(",") if c.strip()]:
                    pair = f"{selected_country_for_city} — {c}"
                    if pair not in st.session_state.city_list:
                        st.session_state.city_list.append(pair)
                st.rerun()

        if st.session_state.city_list:
            st.caption("Cities added:")
            for i, it in enumerate(st.session_state.city_list):
                c1, c2 = st.columns([6,1])
                with c1: st.write(f"- {it}")
                with c2:
                    if st.form_submit_button("Remove", key=f"rm_city_{i}"):
                        st.session_state.city_list.pop(i)
                        st.rerun()
            if st.checkbox("Clear all cities"):
                st.session_state.city_list = []

        project_name = st.text_input("Project name", placeholder="e.g., IDEAMAPS Lagos / Urban Deprivation Mapping")
        years = st.text_input("Years (e.g. 2022–2024)")
        data_types = st.text_area("Data types (Spatial? Quantitative? Qualitative?)")
        description = st.text_area("Short description")
        contact = st.text_input("Contact / Responsible institution")
        access = st.text_input("Access / License / Ethics")
        url = st.text_input("Project URL (optional)")

        submit_btn = st.form_submit_button("Submit for review")

        if submit_btn:
            if not submitter_email.strip():
                st.warning("Please provide the submitter email.")
            elif not project_name.strip():
                st.warning("Please provide Project name.")
            elif not (st.session_state.city_list or countries_sel):
                st.warning("Please add at least one country/city.")
            else:
                rows = st.session_state.city_list[:] if st.session_state.city_list else [f"{c} — " for c in countries_sel]
                ok_all, msg_any = True, None
                for pair in rows:
                    if "—" not in pair: continue
                    country, city = [p.strip() for p in pair.split("—", 1)]
                    lat, lon = COUNTRY_CENTER_FULL.get(country, (None, None))
                    payload = {
                        "country": country, "city": city, "lat": lat, "lon": lon,
                        "project_name": project_name, "years": years, "status": "",
                        "data_types": data_types, "description": description,
                        "contact": contact, "access": access, "url": url,
                        "submitter_email": submitter_email, "is_edit": False,
                        "edit_target": "", "edit_request": "New project submission",
                    }
                    ok, msg = append_project_row(payload)
                    ok_all &= ok; msg_any = msg
                if ok_all:
                    st.success(f"✅ Submission saved ({len(rows)} row(s)).")
                    st.session_state.city_list = []
                else:
                    st.error(f"⚠️ Some rows failed: {msg_any}")

    else:  # Output
        # Primeiro, vínculo com projeto existente OU criar novo projeto
        st.markdown("**Link to existing project or add a new one**")
        existing_names = sorted(list({str(x).strip() for x in df_projects["project_name"].dropna().tolist()})) if not df_projects.empty else []
        choice = st.radio(
            "How do you want to link the output?",
            ["Select existing project", "➕ Add new project (and link to it)"],
            horizontal=True
        )

        linked_project_name = ""
        new_proj_payload = {}

        if choice == "Select existing project":
            linked_project_name = st.selectbox(
                "Existing project (from catalogue)",
                options=[SELECT_PLACEHOLDER] + existing_names if existing_names else [SELECT_PLACEHOLDER],
                index=0,
            )
        else:
            st.info("Fill the fields below to add the project this output belongs to:")
            # Campos mínimos do projeto novo
            proj_country = st.selectbox("Project country (for location)", options=[SELECT_PLACEHOLDER] + sorted(COUNTRY_NAMES), index=0)
            proj_city = st.text_input("Project city (optional)")
            proj_name_free = st.text_input("Project name (free text)", placeholder="e.g., IDEAMAPS Lagos / Urban Deprivation Mapping")
            proj_years = st.text_input("Years (e.g. 2022–2024)")
            proj_desc = st.text_area("Short description (project)")
            proj_url = st.text_input("Project URL (optional)")
            proj_contact = st.text_input("Contact / Responsible institution")
            proj_access = st.text_input("Access / License / Ethics")
            # lat/lon a partir do país
            lat, lon = COUNTRY_CENTER_FULL.get(proj_country, (None, None)) if proj_country and proj_country != SELECT_PLACEHOLDER else (None, None)
            linked_project_name = proj_name_free  # será usado como vínculo
            new_proj_payload = {
                "country": proj_country if proj_country != SELECT_PLACEHOLDER else "",
                "city": proj_city, "lat": lat, "lon": lon,
                "project_name": proj_name_free, "years": proj_years, "status": "",
                "data_types": "", "description": proj_desc, "contact": proj_contact,
                "access": proj_access, "url": proj_url, "submitter_email": submitter_email,
                "is_edit": False, "edit_target": "", "edit_request": "New project (created during output submission)",
            }

        st.markdown("---")
        st.markdown("**Output details**")

        # Taxonomia de projeto (lista fixa + Other)
        project_tax_sel = st.selectbox(
            "Project Name (taxonomy)",
            options=PROJECT_TAXONOMY,
            index=0
        )
        project_tax_other = ""
        if project_tax_sel.startswith("Other"):
            project_tax_other = st.text_input("Please specify the project (taxonomy)")

        output_title = st.text_input("Output Name")
        output_type_sel = st.selectbox("Output Type", options=OUTPUT_TYPES)
        output_type_other = ""
        if output_type_sel.startswith("Other"):
            output_type_other = st.text_input("Please specify the output type")

        output_data_type = ""
        if output_type_sel == "Dataset":
            output_data_type = st.selectbox("Data type", options=DATA_TYPES_FOR_DATASET)

        output_url = st.text_input("Output URL (optional)")
        output_country = st.selectbox("Geographic coverage of output", options=countries_first, index=0)
        output_city = st.text_input("",
            placeholder="City (optional — follows formatting of the current 'Cities covered' question)")
        output_year = st.text_input("Year of output release")
        output_desc = st.text_area("Short description of output")
        output_contact = st.text_input("Name & institution of person responsible")
        output_email = st.text_input("Email of person responsible")
        project_url_for_output = st.text_input("Project URL (optional)")

        submit_btn = st.form_submit_button("Submit for review")

        if submit_btn:
            # validações
            if not submitter_email.strip():
                st.warning("Please provide the submitter email.")
            elif not output_title.strip():
                st.warning("Please provide the Output Name.")
            elif not linked_project_name.strip():
                st.warning("Please select or create a project to link this output.")
            elif choice == "Select existing project" and linked_project_name == SELECT_PLACEHOLDER:
                st.warning("Please pick a valid project from the catalogue.")
            elif choice != "Select existing project" and not new_proj_payload.get("project_name","").strip():
                st.warning("Please provide the new project name.")
            else:
                # Se escolheu novo projeto: grava fila de revisão
                created_project_ok = True
                if choice != "Select existing project":
                    created_project_ok, msgp = append_project_row(new_proj_payload)
                    if not created_project_ok:
                        st.error(f"⚠️ Could not queue the new project: {msgp}")

                if created_project_ok:
                    # Monta payload de OUTPUT
                    payload_out = {
                        "project": (project_tax_other.strip() if project_tax_sel.startswith("Other") else project_tax_sel),
                        "linked_project_name": linked_project_name,
                        "output_title": output_title,
                        "output_type": output_type_sel if not output_type_sel.startswith("Other") else "",
                        "output_type_other": output_type_other if output_type_sel.startswith("Other") else "",
                        "output_data_type": output_data_type if output_type_sel == "Dataset" else "",
                        "output_url": output_url,
                        "output_country": output_country,
                        "output_city": output_city,
                        "output_year": output_year,
                        "output_desc": output_desc,
                        "output_contact": output_contact,
                        "output_email": output_email,
                        "project_url": project_url_for_output,
                        "submitter_email": submitter_email,
                        "is_edit": False, "edit_target": "", "edit_request": "New output submission",
                    }
                    ok_out, msg_out = append_output_row(payload_out)
                    if ok_out:
                        st.success("✅ Output submission saved to review queue.")
                        try_send_email_via_emailjs({
                            "project_name": payload_out["linked_project_name"],
                            "entries": f"{payload_out['output_country']} — {payload_out.get('output_city','')}",
                            "status": "",
                            "years": payload_out.get("output_year",""),
                            "url": payload_out.get("output_url",""),
                            "submitter_email": payload_out["submitter_email"],
                            "is_edit": "no",
                            "edit_target": "",
                            "edit_request": "",
                        })
                        st.markdown("**Submission preview:**")
                        st.code(payload_out, language="python")
                    else:
                        st.error(f"⚠️ Failed to save output: {msg_out}")

# =============================================================================
# 9) COMMUNITY MESSAGE BOARD (igual)
# =============================================================================
st.markdown("---")
st.header("Community message board")

def ensure_message_headers():
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

with st.form("message_form"):
    msg_name = st.text_input("Your full name", placeholder="First Last")
    msg_email = st.text_input("Email (optional)", placeholder="name@org.org")
    msg_text = st.text_area("Your message", placeholder="Write your note to the IDEAMAPS team / partners...")
    send_msg = st.form_submit_button("Send message for approval")

def append_message_to_sheet(name: str, email: str, message: str) -> Tuple[bool, str]:
    ws, err = _ws_messages()
    if err or ws is None:
        return False, err or "Messages worksheet unavailable."
    try:
        ensure_message_headers()
        row = {
            "name": name.strip(),
            "email": (email or "").strip(),
            "message": message.strip(),
            "approved": "FALSE",
            "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
        header = ws.row_values(1)
        if not header:
            ws.update('A1', [MESSAGE_HEADERS]); header = MESSAGE_HEADERS
        values = [row.get(col, "") for col in header]
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
        ok, msg = append_message_to_sheet(msg_name, msg_email, msg_text)
        st.success("✅ Message sent for approval.") if ok else st.warning(f"⚠️ {msg}")
