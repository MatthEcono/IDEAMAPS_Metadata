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
# 1) SOMENTE 2 ABAS NO SHEETS
# ──────────────────────────────────────────────────────────────────────────────
PROJECTS_SHEET = st.secrets.get("SHEETS_PROJECTS", "projects")
OUTPUTS_SHEET  = st.secrets.get("SHEETS_OUTPUTS",  "outputs")

# Cabeçalhos (unificados: master + fila)
PROJECTS_HEADERS = [
    "country","city","lat","lon","project_name","years","status",
    "data_types","description","contact","access","url",
    "submitter_email",
    "is_edit","edit_target","edit_request",
    "approved","created_at"
]

OUTPUTS_HEADERS = [
    "project",                 # taxonomy/fixed list
    "output_title",
    "output_type","output_type_other",
    "output_data_type",        # somente se type = Dataset
    "output_url",
    "output_country","output_country_other",
    "output_city",
    "output_year",             # string com anos separados por vírgula
    "output_desc",
    "output_contact",
    "output_email",
    "project_url",
    "submitter_email",
    "is_edit","edit_target","edit_request",
    "approved","created_at"
]

# Listas fixas
PROJECT_TAXONOMY = [
    "IDEAMAPS Networking Grant","IDEAMAPSudan","SLUMAP","Data4HumanRights",
    "IDEAMAPS Data Ecosystem","Night Watch","ONEKANA","Space4All",
    "IDEAtlas","DEPRIMAP","URBE Latem","Other: ______"
]
OUTPUT_TYPES = ["Dataset","Code / App / Tool","Document","Academic Paper","Other: ________"]
DATASET_DTYPES = [
    "Spatial (eg shapefile)",
    "Qualitative (eg audio recording)",
    "Quantitative (eg survey results)"
]
SELECT_PLACEHOLDER = "— Select —"

# ──────────────────────────────────────────────────────────────────────────────
# 2) Google Sheets helpers
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
        ws = ss.worksheet(ws_name)
    except gspread.exceptions.WorksheetNotFound:
        ncols = max(10, len(headers) if headers else 10)
        ws = ss.add_worksheet(title=ws_name, rows=1000, cols=ncols)
        if headers:
            ws.update("A1", [headers])
    except Exception as e:
        return None, f"Worksheet error: {e}"
    # garante headers
    try:
        current = ws.row_values(1) or []
        missing = [h for h in (headers or []) if h not in current]
        if missing:
            ws.update("A1", [current + missing])
    except Exception:
        pass
    return ws, None

def ws_projects(): return _open_or_create(PROJECTS_SHEET, PROJECTS_HEADERS)
def ws_outputs():  return _open_or_create(OUTPUTS_SHEET,  OUTPUTS_HEADERS)

def _append_row(ws, headers, row_dict: dict) -> Tuple[bool, str]:
    try:
        header = ws.row_values(1) or headers
        values = [row_dict.get(col, "") for col in header]
        ws.append_row(values, value_input_option="RAW")
        return True, "Saved."
    except Exception as e:
        return False, f"Write error: {e}"

# ──────────────────────────────────────────────────────────────────────────────
# 3) Utils
# ──────────────────────────────────────────────────────────────────────────────
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

def _ulid_like():
    return datetime.utcnow().strftime("%Y%m%d%H%M%S%f")

# ──────────────────────────────────────────────────────────────────────────────
# 4) Países (CSV local)
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
COUNTRY_NAMES = sorted(COUNTRY_CENTER_FULL.keys())
def _countries_with_global_first(names: List[str]):
    return (["Global"] + [n for n in names if n != "Global"]) if "Global" in names else (["Global"] + names)

# ──────────────────────────────────────────────────────────────────────────────
# 5) Header + texto resumido
# ──────────────────────────────────────────────────────────────────────────────
st.markdown("""
<div style="border:1px solid #334155; background:#0b1220; border-radius:14px;
padding:16px; margin:4px 0 16px 0; color:#cbd5e1; line-height:1.55; font-size:0.95rem;">
<p>The IDEAMAPS Network brings together diverse "slum" mapping traditions to co-produce
new ways of understanding and addressing urban inequalities…</p>
<p>This form gathers information on datasets, code, apps, training materials, community
profiles, policy briefs, academic papers, and other outputs from IDEAMAPS and related
projects…</p>
<p><b>Call to Action:</b> Share your materials here.</p>
</div>
""", unsafe_allow_html=True)

