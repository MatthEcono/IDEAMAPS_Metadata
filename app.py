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

# ──────────────────────────────────────────────────────────────────────────────
# 0) PAGE CONFIG + LOGO
# ──────────────────────────────────────────────────────────────────────────────
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

# ──────────────────────────────────────────────────────────────────────────────
# 1) SHEETS: nomes das abas (podem ser sobrescritos via st.secrets)
# ──────────────────────────────────────────────────────────────────────────────
PROJECTS_MASTER = st.secrets.get("SHEETS_PROJECTS_MASTER", "projects_master")
OUTPUTS_MASTER  = st.secrets.get("SHEETS_OUTPUTS_MASTER",  "outputs_master")
PROJECTS_QUEUE  = st.secrets.get("SHEETS_PROJECTS_QUEUE",  "projects_queue")
OUTPUTS_QUEUE   = st.secrets.get("SHEETS_OUTPUTS_QUEUE",   "outputs_queue")
MESSAGES_SHEET  = st.secrets.get("SHEETS_MESSAGES",        "messages")

# cabeçalhos
PROJECTS_MASTER_HEADERS = [
    "project_id","project_taxonomy","project_name","country","city","lat","lon",
    "years","status","data_types","description","contact","access","url",
    "created_at","created_by","updated_at","updated_by","active","source_submission_id"
]
OUTPUTS_MASTER_HEADERS = [
    "output_id","project_id","linked_project_name","project_taxonomy",
    "output_title","output_type","output_type_other","output_data_type",
    "output_url","output_country","output_country_other","output_city",
    "output_year","output_desc","output_contact","output_email","project_url",
    "created_at","created_by","updated_at","updated_by","active","source_submission_id"
]
PROJECTS_QUEUE_HEADERS = [
    "submission_id","action",  # new | edit | delete
    "target_project_id",
    "project_taxonomy","project_taxonomy_other",
    "project_name","country","city","lat","lon","years","status","data_types",
    "description","contact","access","url",
    "submitter_email","edit_request",
    "status","reviewed_by","reviewed_at","review_notes",  # pending|approved|rejected
    "created_at"
]
OUTPUTS_QUEUE_HEADERS = [
    "submission_id","action",  # new | edit | delete
    "target_output_id",
    "linked_project_name","linked_project_id",           # se vier de master
    "project_taxonomy","project_taxonomy_other",
    "output_title","output_type","output_type_other","output_data_type",
    "output_url","output_country","output_country_other","output_city",
    "output_year","output_desc","output_contact","output_email","project_url",
    "submitter_email","edit_request",
    "status","reviewed_by","reviewed_at","review_notes",
    "created_at"
]
MESSAGES_HEADERS = ["message_id","name","email","message","approved","created_at"]

# listas fixas
PROJECT_TAXONOMY = [
    "IDEAMAPS Networking Grant","IDEAMAPSudan","SLUMAP","Data4HumanRights",
    "IDEAMAPS Data Ecosystem","Night Watch","ONEKANA","Space4All",
    "IDEAtlas","DEPRIMAP","URBE Latem","Other: ______"
]
OUTPUT_TYPES = ["Dataset","Code / App / Tool","Document","Academic Paper","Other: ________"]
DATASET_DTYPES = ["Spatial (eg shapefile)","Qualitative (eg audio recording)","Quantitative (eg survey results)"]
SELECT_PLACEHOLDER = "— Select —"

