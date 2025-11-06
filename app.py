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

# OBS: Mantemos output_email por retrocompatibilidade, mas o app usará output_linkedin
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
    "output_email",            # legado (preenchido como "")
    "output_linkedin",         # novo campo solicitado
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
        ws = ss.add_worksheet(title=ws_name, rows=2000, cols=ncols)
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
# 4) Países (CSV local) – usado para prepopulação caso novo projeto
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
<p>The IDEAMAPS Network brings together diverse “slum” mapping traditions to co-produce
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
#    (continuamos lendo projetos apenas para pré-popular nomes/infos no envio)
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
# 7) SOMENTE TABELA DE OUTPUTS (prévia + detalhes “See full information”)
# ──────────────────────────────────────────────────────────────────────────────
st.subheader("Browse outputs (approved only)")
df_outputs, okO, msgO = load_outputs_public()
if not okO and msgO:
    st.caption(f"⚠️ {msgO}")
else:
    if df_outputs.empty:
        st.info("No outputs.")
    else:
        # Preview columns
        preview_cols = ["project","output_country","output_city","output_type","output_data_type"]
        for c in preview_cols:
            if c not in df_outputs.columns:
                df_outputs[c] = ""
        st.dataframe(
            df_outputs[preview_cols].reset_index(drop=True),
            use_container_width=True, hide_index=True
        )

        st.markdown("#### See full information")
        # Expanders por linha com todas as infos
        show_cols = [
            ("project","Project"),
            ("project_url","Project URL"),
            ("output_title","Output title"),
            ("output_type","Output type"),
            ("output_data_type","Output data type"),
            ("output_url","Output URL"),
            ("output_country","Output country"),
            ("output_city","Output city"),
            ("output_year","Output year"),
            ("output_desc","Description"),
            ("output_contact","Contact"),
            ("output_linkedin","LinkedIn"),
        ]
        for idx, row in df_outputs.reset_index(drop=True).iterrows():
            label = f"{row.get('project','(no project)')} — {row.get('output_title','(no title)')}"
            with st.expander(f"See full information: {label}"):
                lines = []
                for key, nice in show_cols:
                    val = str(row.get(key,"")).strip()
                    if key in ("project_url","output_url") and val:
                        val = f"[{val}]({val})"
                    lines.append(f"- **{nice}:** {val if val else '—'}")
                st.markdown("\n".join(lines))

# ──────────────────────────────────────────────────────────────────────────────
# 8) SUBMISSÃO → agora SOMENTE OUTPUTS (fila: approved=FALSE)
#    - Se projeto já existir (taxonomy), usamos
#    - Se "Other: ______", solicitar detalhes do projeto (como antes)
# ──────────────────────────────────────────────────────────────────────────────
st.markdown("---")
st.header("Submit Output (goes to review queue)")

if "city_list" not in st.session_state:
    st.session_state.city_list = []