if _logo_img is not None:
    st.sidebar.image(_logo_img, caption="IDEAMAPS", use_container_width=True)

# ──────────────────────────────────────────────────────────────────────────────
# 6) Carregar SOMENTE aprovados (approved=TRUE)
# ──────────────────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def load_projects_public():
    ws, err = ws_projects()
    if err or ws is None: return pd.DataFrame(), False, err
    try:
        df = pd.DataFrame(ws.get_all_records())
        if df.empty:
            return df, True, None
        for c in PROJECTS_HEADERS:
            if c not in df.columns:
                df[c] = ""
        df["approved"] = df["approved"].astype(str).str.upper().isin(["TRUE","1","YES"])
        df = df[df["approved"]].copy()
        if "lat" in df.columns: df["lat"] = df["lat"].apply(_as_float)
        if "lon" in df.columns: df["lon"] = df["lon"].apply(_as_float)
        return df, True, None
    except Exception as e:
        return pd.DataFrame(), False, f"Read error: {e}"

@st.cache_data(show_spinner=False)
def load_outputs_public():
    ws, err = ws_outputs()
    if err or ws is None: return pd.DataFrame(), False, err
    try:
        df = pd.DataFrame(ws.get_all_records())
        if df.empty:
            return df, True, None
        for c in OUTPUTS_HEADERS:
            if c not in df.columns:
                df[c] = ""
        df["approved"] = df["approved"].astype(str).str.upper().isin(["TRUE","1","YES"])
        df = df[df["approved"]].copy()
        return df, True, None
    except Exception as e:
        return pd.DataFrame(), False, f"Read error: {e}"

if st.sidebar.button("🔄 Check updates"):
    load_projects_public.clear(); load_outputs_public.clear(); load_country_centers.clear()
    st.rerun()

df_projects, okP, msgP = load_projects_public()
if not okP and msgP:
    st.caption(f"⚠️ {msgP}")

# ──────────────────────────────────────────────────────────────────────────────
# 7) Mapa + tabelas (apenas aprovados)
# ──────────────────────────────────────────────────────────────────────────────
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
st.subheader("Browse existing projects (approved only)")
if df_projects.empty:
    st.info("No data.")
else:
    colsP = ["project_name","country","city","years","data_types","contact","access","url","lat","lon"]
    colsP = [c for c in colsP if c in df_projects.columns]
    st.dataframe(df_projects[colsP].reset_index(drop=True), use_container_width=True, hide_index=True)

st.markdown("---")
st.subheader("Browse existing outputs (approved only)")
df_outputs, okO, msgO = load_outputs_public()
if not okO and msgO:
    st.caption(f"⚠️ {msgO}")
else:
    if df_outputs.empty:
        st.info("No outputs.")
    else:
        colsO = ["project","output_title","output_type","output_data_type","output_country","output_city","output_year"]
        for c in colsO:
            if c not in df_outputs.columns:
                df_outputs[c] = ""
        st.dataframe(df_outputs[colsO].reset_index(drop=True), use_container_width=True, hide_index=True)

# ──────────────────────────────────────────────────────────────────────────────
# 8) SUBMISSÃO → grava no MESMO sheet, approved=FALSE (fila)
# ──────────────────────────────────────────────────────────────────────────────

# Sistema de reset do formulário
if "_pending_form_reset" not in st.session_state:
    st.session_state._pending_form_reset = False

if "city_list" not in st.session_state:
    st.session_state.city_list = []

def _clear_form_state():
    """Limpa o estado do formulário após submissão"""
    st.session_state.city_list = []
    # Limpa outros estados específicos do formulário se necessário
    keys_to_clear = [
        "submitter_email", "selected_country", "city_add_proj", 
        "project_name", "years", "data_types", "description", 
        "contact", "access", "url"
    ]
    for key in keys_to_clear:
        if key in st.session_state:
            del st.session_state[key]