# ──────────────────────────────────────────────────────────────────────────────
# 2) GSpread helpers
# ──────────────────────────────────────────────────────────────────────────────
@st.cache_resource(show_spinner=False)
def _gs_client():
    try:
        creds_info = st.secrets.get("gcp_service_account")
        if not creds_info:
            return None, "Configure gcp_service_account em st.secrets."
        scopes = ["https://www.googleapis.com/auth/spreadsheets",
                  "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
        client = gspread.authorize(creds)
        return client, None
    except Exception as e:
        return None, f"Google Sheets auth error: {e}"

def _open_or_create(ws_name: str, headers: Optional[List[str]] = None):
    client, err = _gs_client()
    if err or client is None:
        return None, err or "Client unavailable."
    ss_id = st.secrets.get("SHEETS_SPREADSHEET_ID")
    if not ss_id:
        return None, "Defina SHEETS_SPREADSHEET_ID em st.secrets."
    try:
        ss = client.open_by_key(ss_id)
    except Exception as e:
        return None, f"Open spreadsheet error: {e}"
    try:
        return ss.worksheet(ws_name), None
    except gspread.exceptions.WorksheetNotFound:
        try:
            ncols = max(10, len(headers) if headers else 10)
            ws = ss.add_worksheet(title=ws_name, rows=500, cols=ncols)
            if headers:
                ws.update('A1', [headers])
            return ws, None
        except Exception as e:
            return None, f"Create worksheet '{ws_name}' error: {e}"

def _ws(name, headers):  # pega e garante header
    ws, err = _open_or_create(name, headers)
    if err or ws is None:
        return None, err or "Worksheet unavailable."
    try:
        header = ws.row_values(1) or []
        missing = [h for h in headers if h not in header]
        if missing:
            ws.update('A1', [header + missing])
        return ws, None
    except Exception as e:
        return None, f"Header check error: {e}"

def ws_projects_master(): return _ws(PROJECTS_MASTER, PROJECTS_MASTER_HEADERS)
def ws_outputs_master():  return _ws(OUTPUTS_MASTER,  OUTPUTS_MASTER_HEADERS)
def ws_projects_queue():  return _ws(PROJECTS_QUEUE,  PROJECTS_QUEUE_HEADERS)
def ws_outputs_queue():   return _ws(OUTPUTS_QUEUE,   OUTPUTS_QUEUE_HEADERS)
def ws_messages():        return _ws(MESSAGES_SHEET,  MESSAGES_HEADERS)

# ──────────────────────────────────────────────────────────────────────────────
# 3) Utils
# ──────────────────────────────────────────────────────────────────────────────
def try_email(template_params: dict) -> Tuple[bool, str]:
    svc = st.secrets.get("EMAILJS_SERVICE_ID")
    tpl = st.secrets.get("EMAILJS_TEMPLATE_ID")
    key = st.secrets.get("EMAILJS_PUBLIC_KEY")
    if not (svc and tpl and key):
        return False, "EmailJS not configured."
    try:
        r = requests.post(
            "https://api.emailjs.com/api/v1.0/email/send",
            json={"service_id": svc, "template_id": tpl, "user_id": key, "template_params": template_params},
            timeout=12,
        )
        return (True, "Email sent.") if r.status_code == 200 else (False, f"EmailJS {r.status_code}")
    except Exception as e:
        return False, f"Email error: {e}"

def _parse_number_loose(x):
    if x is None or (isinstance(x, float) and pd.isna(x)): return None
    s = str(x).strip().strip("'").strip('"')
    if not s: return None
    if ("," in s) or ("." in s):
        last = max(s.rfind(","), s.rfind("."))
        if last >= 0:
            intp = re.sub(r"[^\d\-\+]", "", s[:last]) or "0"
            frac = re.sub(r"\D", "", s[last+1:]) or "0"
            try: return float(f"{intp}.{frac}")
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

def _diff(before: dict, after: dict, cols: List[str]) -> str:
    out = []
    for c in cols:
        b = str(before.get(c,"")); a = str(after.get(c,""))
        if b != a:
            if len(b) > 120: b = b[:117]+"…"
            if len(a) > 120: a = a[:117]+"…"
            out.append(f"{c}: '{b}' → '{a}'")
    return "; ".join(out) if out else "No visible change"

# ──────────────────────────────────────────────────────────────────────────────
# 4) Países (CSV local) para coordenadas
# ──────────────────────────────────────────────────────────────────────────────
COUNTRY_CSV_PATH = APP_DIR / "country-coord.csv"

@st.cache_data(show_spinner=False)
def load_country_centers():
    df = pd.read_csv(COUNTRY_CSV_PATH, dtype=str, encoding="utf-8", on_bad_lines="skip")
    df.columns = [c.strip().lower() for c in df.columns]
    c_country = "country"; c_lat = "latitude (average)"; c_lon = "longitude (average)"
    if c_country not in df.columns or c_lat not in df.columns or c_lon not in df.columns:
        raise RuntimeError("CSV must contain: 'Country', 'Latitude (average)', 'Longitude (average)'.")
    df["lat"] = df[c_lat].apply(_parse_number_loose)
    df["lon"] = df[c_lon].apply(_parse_number_loose)
    df = df.dropna(subset=["lat", "lon"])
    mapping = {row[c_country]: (float(row["lat"]), float(row["lon"])) for _, row in df.iterrows()}
    return mapping, df

COUNTRY_CENTER_FULL, _df_countries = load_country_centers()
COUNTRY_NAMES = list(COUNTRY_CENTER_FULL.keys())
def _countries_with_global_first(names: List[str]):
    return (["Global"] + sorted([n for n in names if n != "Global"])) if "Global" in names else (["Global"] + sorted(names))

# ──────────────────────────────────────────────────────────────────────────────
# 5) Header + texto
# ──────────────────────────────────────────────────────────────────────────────
st.markdown("""
<div style="
  border:1px solid #334155; background:#0b1220; border-radius:14px;
  padding:16px; margin:4px 0 16px 0; color:#cbd5e1; line-height:1.55; font-size:0.95rem;">
<p>
The IDEAMAPS Network brings together diverse “slum” mapping traditions to co-produce
new ways of understanding and addressing urban inequalities…
</p>
<p>
This form gathers information on datasets, code, apps, training materials, community
profiles, policy briefs, academic papers, and other outputs from IDEAMAPS and related
projects…
</p>
<p><b>Call to Action:</b> Share your materials here.</p>
</div>
""", unsafe_allow_html=True)

if _logo_img is not None:
    st.sidebar.image(_logo_img, caption="IDEAMAPS", use_container_width=True)

# ──────────────────────────────────────────────────────────────────────────────
# 6) LOAD masters (leitura pública) + mapa + browse
# ──────────────────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def load_projects_master():
    ws, err = ws_projects_master()
    if err or ws is None: return pd.DataFrame(), False, err
    try:
        rows = ws.get_all_records()
        df = pd.DataFrame(rows)
        if df.empty: return df, True, None
        if "lat" in df.columns: df["lat"] = df["lat"].apply(_as_float)
        if "lon" in df.columns: df["lon"] = df["lon"].apply(_as_float)
        df = df[(df.get("active", True) == True) | (df.get("active","TRUE").astype(str).str.upper()=="TRUE")]
        return df, True, None
    except Exception as e:
        return pd.DataFrame(), False, f"Read error: {e}"

@st.cache_data(show_spinner=False)
def load_outputs_master():
    ws, err = ws_outputs_master()
    if err or ws is None: return pd.DataFrame(), False, err
    try:
        df = pd.DataFrame(ws.get_all_records())
        if df.empty: return df, True, None
        df = df[(df.get("active", True) == True) | (df.get("active","TRUE").astype(str).str.upper()=="TRUE")]
        return df, True, None
    except Exception as e:
        return pd.DataFrame(), False, f"Read error: {e}"

if st.sidebar.button("🔄 Check updates"):
    load_projects_master.clear(); load_outputs_master.clear(); load_country_centers.clear()
    st.rerun()

df_projects, okP, msgP = load_projects_master()
if not okP and msgP: st.caption(f"⚠️ {msgP}")

# mapa
if not df_projects.empty and df_projects[["lat","lon"]].dropna().shape[0] > 0:
    groups = df_projects.dropna(subset=["lat","lon"]).groupby(["country","lat","lon"], as_index=False)
    m = folium.Map(
        location=[df_projects["lat"].dropna().mean(), df_projects["lon"].dropna().mean()],
        zoom_start=2, tiles="CartoDB dark_matter"
    )
    for (country, lat, lon), g in groups:
        proj_dict = {}
        for _, r in g.iterrows():
            pname = str(r.get("project_name","")).strip() or "(unnamed)"
            city = str(r.get("city","")).strip()
            url  = _clean_url(r.get("url",""))
            proj_dict.setdefault(pname, {"cities": set(), "urls": set()})
            if city: proj_dict[pname]["cities"].add(city)
            if url:  proj_dict[pname]["urls"].add(url)
        lines = ["<div style='font-size:0.9rem; color:#0f172a;'>", f"<b>{country}</b>", "<ul style='padding-left:1rem; margin:0;'>"]
        for pname, info in proj_dict.items():
            cities_txt = ", ".join(sorted(info["cities"])) if info["cities"] else "—"
            url_html = ""
            if info["urls"]:
                u_any = sorted(info["urls"])[0]
                url_html = " — " + f"<a href='{u_any}' target='_blank' style='color:#2563eb;text-decoration:none;'>link</a>"
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
    st.info("No approved projects with lat/lon yet.")

st.markdown("---")
st.subheader("Browse existing projects (from master)")
if df_projects.empty:
    st.info("No data.")
else:
    show_cols = ["project_name","country","city","years","data_types","contact","access","url","lat","lon"]
    show_cols = [c for c in show_cols if c in df_projects.columns]
    st.dataframe(df_projects[show_cols].reset_index(drop=True), use_container_width=True, hide_index=True)

# outputs master
st.markdown("---")
st.subheader("Browse existing outputs (from master)")
df_outputs, okO, msgO = load_outputs_master()
if not okO and msgO: st.caption(f"⚠️ {msgO}")
else:
    if df_outputs.empty:
        st.info("No outputs.")
    else:
        cols = ["linked_project_name","output_title","output_type","output_data_type","output_country","output_city","output_year"]
        for c in cols:
            if c not in df_outputs.columns: df_outputs[c] = ""
        st.dataframe(df_outputs[cols].reset_index(drop=True), use_container_width=True, hide_index=True)

# ──────────────────────────────────────────────────────────────────────────────
# 7) Append to QUEUE helpers
# ──────────────────────────────────────────────────────────────────────────────
def _append_row(ws, headers, row_dict: dict) -> Tuple[bool, str]:
    try:
        header = ws.row_values(1) or headers
        values = [row_dict.get(col, "") for col in header]
        ws.append_row(values, value_input_option="RAW")
        return True, "Saved."
    except Exception as e:
        return False, f"Write error: {e}"

def queue_project(payload: dict) -> Tuple[bool, str]:
    ws, err = ws_projects_queue()
    if err or ws is None: return False, err
    return _append_row(ws, PROJECTS_QUEUE_HEADERS, payload)

def queue_output(payload: dict) -> Tuple[bool, str]:
    ws, err = ws_outputs_queue()
    if err or ws is None: return False, err
    return _append_row(ws, OUTPUTS_QUEUE_HEADERS, payload)

# ──────────────────────────────────────────────────────────────────────────────
# 8) Formulário ÚNICO (Project | Output) gravando nas filas
# ──────────────────────────────────────────────────────────────────────────────
st.markdown("---")
st.header("Add / Edit Entry (goes to review queue)")

entry_kind = st.radio("What would you like to add?", ["Project","Output"], index=0, horizontal=True, key="entry_kind_radio")

# estado comum
if "city_list" not in st.session_state:
    st.session_state.city_list = []

def _ulid_like():
    # id curto legível p/ submissions
    return datetime.utcnow().strftime("%Y%m%d%H%M%S%f")

with st.form("UNIFIED_FORM", clear_on_submit=False):
    submitter_email = st.text_input("Submitter email (required for review)", placeholder="name@org.org")

    # --------------------------- PROJECT --------------------------------------
    if entry_kind == "Project":
        st.markdown("**Project details**")
        countries_sel = st.multiselect("Implementation countries (one or more)", sorted(COUNTRY_NAMES))
        st.caption("Add at least one (country — city).")

        colc1, colc2, colc3 = st.columns([2,2,1])
        with colc1:
            selected_country = st.selectbox("Select implementation country for the city",
                                            options=[SELECT_PLACEHOLDER] + countries_sel if countries_sel else [SELECT_PLACEHOLDER],
                                            index=0, disabled=not bool(countries_sel))
        with colc2:
            city_input = st.text_input("City (accepts multiple, separated by commas)", key="city_add_proj")
        with colc3:
            st.write("")
            if st.form_submit_button("➕ Add"):
                if selected_country and selected_country != SELECT_PLACEHOLDER and city_input.strip():
                    for c in [x.strip() for x in city_input.split(",") if x.strip()]:
                        pair = f"{selected_country} — {c}"
                        if pair not in st.session_state.city_list: st.session_state.city_list.append(pair)
                    st.rerun()
                else:
                    st.warning("Select a valid country and type a city.")

        if st.session_state.city_list:
            st.caption("Cities added:")
            for i, it in enumerate(st.session_state.city_list):
                c1, c2 = st.columns([6,1])
                with c1: st.write(f"- {it}")
                with c2:
                    if st.form_submit_button("Remove", key=f"rm_city_{i}"):
                        st.session_state.city_list.pop(i); st.rerun()
            if st.checkbox("Clear all cities"): st.session_state.city_list = []

        project_name = st.text_input("Project name")
        years  = st.text_input("Years (e.g. 2022–2024)")
        data_types = st.text_area("Data types (Spatial? Quantitative? Qualitative?)")
        description = st.text_area("Short description")
        contact = st.text_input("Contact / Responsible institution")
        access  = st.text_input("Access / License / Ethics")
        url     = st.text_input("Project URL (optional)")

        # “Edit” via browse: se usuário clicou numa linha (fora deste form),
        # podemos guardar a linha original em session_state["_PROJ_before"] e acionar action="edit".
        editing = bool(st.session_state.get("_PROJ_before"))
        before  = st.session_state.get("_PROJ_before", {})

        submit_btn = st.form_submit_button("Submit for review (Project)")

        if submit_btn:
            if not submitter_email.strip(): st.warning("Please provide submitter email.")
            elif not project_name.strip(): st.warning("Please provide Project name.")
            elif not (st.session_state.city_list or countries_sel): st.warning("Please add countries/cities.")
            else:
                pairs = st.session_state.city_list[:] if st.session_state.city_list else [f"{c} — " for c in countries_sel]
                ok_all, msg_any = True, None
                for pair in pairs:
                    if "—" not in pair: continue
                    country, city = [p.strip() for p in pair.split("—",1)]
                    lat, lon = COUNTRY_CENTER_FULL.get(country, (None, None))
                    after = {
                        "project_taxonomy": "", "project_taxonomy_other": "",
                        "project_name": project_name, "country": country, "city": city,
                        "lat": lat, "lon": lon, "years": years, "status": "",
                        "data_types": data_types, "description": description, "contact": contact,
                        "access": access, "url": url, "submitter_email": submitter_email,
                    }
                    if editing:
                        payload = {
                            "submission_id": f"sub_{_ulid_like()}",
                            "action": "edit",
                            "target_project_id": before.get("project_id",""),
                            **after,
                            "edit_request": _diff(before, after, ["project_name","country","city","lat","lon","years","status","data_types","description","contact","access","url"]),
                            "status": "pending","reviewed_by":"","reviewed_at":"","review_notes":"",
                            "created_at": datetime.utcnow().isoformat(timespec="seconds")+"Z",
                        }
                    else:
                        payload = {
                            "submission_id": f"sub_{_ulid_like()}",
                            "action": "new","target_project_id": "",
                            **after,
                            "edit_request": "New project submission",
                            "status": "pending","reviewed_by":"","reviewed_at":"","review_notes":"",
                            "created_at": datetime.utcnow().isoformat(timespec="seconds")+"Z",
                        }
                    ok, msg = queue_project(payload); ok_all &= ok; msg_any = msg
                if ok_all:
                    st.success(f"✅ Project {'edit' if editing else 'submission'} queued.")
                    st.session_state.city_list = []; st.session_state["_PROJ_before"] = {}
                else:
                    st.error(f"⚠️ {msg_any}")

    # --------------------------- OUTPUT ---------------------------------------
    else:
        st.markdown("**Link to existing project or add a new one**")
        # nomes do master
        dfP, ok, _ = load_projects_master()
        existing_names = sorted(list({str(x).strip() for x in dfP["project_name"].dropna().tolist()})) if (ok and not dfP.empty) else []
        choice = st.radio("How do you want to link the output?",
                          ["Select existing project", "➕ Add new project (and link to it)"],
                          horizontal=True, key="out_link_choice")

        linked_project_name = ""
        new_proj = {}

        if choice == "Select existing project":
            linked_project_name = st.selectbox("Existing project (from catalogue)",
                                               options=[SELECT_PLACEHOLDER] + existing_names if existing_names else [SELECT_PLACEHOLDER],
                                               index=0, key="existing_project_select")
        else:
            # mini-form para criar projeto junto (entra na projects_queue)
            proj_country = st.selectbox("Project country (for location)",
                                        options=[SELECT_PLACEHOLDER] + sorted(COUNTRY_NAMES), index=0)
            proj_city = st.text_input("Project city (optional)")
            proj_name_free = st.text_input("Project name (free text)")
            proj_years = st.text_input("Years (e.g. 2022–2024)")
            proj_desc  = st.text_area("Short description (project)")
            proj_url   = st.text_input("Project URL (optional)")
            proj_contact = st.text_input("Contact / Responsible institution")
            proj_access  = st.text_input("Access / License / Ethics")
            lat, lon = COUNTRY_CENTER_FULL.get(proj_country, (None, None)) if proj_country and proj_country != SELECT_PLACEHOLDER else (None, None)

            linked_project_name = proj_name_free
            new_proj = {
                "submission_id": f"sub_{_ulid_like()}",
                "action": "new","target_project_id": "",
                "project_taxonomy":"", "project_taxonomy_other":"",
                "project_name": proj_name_free, "country": proj_country if proj_country != SELECT_PLACEHOLDER else "",
                "city": proj_city, "lat": lat, "lon": lon, "years": proj_years, "status": "",
                "data_types":"", "description": proj_desc, "contact": proj_contact, "access": proj_access, "url": proj_url,
                "submitter_email": submitter_email, "edit_request": "New project (created during output submission)",
                "status":"pending","reviewed_by":"","reviewed_at":"","review_notes":"",
                "created_at": datetime.utcnow().isoformat(timespec="seconds")+"Z",
            }

        st.markdown("---")
        st.markdown("**Output details**")

        # Taxonomia (fixa; edit travado se vier de edição)
        editing = bool(st.session_state.get("_OUT_before"))
        before  = st.session_state.get("_OUT_before", {})

        project_tax_sel = st.selectbox("Project Name (taxonomy)",
                                       options=PROJECT_TAXONOMY,
                                       index=PROJECT_TAXONOMY.index(st.session_state.get("out_taxonomy", PROJECT_TAXONOMY[0]))
                                       if st.session_state.get("out_taxonomy") in PROJECT_TAXONOMY else 0,
                                       disabled=editing, key="out_taxonomy")
        project_tax_other = ""
        if not editing and project_tax_sel.startswith("Other"):
            project_tax_other = st.text_input("Please specify the project (taxonomy)", key="out_taxonomy_other")

        output_type_sel = st.selectbox("Output Type",
                                       options=OUTPUT_TYPES,
                                       index=OUTPUT_TYPES.index(st.session_state.get("out_type", OUTPUT_TYPES[0]))
                                       if st.session_state.get("out_type") in OUTPUT_TYPES else 0,
                                       disabled=editing, key="out_type")
        output_type_other = ""
        if not editing and output_type_sel.startswith("Other"):
            output_type_other = st.text_input("Please specify the output type", key="out_type_other")

        output_data_type = ""
        if (editing and st.session_state.get("out_type")=="Dataset") or (not editing and output_type_sel=="Dataset"):
            output_data_type = st.selectbox("Data type",
                                            options=DATASET_DTYPES,
                                            index=DATASET_DTYPES.index(st.session_state.get("out_dtype", DATASET_DTYPES[0]))
                                            if st.session_state.get("out_dtype") in DATASET_DTYPES else 0,
                                            disabled=editing, key="out_dtype")

        output_title = st.text_input("Output Name", value=st.session_state.get("out_title",""), key="out_title")
        output_url   = st.text_input("Output URL (optional)", value=st.session_state.get("out_url",""), key="out_url")

        countries_fixed = _countries_with_global_first(COUNTRY_NAMES) + ["Other: ______"]
        output_country = st.selectbox("Geographic coverage of output",
                                      options=countries_fixed,
                                      index=countries_fixed.index(st.session_state.get("out_country", countries_fixed[0]))
                                      if st.session_state.get("out_country") in countries_fixed else 0,
                                      disabled=editing, key="out_country")
        output_country_other = ""
        if not editing and output_country.startswith("Other"):
            output_country_other = st.text_input("Please specify the geographic coverage", key="out_country_other")

        output_city = st.text_input("", placeholder="City (optional — follows formatting of 'Cities covered')",
                                    value=st.session_state.get("out_city",""), key="out_city")

        base_years = list(range(2000, 2026))
        years_selected = st.multiselect("Year of output release", base_years,
                                        default=st.session_state.get("out_years_multi", []),
                                        key="out_years_multi")
        extra_years_raw = st.text_input("Add other years (e.g., 1998, 2026)",
                                        value=st.session_state.get("out_years_extra",""), key="out_years_extra")
        extra_years = []
        if extra_years_raw.strip():
            for part in extra_years_raw.split(","):
                s = part.strip()
                if s.isdigit(): extra_years.append(int(s))
        final_years = sorted(set(years_selected + extra_years))
        final_years_str = ",".join(str(y) for y in final_years) if final_years else ""

        output_desc = st.text_area("Short description of output", value=st.session_state.get("out_desc",""), key="out_desc")
        output_contact = st.text_input("Name & institution of person responsible", value=st.session_state.get("out_contact",""), key="out_contact")
        output_email   = st.text_input("Email of person responsible", value=st.session_state.get("out_email",""), key="out_email")
        project_url_for_output = st.text_input("Project URL (optional)", value=st.session_state.get("out_proj_url",""), key="out_proj_url")

        submit_btn = st.form_submit_button("Submit for review (Output)")

        if submit_btn:
            if not submitter_email.strip():
                st.warning("Please provide the submitter email.")
            elif not output_title.strip():
                st.warning("Please provide the Output Name.")
            elif not linked_project_name.strip() or (choice == "Select existing project" and linked_project_name == SELECT_PLACEHOLDER):
                st.warning("Select or create a project to link this output.")
            else:
                # se escolheu criar projeto junto, primeiro registramos a submissão na fila de projetos
                if choice != "Select existing project":
                    okPq, msgPq = queue_project(new_proj)
                    if not okPq:
                        st.error(f"⚠️ Could not queue the new project: {msgPq}")
                        st.stop()

                proj_tax_val = st.session_state.get("out_taxonomy")
                proj_tax_val = (st.session_state.get("out_taxonomy_other","").strip()
                                if (not editing) and proj_tax_val.startswith("Other")
                                else proj_tax_val)
                out_type_val = st.session_state.get("out_type")
                out_type_other_val = st.session_state.get("out_type_other","") if ((not editing) and out_type_val.startswith("Other")) else ""
                out_country_val = st.session_state.get("out_country")
                out_country_other_val = st.session_state.get("out_country_other","") if ((not editing) and out_country_val.startswith("Other")) else ""

                after = {
                    "linked_project_name": linked_project_name, "linked_project_id": "",
                    "project_taxonomy": proj_tax_val, "project_taxonomy_other": "" if not proj_tax_val.startswith("Other") else proj_tax_val,
                    "output_title": output_title,
                    "output_type": (out_type_val if not out_type_val.startswith("Other") else ""),
                    "output_type_other": out_type_other_val if out_type_val.startswith("Other") else "",
                    "output_data_type": st.session_state.get("out_dtype","") if ((out_type_val=="Dataset") or (editing and st.session_state.get("out_type")=="Dataset")) else "",
                    "output_url": output_url,
                    "output_country": (out_country_val if not out_country_val.startswith("Other") else ""),
                    "output_country_other": out_country_other_val if out_country_val.startswith("Other") else "",
                    "output_city": output_city,
                    "output_year": final_years_str,
                    "output_desc": output_desc, "output_contact": output_contact, "output_email": output_email,
                    "project_url": project_url_for_output,
                    "submitter_email": submitter_email,
                }

                if editing:
                    payload = {
                        "submission_id": f"sub_{_ulid_like()}",
                        "action": "edit",
                        "target_output_id": before.get("output_id",""),
                        **after,
                        "edit_request": _diff(before, after, [
                            "linked_project_name","project_taxonomy","output_title","output_type","output_type_other",
                            "output_data_type","output_url","output_country","output_country_other","output_city",
                            "output_year","output_desc","output_contact","output_email","project_url"
                        ]),
                        "status":"pending","reviewed_by":"","reviewed_at":"","review_notes":"",
                        "created_at": datetime.utcnow().isoformat(timespec="seconds")+"Z",
                    }
                else:
                    payload = {
                        "submission_id": f"sub_{_ulid_like()}",
                        "action": "new","target_output_id": "",
                        **after,
                        "edit_request": "New output submission",
                        "status":"pending","reviewed_by":"","reviewed_at":"","review_notes":"",
                        "created_at": datetime.utcnow().isoformat(timespec="seconds")+"Z",
                    }

                okQ, msgQ = queue_output(payload)
                if okQ:
                    st.success("✅ Output submission queued.")
                    try_email({
                        "project_name": linked_project_name,
                        "entries": f"{(payload['output_country'] or payload['output_country_other'])} — {payload.get('output_city','')}",
                        "years": payload.get("output_year",""),
                        "url": payload.get("output_url",""),
                        "submitter_email": payload["submitter_email"],
                        "is_edit": "yes" if editing else "no",
                    })
                    st.session_state["_OUT_before"] = {}
                else:
                    st.error(f"⚠️ {msgQ}")

# ──────────────────────────────────────────────────────────────────────────────
# 9) COMMUNITY MESSAGE BOARD  →  messages
# ──────────────────────────────────────────────────────────────────────────────
st.markdown("---")
st.header("Community message board")

with st.form("message_form"):
    msg_name = st.text_input("Your full name", placeholder="First Last")
    msg_email = st.text_input("Email (optional)", placeholder="name@org.org")
    msg_text = st.text_area("Your message", placeholder="Write your note to the IDEAMAPS team / partners…")
    send_msg = st.form_submit_button("Send message for approval")

def queue_message(name: str, email: str, message: str) -> Tuple[bool, str]:
    ws, err = ws_messages()
    if err or ws is None: return False, err
    row = {
        "message_id": f"msg_{_ulid_like()}",
        "name": (name or "").strip(),
        "email": (email or "").strip(),
        "message": (message or "").strip(),
        "approved": "FALSE",
        "created_at": datetime.utcnow().isoformat(timespec="seconds")+"Z",
    }
    return _append_row(ws, MESSAGES_HEADERS, row)

if send_msg:
    if not msg_name.strip(): st.warning("Please provide your full name.")
    elif not msg_text.strip(): st.warning("Please write a message.")
    else:
        ok, msg = queue_message(msg_name, msg_email, msg_text)
        st.success("✅ Message sent for approval.") if ok else st.warning(f"⚠️ {msg}")