with st.form("OUTPUT_FORM", clear_on_submit=False):
    submitter_email = st.text_input("Submitter email (required for review)", placeholder="name@org.org")

    # Escolha do projeto (taxonomia)
    project_tax_sel = st.selectbox("Project Name (taxonomy)", options=PROJECT_TAXONOMY)
    project_tax_other = ""
    is_other_project = project_tax_sel.startswith("Other")
    if is_other_project:
        project_tax_other = st.text_input("Please specify the project (taxonomy)")

    # Se projeto não está na taxonomia → solicitar detalhes (como antes)
    new_project_url = ""
    new_project_contact = ""
    countries_sel = []
    if is_other_project:
        st.markdown("**New project details (required if not in taxonomy)**")
        # Países + cidades (mini UI igual à de antes)
        countries_sel = st.multiselect("Implementation countries (one or more)", COUNTRY_NAMES)
        colc1, colc2, colc3 = st.columns([2,2,1])
        with colc1:
            selected_country = st.selectbox(
                "Select implementation country for the city",
                options=[SELECT_PLACEHOLDER] + countries_sel if countries_sel else [SELECT_PLACEHOLDER],
                index=0, disabled=not bool(countries_sel)
            )
        with colc2:
            city_input = st.text_input("City (accepts multiple, separated by commas)", key="city_add_proj")
        with colc3:
            st.write("")
            if st.form_submit_button("➕ Add city"):
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
                        st.session_state.city_list.pop(i); st.rerun()
            if st.checkbox("Clear all cities"): st.session_state.city_list = []

        new_project_url = st.text_input("Project URL (optional)")
        new_project_contact = st.text_input("Project contact / institution (optional)")

    # Tipo de output
    output_type_sel = st.selectbox("Output Type", options=OUTPUT_TYPES)
    output_type_other = ""
    if output_type_sel.startswith("Other"):
        output_type_other = st.text_input("Please specify the output type")

    # Mostrar data type SOMENTE se Dataset (senão, pular)
    output_data_type = ""
    if output_type_sel == "Dataset":
        output_data_type = st.selectbox("Data type (for datasets)", options=DATASET_DTYPES)

    output_title = st.text_input("Output Name")
    output_url   = st.text_input("Output URL (optional)")

    countries_fixed = _countries_with_global_first(COUNTRY_NAMES) + ["Other: ______"]
    output_country = st.selectbox("Geographic coverage of output", options=countries_fixed)
    output_country_other = ""
    if output_country.startswith("Other"):
        output_country_other = st.text_input("Please specify the geographic coverage")

    output_city = st.text_input("", placeholder="City (optional — follows formatting of 'Cities covered')")

    # REMOVIDO: "Add other years..." (conforme solicitado)
    base_years = list(range(2000, 2026))
    years_selected = st.multiselect("Year of output release", base_years)
    final_years_str = ",".join(str(y) for y in sorted(set(years_selected))) if years_selected else ""

    output_desc = st.text_area("Short description of output")
    output_contact = st.text_input("Name & institution of person responsible")
    output_linkedin = st.text_input("LinkedIn address of contact")  # substitui output_email
    project_url_for_output = st.text_input("Project URL (optional, if different)")

    submitted = st.form_submit_button("Submit for review (Output)")

    if submitted:
        # validações básicas
        if not submitter_email.strip():
            st.warning("Please provide the submitter email.")
        elif not output_title.strip():
            st.warning("Please provide the Output Name.")
        elif is_other_project and not (st.session_state.city_list or countries_sel):
            st.warning("For a new project (Other), please add at least one country/city.")
        else:
            # 1) Se for “Other”, opcionalmente registrar entradas de projeto na fila (interno)
            if is_other_project:
                wsP, errP = ws_projects()
                if errP or wsP is None:
                    st.error(errP or "Worksheet unavailable for projects.")
                else:
                    # cria linhas de projeto para cada par país-cidade (ou somente país)
                    pairs = st.session_state.city_list[:] if st.session_state.city_list else [f"{c} — " for c in countries_sel]
                    ok_allP, msg_anyP = True, None
                    for pair in pairs:
                        if "—" not in pair: continue
                        country, city = [p.strip() for p in pair.split("—",1)]
                        lat, lon = COUNTRY_CENTER_FULL.get(country, (None, None))
                        rowP = {
                            "country": country, "city": city, "lat": lat, "lon": lon,
                            "project_name": project_tax_other.strip(), "years": "",
                            "status": "", "data_types": "", "description": "",
                            "contact": new_project_contact, "access": "", "url": new_project_url,
                            "submitter_email": submitter_email,
                            "is_edit": "FALSE", "edit_target": "", "edit_request": "New project via output submission",
                            "approved": "FALSE",
                            "created_at": datetime.utcnow().isoformat(timespec="seconds")+"Z",
                        }
                        okP2, msgP2 = _append_row(wsP, PROJECTS_HEADERS, rowP)
                        ok_allP &= okP2; msg_anyP = msgP2
                    if not ok_allP:
                        st.error(f"⚠️ Project staging write error: {msg_anyP}")
                        st.stop()

            # 2) Gravar output na fila
            wsO, errO = ws_outputs()
            if errO or wsO is None:
                st.error(errO or "Worksheet unavailable for outputs.")
            else:
                rowO = {
                    "project": (project_tax_other.strip() if is_other_project else project_tax_sel),
                    "output_title": output_title,
                    "output_type": ("" if output_type_sel.startswith("Other") else output_type_sel),
                    "output_type_other": (output_type_other if output_type_sel.startswith("Other") else ""),
                    "output_data_type": (output_data_type if (output_type_sel=="Dataset") else ""),
                    "output_url": output_url,
                    "output_country": ("" if output_country.startswith("Other") else output_country),
                    "output_country_other": (output_country_other if output_country.startswith("Other") else ""),
                    "output_city": output_city,
                    "output_year": final_years_str,
                    "output_desc": output_desc,
                    "output_contact": output_contact,
                    "output_email": "",  # legado vazio
                    "output_linkedin": output_linkedin,
                    "project_url": (project_url_for_output or (new_project_url if is_other_project else "")),
                    "submitter_email": submitter_email,
                    "is_edit": "FALSE","edit_target":"","edit_request":"New submission",
                    "approved": "FALSE",
                    "created_at": datetime.utcnow().isoformat(timespec="seconds")+"Z",
                }
                okO2, msgO2 = _append_row(wsO, OUTPUTS_HEADERS, rowO)
                if okO2:
                    st.success("✅ Output submission queued (approved=FALSE).")
                    # limpar cidades temporárias para evitar duplicação
                    st.session_state.city_list = []
                else:
                    st.error(f"⚠️ {msgO2}")