st.markdown("---")
st.header("Add / Edit Entry (goes to review queue)")
entry_kind = st.radio("What would you like to add?", ["Project","Output"], index=0, horizontal=True)

# Se há reset pendente, limpa o estado
if st.session_state._pending_form_reset:
    _clear_form_state()
    st.session_state._pending_form_reset = False

with st.form("UNIFIED_FORM", clear_on_submit=False):
    submitter_email = st.text_input(
        "Submitter email (required for review)", 
        placeholder="name@org.org",
        key="submitter_email"
    )

    # --------------------------- PROJECT ---------------------------
    if entry_kind == "Project":
        st.markdown("**Project details**")
        countries_sel = st.multiselect("Implementation countries (one or more)", COUNTRY_NAMES)
        st.caption("Add at least one (country — city).")

        colc1, colc2, colc3 = st.columns([2,2,1])
        with colc1:
            selected_country = st.selectbox(
                "Select implementation country for the city",
                options=[SELECT_PLACEHOLDER] + countries_sel if countries_sel else [SELECT_PLACEHOLDER],
                index=0, 
                disabled=not bool(countries_sel),
                key="selected_country"
            )
        with colc2:
            city_input = st.text_input(
                "City (accepts multiple, separated by commas)", 
                key="city_add_proj"
            )
        with colc3:
            st.write("")
            if st.form_submit_button("➕ Add"):
                if selected_country and selected_country != SELECT_PLACEHOLDER and city_input.strip():
                    for c in [x.strip() for x in city_input.split(",") if x.strip()]:
                        pair = f"{selected_country} — {c}"
                        if pair not in st.session_state.city_list:
                            st.session_state.city_list.append(pair)
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
                        st.session_state.city_list.pop(i)
                        st.rerun()
            if st.checkbox("Clear all cities"): 
                st.session_state.city_list = []
                st.rerun()

        project_name = st.text_input("Project name", key="project_name")
        years  = st.text_input("Years (e.g. 2022–2024)", key="years")
        data_types = st.text_area("Data types (Spatial? Quantitative? Qualitative?)", key="data_types")
        description = st.text_area("Short description", key="description")
        contact = st.text_input("Contact / Responsible institution", key="contact")
        access  = st.text_input("Access / License / Ethics", key="access")
        url     = st.text_input("Project URL (optional)", key="url")

        submitted = st.form_submit_button("Submit for review (Project)")

        if submitted:
            if not submitter_email.strip():
                st.warning("Please provide submitter email.")
            elif not project_name.strip():
                st.warning("Please provide Project name.")
            elif not (st.session_state.city_list or countries_sel):
                st.warning("Please add countries/cities.")
            else:
                pairs = st.session_state.city_list[:] if st.session_state.city_list else [f"{c} — " for c in countries_sel]
                ws, err = ws_projects()
                if err or ws is None:
                    st.error(err or "Worksheet unavailable.")
                else:
                    ok_all, msg_any = True, None
                    for pair in pairs:
                        if "—" not in pair: continue
                        country, city = [p.strip() for p in pair.split("—",1)]
                        lat, lon = COUNTRY_CENTER_FULL.get(country, (None, None))
                        row = {
                            "country": country, "city": city, "lat": lat, "lon": lon,
                            "project_name": project_name, "years": years, "status": "",
                            "data_types": data_types, "description": description,
                            "contact": contact, "access": access, "url": url,
                            "submitter_email": submitter_email,
                            "is_edit": "FALSE", "edit_target": "", "edit_request": "New submission",
                            "approved": "FALSE",
                            "created_at": datetime.utcnow().isoformat(timespec="seconds")+"Z",
                        }
                        ok, msg = _append_row(ws, PROJECTS_HEADERS, row)
                        ok_all &= ok; msg_any = msg
                    if ok_all:
                        st.success("✅ Project submission queued (approved=FALSE).")
                        st.session_state._pending_form_reset = True
                        st.rerun()
                    else:
                        st.error(f"⚠️ {msg_any}")

    # --------------------------- OUTPUT ---------------------------
    else:
        st.markdown("**Output details**")

        # Taxonomia (fixa) – quando for editar, desabilita
        editing = False  # Simplificado - removido sistema complexo de edição
        project_tax_sel = st.selectbox(
            "Project Name (taxonomy)",
            options=PROJECT_TAXONOMY,
            disabled=editing
        )
        project_tax_other = ""
        if not editing and project_tax_sel.startswith("Other"):
            project_tax_other = st.text_input("Please specify the project (taxonomy)")

        output_type_sel = st.selectbox(
            "Output Type",
            options=OUTPUT_TYPES,
            disabled=editing
        )
        output_type_other = ""
        if not editing and output_type_sel.startswith("Other"):
            output_type_other = st.text_input("Please specify the output type")

        # CORREÇÃO: Data type só aparece quando Output Type = Dataset
        output_data_type = ""
        if output_type_sel == "Dataset":
            output_data_type = st.selectbox(
                "Data type (for datasets) *", 
                options=[SELECT_PLACEHOLDER] + DATASET_DTYPES,
                disabled=editing
            )
        else:
            output_data_type = ""  # Fica vazio quando não é Dataset

        output_title = st.text_input("Output Name")
        output_url   = st.text_input("Output URL (optional)")

        countries_fixed = _countries_with_global_first(COUNTRY_NAMES) + ["Other: ______"]
        output_country = st.selectbox("Geographic coverage of output", options=countries_fixed, disabled=editing)
        output_country_other = ""
        if not editing and output_country.startswith("Other"):
            output_country_other = st.text_input("Please specify the geographic coverage")

        output_city = st.text_input("", placeholder="City (optional — follows formatting of 'Cities covered')")

        base_years = list(range(2000, 2026))
        years_selected = st.multiselect("Year of output release", base_years)
        extra_years_raw = st.text_input("Add other years (e.g., 1998, 2026)")
        extra_years = []
        if extra_years_raw.strip():
            for part in extra_years_raw.split(","):
                s = part.strip()
                if s.isdigit(): extra_years.append(int(s))
        final_years = sorted(set(years_selected + extra_years))
        final_years_str = ",".join(str(y) for y in final_years) if final_years else ""

        output_desc = st.text_area("Short description of output")
        output_contact = st.text_input("Name & institution of person responsible")
        output_email   = st.text_input("Email of person responsible")
        project_url_for_output = st.text_input("Project URL (optional)")

        submitted = st.form_submit_button("Submit for review (Output)")

        if submitted:
            if not submitter_email.strip():
                st.warning("Please provide the submitter email.")
            elif not output_title.strip():
                st.warning("Please provide the Output Name.")
            elif output_type_sel == "Dataset" and (not output_data_type or output_data_type == SELECT_PLACEHOLDER):
                st.warning("Please select a Data type for Dataset outputs.")
            else:
                ws, err = ws_outputs()
                if err or ws is None:
                    st.error(err or "Worksheet unavailable.")
                else:
                    # Garante que Data Type fica vazio se não for Dataset
                    final_output_data_type = output_data_type if output_type_sel == "Dataset" else ""
                    
                    row = {
                        "project": (project_tax_other.strip() if project_tax_sel.startswith("Other") else project_tax_sel),
                        "output_title": output_title,
                        "output_type": ("" if output_type_sel.startswith("Other") else output_type_sel),
                        "output_type_other": (output_type_other if output_type_sel.startswith("Other") else ""),
                        "output_data_type": final_output_data_type,
                        "output_url": output_url,
                        "output_country": ("" if output_country.startswith("Other") else output_country),
                        "output_country_other": (output_country_other if output_country.startswith("Other") else ""),
                        "output_city": output_city,
                        "output_year": final_years_str,
                        "output_desc": output_desc,
                        "output_contact": output_contact,
                        "output_email": output_email,
                        "project_url": project_url_for_output,
                        "submitter_email": submitter_email,
                        "is_edit": "FALSE","edit_target":"","edit_request":"New submission",
                        "approved": "FALSE",
                        "created_at": datetime.utcnow().isoformat(timespec="seconds")+"Z",
                    }
                    ok, msg = _append_row(ws, OUTPUTS_HEADERS, row)
                    if ok:
                        st.success("✅ Output submission queued (approved=FALSE).")
                        st.session_state._pending_form_reset = True
                        st.rerun()
                    else:
                        st.error(f"⚠️ {msg}")
