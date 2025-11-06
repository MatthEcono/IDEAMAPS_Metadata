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
# 1) SHEETS
# ──────────────────────────────────────────────────────────────────────────────
PROJECTS_SHEET = st.secrets.get("SHEETS_PROJECTS", "projects")
OUTPUTS_SHEET  = st.secrets.get("SHEETS_OUTPUTS",  "outputs")

PROJECTS_HEADERS = [
    "country","city","lat","lon","project_name","years","status",
    "data_types","description","contact","access","url",
    "submitter_email",
    "is_edit","edit_target","edit_request",
    "approved","created_at"
]

OUTPUTS_HEADERS = [
    "project",
    "output_title",
    "output_type","output_type_other",
    "output_data_type",
    "output_url",
    "output_country","output_country_other",
    "output_city",
    "output_year",
    "output_desc",
    "output_contact",
    "output_email",
    "output_linkedin",
    "project_url",
    "submitter_email",
    "is_edit","edit_target","edit_request",
    "approved","created_at",
    "lat","lon"
]

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
        ws = ss.add_worksheet(title=ws_name, rows=3000, cols=ncols)
        if headers:
            ws.update("A1", [headers])
    except Exception as e:
        return None, f"Worksheet error: {e}"
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

def _countries_with_global_first(names: List[str]):
    """Coloca 'Global' no início da lista de países"""
    if "Global" in names:
        return ["Global"] + [n for n in names if n != "Global"]
    else:
        return ["Global"] + names

# ──────────────────────────────────────────────────────────────────────────────
# 4) Países e Cidades (CSV local/remoto)
# ──────────────────────────────────────────────────────────────────────────────
COUNTRY_CSV_PATH = APP_DIR / "country-coord.csv"
CITIES_CSV_PATH = APP_DIR / "world_cities.csv"
WORLD_CITIES_URL = "https://raw.githubusercontent.com/joelacus/world-cities/master/world-cities.csv"

@st.cache_data(show_spinner=False)
def load_country_centers():
    try:
        df = pd.read_csv(COUNTRY_CSV_PATH, dtype=str, encoding="utf-8", on_bad_lines="skip")
        df.columns = [c.strip().lower() for c in df.columns]
        c_country = "country"; c_lat = "latitude (average)"; c_lon = "longitude (average)"
        if c_country not in df.columns or c_lat not in df.columns or c_lon not in df.columns:
            st.error("CSV must contain: 'Country', 'Latitude (average)', 'Longitude (average)'.")
            return {}, pd.DataFrame()
        df["lat"] = df[c_lat].apply(_parse_number_loose)
        df["lon"] = df[c_lon].apply(_parse_number_loose)
        df = df.dropna(subset=["lat", "lon"])
        mapping = {row[c_country]: (float(row["lat"]), float(row["lon"])) for _, row in df.iterrows()}
        return mapping, df
    except Exception as e:
        st.error(f"Error loading country centers: {e}")
        return {}, pd.DataFrame()

@st.cache_data(show_spinner=False)
def load_world_cities():
    """Carrega o arquivo de cidades do mundo com coordenadas - baixa do GitHub se necessário"""
    try:
        # Tenta carregar localmente primeiro
        if CITIES_CSV_PATH.exists():
            df = pd.read_csv(CITIES_CSV_PATH, dtype=str, encoding="utf-8", on_bad_lines="skip")
        else:
            # Se não existe localmente, baixa do GitHub
            try:
                df = pd.read_csv(WORLD_CITIES_URL, dtype=str, encoding="utf-8", on_bad_lines="skip")
                # Salva localmente para uso futuro
                CITIES_CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
                df.to_csv(CITIES_CSV_PATH, index=False, encoding="utf-8")
            except Exception as e:
                st.error(f"Error downloading cities data: {e}")
                return pd.DataFrame(columns=['country', 'city', 'lat', 'lon'])
        
        # Normaliza nomes das colunas
        df.columns = [c.strip().lower() for c in df.columns]
        
        # Mapeia possíveis nomes de colunas
        col_mapping = {
            'name': 'city',
            'country_name': 'country', 
            'lat': 'lat',
            'lng': 'lon',
            'latitude': 'lat',
            'longitude': 'lon',
            'country': 'country',
            'city': 'city'
        }
        
        # Renomeia colunas para padrão
        for old_col, new_col in col_mapping.items():
            if old_col in df.columns and new_col not in df.columns:
                df[new_col] = df[old_col]
        
        # Garante que temos as colunas necessárias
        required_cols = ['country', 'city', 'lat', 'lon']
        missing_cols = [col for col in required_cols if col not in df.columns]
        if missing_cols:
            st.error(f"CSV de cidades está faltando colunas: {missing_cols}")
            return pd.DataFrame(columns=['country', 'city', 'lat', 'lon'])
        
        # Converte coordenadas
        df["lat"] = df["lat"].apply(_parse_number_loose)
        df["lon"] = df["lon"].apply(_parse_number_loose)
        df = df.dropna(subset=["lat", "lon"])
        
        return df[['country', 'city', 'lat', 'lon']]
    except Exception as e:
        st.error(f"Erro ao carregar cidades: {e}")
        return pd.DataFrame(columns=['country', 'city', 'lat', 'lon'])

def find_city_coordinates(country_name, city_name):
    """Encontra coordenadas para uma cidade específica"""
    cities_df = load_world_cities()
    if cities_df.empty:
        return None, None
    
    try:
        match = cities_df[
            (cities_df['country'].str.strip().str.lower() == country_name.strip().lower()) &
            (cities_df['city'].str.strip().str.lower() == city_name.strip().lower())
        ]
        
        if not match.empty:
            return match.iloc[0]['lat'], match.iloc[0]['lon']
    except Exception as e:
        st.error(f"Error finding city coordinates: {e}")
    
    return None, None

# Carregar dados de países e cidades
COUNTRY_CENTER_FULL, _df_countries = load_country_centers()
WORLD_CITIES = load_world_cities()

# Garantir que COUNTRY_NAMES existe mesmo se houve erro no carregamento
if not COUNTRY_CENTER_FULL:
    COUNTRY_NAMES = []
else:
    COUNTRY_NAMES = sorted(COUNTRY_CENTER_FULL.keys())

# ──────────────────────────────────────────────────────────────────────────────
# 5) Header
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
# 6) Carregamento de dados
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
        df["lat"] = df["lat"].apply(_as_float)
        df["lon"] = df["lon"].apply(_as_float)
        
        def _fallback_coords(row):
            if pd.notna(row.get("lat")) and pd.notna(row.get("lon")):
                return row["lat"], row["lon"]
            ctry = str(row.get("output_country","")).strip()
            if ctry in COUNTRY_CENTER_FULL:
                return COUNTRY_CENTER_FULL[ctry]
            return None, None
        
        lats, lons = [], []
        for _, r in df.iterrows():
            la, lo = _fallback_coords(r)
            lats.append(la); lons.append(lo)
        df["lat"] = lats; df["lon"] = lons
        return df, True, None
    except Exception as e:
        return pd.DataFrame(), False, f"Read error: {e}"

if st.sidebar.button("🔄 Check updates"):
    load_projects_public.clear(); load_outputs_public.clear(); load_country_centers.clear(); load_world_cities.clear()
    st.rerun()

df_projects, okP, msgP = load_projects_public()
if not okP and msgP:
    st.caption(f"⚠️ {msgP}")

# ──────────────────────────────────────────────────────────────────────────────
# 7) Mapa de OUTPUTS aprovados
# ──────────────────────────────────────────────────────────────────────────────
st.subheader("Projects & outputs map (approved outputs)")
df_outputs_map, okOm, msgOm = load_outputs_public()
if not okOm and msgOm:
    st.caption(f"⚠️ {msgOm}")
else:
    has_coords = (not df_outputs_map.empty) and (df_outputs_map[["lat","lon"]].dropna().shape[0] > 0)
    if has_coords:
        dfc = df_outputs_map.dropna(subset=["lat","lon"]).copy()
        # Fallback para coordenadas padrão se não houver dados
        if dfc.empty:
            center_lat, center_lon = 0, 0
        else:
            center_lat, center_lon = dfc["lat"].mean(), dfc["lon"].mean()
        m = folium.Map(
            location=[center_lat, center_lon],
            zoom_start=2, tiles="CartoDB dark_matter"
        )
        groups = dfc.groupby(["output_country","lat","lon"], as_index=False)
        for (country, lat, lon), g in groups:
            proj_info = {}
            for _, r in g.iterrows():
                proj = (str(r.get("project","")).strip() or "(unnamed)")
                out_title = str(r.get("output_title","")).strip()
                out_url = _clean_url(r.get("output_url",""))
                proj_info.setdefault(proj, [])
                proj_info[proj].append((out_title, out_url))
            lines = ["<div style='font-size:0.9rem; color:#0f172a;'>",
                     f"<b>{country if country else '—'}</b>",
                     "<ul style='padding-left:1rem; margin:0;'>"]
            for proj, outs in proj_info.items():
                inner = []
                for (t, u) in outs:
                    if t:
                        if u:
                            inner.append(f"{t} (<a href='{u}' target='_blank' style='color:#2563eb;text-decoration:none;'>link</a>)")
                        else:
                            inner.append(t)
                inner_txt = "; ".join(inner) if inner else "—"
                lines.append(f"<li><b>{proj}</b> — {inner_txt}</li>")
            lines.append("</ul></div>")
            html_block = "".join(lines)
            folium.CircleMarker(
                location=[lat, lon], radius=6, color="#38bdf8", fill=True, fill_opacity=0.9,
                tooltip=folium.Tooltip(html_block, sticky=True, direction='top',
                                       style="background:#ffffff; color:#0f172a; border:1px solid #cbd5e1; border-radius:8px; padding:8px;"),
                popup=folium.Popup(html_block, max_width=420),
            ).add_to(m)
        st_folium(m, height=520, width=None)
    else:
        st.info("No approved outputs with location yet.")

# ──────────────────────────────────────────────────────────────────────────────
# 8) Tabela de outputs
# ──────────────────────────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("Browse outputs (approved only)")

ss = st.session_state
if "_selected_output_idx" not in ss:
    ss._selected_output_idx = None
if "_want_open_dialog" not in ss:
    ss._want_open_dialog = False
if "_outputs_editor_key_version" not in ss:
    ss._outputs_editor_key_version = 0

df_outputs, okO, msgO = load_outputs_public()
if not okO and msgO:
    st.caption(f"⚠️ {msgO}")
else:
    if df_outputs.empty:
        st.info("No outputs.")
    else:
        df_base = df_outputs.reset_index(drop=True).copy()
        preview_cols = ["project","output_country","output_city","output_type","output_data_type"]
        for c in preview_cols:
            if c not in df_base.columns:
                df_base[c] = ""

        df_preview = df_base[preview_cols].copy()
        details_col = "See full information"
        df_preview[details_col] = False

        editor_key = f"outputs_editor_{ss._outputs_editor_key_version}"
        edited = st.data_editor(
            df_preview,
            key=editor_key,
            use_container_width=True,
            hide_index=True,
            disabled=preview_cols,
            column_config={
                "project": st.column_config.TextColumn("project"),
                "output_country": st.column_config.TextColumn("output_country"),
                "output_city": st.column_config.TextColumn("output_city"),
                "output_type": st.column_config.TextColumn("output_type"),
                "output_data_type": st.column_config.TextColumn("output_data_type"),
                details_col: st.column_config.CheckboxColumn(
                    details_col,
                    help="Open details for this row"
                ),
            }
        )

        selected_idx_list = []
        if details_col in edited.columns:
            selected_idx_list = [i for i, v in enumerate(edited[details_col].tolist()) if bool(v)]

        if selected_idx_list and not ss._want_open_dialog:
            ss._selected_output_idx = int(selected_idx_list[0])
            ss._want_open_dialog = True
            ss._outputs_editor_key_version += 1
            st.rerun()

        def _render_full_info_md(row):
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
            lines = []
            for key, nice in show_cols:
                val = str(row.get(key,"")).strip()
                if key in ("project_url","output_url") and val:
                    val = f"[{val}]({val})"
                lines.append(f"- **{nice}:** {val if val else '—'}")
            st.markdown("\n".join(lines))

        def _open_details(row):
            try:
                @st.dialog("Full information")
                def _dialog(rdict):
                    _render_full_info_md(rdict)
                _dialog(row.to_dict())
            except Exception:
                with st.container(border=True):
                    st.markdown("### Full information")
                    _render_full_info_md(row)
                    st.button(
                        "Close",
                        key=f"close_inline_details_{_ulid_like()}",
                        on_click=lambda: ss.update(
                            {"_want_open_dialog": False, "_selected_output_idx": None}
                        )
                    )

        if ss._want_open_dialog:
            idx = ss._selected_output_idx
            if isinstance(idx, int) and (0 <= idx < len(df_base)):
                row = df_base.iloc[idx]
                _open_details(row)
            ss._want_open_dialog = False
            ss._selected_output_idx = None

# ──────────────────────────────────────────────────────────────────────────────
# 9) SUBMISSÃO DE OUTPUT - VERSÃO CORRIGIDA
# ──────────────────────────────────────────────────────────────────────────────

# Inicialização do estado da sessão
if "output_form_data" not in st.session_state:
    st.session_state.output_form_data = {
        "submitter_email": "",
        "project_tax_sel": PROJECT_TAXONOMY[0],
        "project_tax_other": "",
        "new_project_url": "",
        "new_project_contact": "",
        "new_project_countries": [],
        "selected_country_city": SELECT_PLACEHOLDER,
        "city_add_proj": "",
        "output_type_sel": OUTPUT_TYPES[0],
        "output_type_other": "",
        "output_data_type": SELECT_PLACEHOLDER,
        "output_title": "",
        "output_url": "",
        "output_countries": [],
        "output_country_other": "",
        "output_city_dummy": "",
        "years_selected": [],
        "output_desc": "",
        "output_contact": "",
        "output_linkedin": "",
        "project_url_for_output": "",
        "country_for_city": SELECT_PLACEHOLDER,
        "city_list_output": [],
        "city_coordinates": {},
        "countries_applied": False,
        "map_center": None,
        "map_zoom": 2
    }

if "form_submitted" not in st.session_state:
    st.session_state.form_submitted = False

if "show_success" not in st.session_state:
    st.session_state.show_success = False

def clear_output_form():
    """Limpa todos os dados do formulário"""
    st.session_state.output_form_data = {
        "submitter_email": "",
        "project_tax_sel": PROJECT_TAXONOMY[0],
        "project_tax_other": "",
        "new_project_url": "",
        "new_project_contact": "",
        "new_project_countries": [],
        "selected_country_city": SELECT_PLACEHOLDER,
        "city_add_proj": "",
        "output_type_sel": OUTPUT_TYPES[0],
        "output_type_other": "",
        "output_data_type": SELECT_PLACEHOLDER,
        "output_title": "",
        "output_url": "",
        "output_countries": [],
        "output_country_other": "",
        "output_city_dummy": "",
        "years_selected": [],
        "output_desc": "",
        "output_contact": "",
        "output_linkedin": "",
        "project_url_for_output": "",
        "country_for_city": SELECT_PLACEHOLDER,
        "city_list_output": [],
        "city_coordinates": {},
        "countries_applied": False,
        "map_center": None,
        "map_zoom": 2
    }
    st.session_state.form_submitted = False
    st.session_state.show_success = False

def apply_countries_selection():
    """Aplica a seleção de países"""
    if st.session_state.output_form_data["output_countries"]:
        st.session_state.output_form_data["countries_applied"] = True
    else:
        st.warning("Please select at least one country first")

def clear_countries_selection():
    """Limpa a seleção de países"""
    st.session_state.output_form_data["output_countries"] = []
    st.session_state.output_form_data["city_list_output"] = []
    st.session_state.output_form_data["city_coordinates"] = {}
    st.session_state.output_form_data["countries_applied"] = False

def add_city_to_new_project():
    """Adiciona cidade ao novo projeto"""
    form_data = st.session_state.output_form_data
    if (form_data["selected_country_city"] and 
        form_data["selected_country_city"] != SELECT_PLACEHOLDER and 
        form_data["city_add_proj"].strip()):
        
        for city in [x.strip() for x in form_data["city_add_proj"].split(",") if x.strip()]:
            pair = f"{form_data['selected_country_city']} — {city}"
            if pair not in form_data["city_list_output"]:
                form_data["city_list_output"].append(pair)
                lat, lon = find_city_coordinates(form_data["selected_country_city"], city)
                if lat and lon:
                    form_data["city_coordinates"][pair] = (lat, lon)
        form_data["city_add_proj"] = ""

def add_city_to_output():
    """Adiciona cidade ao output"""
    form_data = st.session_state.output_form_data
    is_global = "Global" in form_data["output_countries"]
    
    if (form_data["countries_applied"] and 
        not is_global and 
        form_data["country_for_city"] and 
        form_data["country_for_city"] != SELECT_PLACEHOLDER and 
        form_data["output_city_dummy"].strip()):
        
        for city in [x.strip() for x in form_data["output_city_dummy"].split(",") if x.strip()]:
            pair = f"{form_data['country_for_city']} — {city}"
            if pair not in form_data["city_list_output"]:
                form_data["city_list_output"].append(pair)
                lat, lon = find_city_coordinates(form_data["country_for_city"], city)
                if lat and lon:
                    form_data["city_coordinates"][pair] = (lat, lon)
        form_data["output_city_dummy"] = ""

def remove_city(city_index):
    """Remove uma cidade da lista"""
    form_data = st.session_state.output_form_data
    if 0 <= city_index < len(form_data["city_list_output"]):
        removed_city = form_data["city_list_output"].pop(city_index)
        if removed_city in form_data["city_coordinates"]:
            del form_data["city_coordinates"][removed_city]

st.markdown("---")
st.header("Submit Output (goes to review queue)")

# Mostrar mensagem de sucesso se aplicável
if st.session_state.show_success:
    st.success("✅ Output submission queued for review")
    if st.button("Submit another output"):
        clear_output_form()
        st.rerun()
    st.stop()

# Formulário principal
with st.form("output_form", clear_on_submit=False):
    form_data = st.session_state.output_form_data
    
    # Campos do formulário
    submitter_email = st.text_input(
        "Submitter email (required for review)",
        value=form_data["submitter_email"],
        key="submitter_email_input",
        placeholder="name@org.org"
    )
    
    project_tax_sel = st.selectbox(
        "Project Name (taxonomy)",
        options=PROJECT_TAXONOMY,
        index=PROJECT_TAXONOMY.index(form_data["project_tax_sel"]),
        key="project_tax_sel_input"
    )
    
    is_other_project = project_tax_sel.startswith("Other")
    project_tax_other = ""
    if is_other_project:
        project_tax_other = st.text_input(
            "Please specify the project (taxonomy)", 
            value=form_data["project_tax_other"],
            key="project_tax_other_input"
        )
    
    # Seção de novo projeto
    new_project_url = ""
    new_project_contact = ""
    if is_other_project:
        st.markdown("**New project details (required if not in taxonomy)**")
        
        countries_sel = st.multiselect(
            "Implementation countries (one or more)",
            COUNTRY_NAMES,
            default=form_data["new_project_countries"],
            key="new_project_countries_input"
        )
        
        colc1, colc2, colc3 = st.columns([2,2,1])
        with colc1:
            selected_country_city = st.selectbox(
                "Select implementation country for the city",
                options=[SELECT_PLACEHOLDER] + countries_sel if countries_sel else [SELECT_PLACEHOLDER],
                index=0,
                key="selected_country_city_input"
            )
        with colc2:
            city_input_proj = st.text_input(
                "City name (type manually)",
                value=form_data["city_add_proj"],
                key="city_add_proj_input",
                placeholder="Enter city name"
            )
        with colc3:
            st.write("")
            if st.form_submit_button("➕ Add city to NEW project", use_container_width=True):
                add_city_to_new_project()
                st.rerun()
        
        # Lista de cidades adicionadas
        if form_data["city_list_output"]:
            st.caption("Cities added to NEW project:")
            for i, city_pair in enumerate(form_data["city_list_output"]):
                col1, col2 = st.columns([6,1])
                with col1:
                    coords = form_data["city_coordinates"].get(city_pair, (None, None))
                    if coords[0] and coords[1]:
                        st.write(f"- {city_pair} (📍 {coords[0]:.4f}, {coords[1]:.4f})")
                    else:
                        st.write(f"- {city_pair} (⚠️ coordinates not found)")
                with col2:
                    if st.form_submit_button("Remove", key=f"rm_city_newproj_{i}"):
                        remove_city(i)
                        st.rerun()
        
        new_project_url = st.text_input(
            "Project URL (optional)", 
            value=form_data["new_project_url"],
            key="new_project_url_input"
        )
        new_project_contact = st.text_input(
            "Project contact / institution (optional)", 
            value=form_data["new_project_contact"],
            key="new_project_contact_input"
        )
    
    # Tipo de output
    output_type_sel = st.selectbox(
        "Output Type", 
        options=OUTPUT_TYPES,
        index=OUTPUT_TYPES.index(form_data["output_type_sel"]),
        key="output_type_sel_input"
    )
    
    output_data_type = SELECT_PLACEHOLDER
    if output_type_sel == "Dataset":
        current_index = 0
        if form_data["output_data_type"] in [SELECT_PLACEHOLDER] + DATASET_DTYPES:
            current_index = [SELECT_PLACEHOLDER] + DATASET_DTYPES.index(form_data["output_data_type"])
        output_data_type = st.selectbox(
            "Data type (for datasets) *", 
            options=[SELECT_PLACEHOLDER] + DATASET_DTYPES,
            index=current_index,
            key="output_data_type_input"
        )
    
    output_type_other = ""
    if output_type_sel.startswith("Other"):
        output_type_other = st.text_input(
            "Please specify the output type", 
            value=form_data["output_type_other"],
            key="output_type_other_input"
        )
    
    output_title = st.text_input(
        "Output Name *", 
        value=form_data["output_title"],
        key="output_title_input"
    )
    output_url = st.text_input(
        "Output URL (optional)", 
        value=form_data["output_url"],
        key="output_url_input"
    )
    
    # Cobertura geográfica
    st.markdown("**Geographic coverage of output**")
    countries_fixed = _countries_with_global_first(COUNTRY_NAMES) + ["Other: ______"]
    
    output_countries = st.multiselect(
        "Select one or more countries (select 'Global' for worldwide coverage) *",
        options=countries_fixed,
        default=form_data["output_countries"],
        key="output_countries_input"
    )
    
    # Botões para aplicar/limpar seleção de países
    col_apply1, col_apply2 = st.columns([3, 1])
    with col_apply1:
        if st.form_submit_button("✅ Apply countries selection", use_container_width=True):
            apply_countries_selection()
            st.rerun()
    
    with col_apply2:
        if st.form_submit_button("🔄 Clear selection", use_container_width=True):
            clear_countries_selection()
            st.rerun()
    
    # Status da aplicação
    if form_data["countries_applied"] and form_data["output_countries"]:
        st.success(f"✅ Countries applied: {', '.join(form_data['output_countries'])}")
    
    is_global = "Global" in form_data["output_countries"]
    if is_global:
        st.info("Global coverage selected - city fields will be disabled")
    
    output_country_other = ""
    if "Other: ______" in form_data["output_countries"]:
        output_country_other = st.text_input(
            "Please specify other geographic coverage", 
            value=form_data["output_country_other"],
            key="output_country_other_input"
        )
    
    # Cidades
    st.markdown("**Cities covered**")
    
    available_countries_for_cities = []
    if form_data["countries_applied"] and form_data["output_countries"]:
        available_countries_for_cities = [c for c in form_data["output_countries"] if c not in ["Global", "Other: ______"]]
    
    if not form_data["countries_applied"]:
        st.info("👆 Please select countries above and click 'Apply countries selection' to add cities")
    
    colx1, colx2, colx3 = st.columns([2,2,1])
    with colx1:
        country_for_city = st.selectbox(
            "Country for the city",
            options=[SELECT_PLACEHOLDER] + available_countries_for_cities,
            index=0,
            key="country_for_city_input",
            disabled=is_global or not available_countries_for_cities or not form_data["countries_applied"]
        )
    with colx2:
        city_input_out = st.text_input(
            "City name (type manually)",
            value=form_data["output_city_dummy"],
            key="output_city_dummy_input",
            placeholder="Enter city name",
            disabled=is_global or not form_data["countries_applied"]
        )
    with colx3:
        st.write("")
        add_city_disabled = (is_global or 
                           not form_data["countries_applied"] or 
                           not country_for_city or 
                           country_for_city == SELECT_PLACEHOLDER or 
                           not city_input_out.strip())
        
        if st.form_submit_button("➕ Add city to OUTPUT", disabled=add_city_disabled):
            add_city_to_output()
            st.rerun()
    
    # Lista de cidades do output
    if form_data["city_list_output"] and not is_global and form_data["countries_applied"]:
        st.caption("Cities added to OUTPUT:")
        for i, city_pair in enumerate(form_data["city_list_output"]):
            col1, col2 = st.columns([6,1])
            with col1:
                coords = form_data["city_coordinates"].get(city_pair, (None, None))
                if coords[0] and coords[1]:
                    st.write(f"- {city_pair} (📍 {coords[0]:.4f}, {coords[1]:.4f})")
                else:
                    st.write(f"- {city_pair} (⚠️ coordinates not found)")
            with col2:
                if st.form_submit_button("Remove", key=f"rm_city_out_{i}"):
                    remove_city(i)
                    st.rerun()
    
    # Mapa
    if (form_data["countries_applied"] and 
        not is_global and 
        available_countries_for_cities):
        
        if available_countries_for_cities:
            first_country = available_countries_for_cities[0]
            if first_country in COUNTRY_CENTER_FULL:
                form_data["map_center"] = COUNTRY_CENTER_FULL[first_country]
                form_data["map_zoom"] = 3
                
            if form_data["map_center"]:
                m = folium.Map(
                    location=form_data["map_center"],
                    zoom_start=form_data["map_zoom"],
                    tiles="CartoDB positron"
                )
                
                for country in available_countries_for_cities:
                    if country in COUNTRY_CENTER_FULL:
                        latlon = COUNTRY_CENTER_FULL[country]
                        folium.CircleMarker(
                            location=latlon, radius=8, color="#2563eb",
                            fill=True, fill_opacity=0.9, tooltip=f"{country}"
                        ).add_to(m)
                
                for pair in form_data["city_list_output"]:
                    if "—" in pair:
                        ctry, cty = [p.strip() for p in pair.split("—",1)]
                        coords = form_data["city_coordinates"].get(pair, None)
                        if coords and coords[0] is not None and coords[1] is not None:
                            folium.Marker(
                                location=coords, 
                                tooltip=f"{cty} ({ctry})",
                                icon=folium.Icon(color="green", icon="info-sign")
                            ).add_to(m)
                        else:
                            latlon = COUNTRY_CENTER_FULL.get(ctry)
                            if latlon:
                                folium.Marker(
                                    location=latlon, 
                                    tooltip=f"{cty} ({ctry})",
                                    icon=folium.Icon(color="orange", icon="info-sign")
                                ).add_to(m)
                st_folium(m, height=320, width=None)
    elif is_global and form_data["countries_applied"]:
        st.info("Map preview not available for global coverage")
    
    # Ano de lançamento
    current_year = datetime.utcnow().year
    base_years_desc = list(range(current_year, 1999, -1))
    years_selected = st.multiselect(
        "Year of output release", 
        base_years_desc,
        default=form_data["years_selected"],
        key="years_selected_input"
    )
    
    # Descrição e contato
    output_desc = st.text_area(
        "Short description of output", 
        value=form_data["output_desc"],
        key="output_desc_input"
    )
    output_contact = st.text_input(
        "Name & institution of person responsible", 
        value=form_data["output_contact"],
        key="output_contact_input"
    )
    output_linkedin = st.text_input(
        "LinkedIn address of contact", 
        value=form_data["output_linkedin"],
        key="output_linkedin_input"
    )
    project_url_for_output = st.text_input(
        "Project URL (optional, if different)", 
        value=form_data["project_url_for_output"],
        key="project_url_for_output_input"
    )
    
    # Botões finais
    col1, col2 = st.columns([1, 1])
    with col1:
        submitted = st.form_submit_button(
            "✅ Submit for review (Output)",
            use_container_width=True,
            type="primary"
        )
    with col2:
        if st.form_submit_button("🗑️ Clear Form", use_container_width=True, type="secondary"):
            clear_output_form()
            st.rerun()

# Atualizar session_state com os valores atuais dos campos
if 'submitter_email_input' in st.session_state:
    st.session_state.output_form_data["submitter_email"] = st.session_state.submitter_email_input
if 'project_tax_sel_input' in st.session_state:
    st.session_state.output_form_data["project_tax_sel"] = st.session_state.project_tax_sel_input
if 'project_tax_other_input' in st.session_state:
    st.session_state.output_form_data["project_tax_other"] = st.session_state.project_tax_other_input
if 'new_project_url_input' in st.session_state:
    st.session_state.output_form_data["new_project_url"] = st.session_state.new_project_url_input
if 'new_project_contact_input' in st.session_state:
    st.session_state.output_form_data["new_project_contact"] = st.session_state.new_project_contact_input
if 'new_project_countries_input' in st.session_state:
    st.session_state.output_form_data["new_project_countries"] = st.session_state.new_project_countries_input
if 'selected_country_city_input' in st.session_state:
    st.session_state.output_form_data["selected_country_city"] = st.session_state.selected_country_city_input
if 'city_add_proj_input' in st.session_state:
    st.session_state.output_form_data["city_add_proj"] = st.session_state.city_add_proj_input
if 'output_type_sel_input' in st.session_state:
    st.session_state.output_form_data["output_type_sel"] = st.session_state.output_type_sel_input
if 'output_type_other_input' in st.session_state:
    st.session_state.output_form_data["output_type_other"] = st.session_state.output_type_other_input
if 'output_data_type_input' in st.session_state:
    st.session_state.output_form_data["output_data_type"] = st.session_state.output_data_type_input
if 'output_title_input' in st.session_state:
    st.session_state.output_form_data["output_title"] = st.session_state.output_title_input
if 'output_url_input' in st.session_state:
    st.session_state.output_form_data["output_url"] = st.session_state.output_url_input
if 'output_countries_input' in st.session_state:
    st.session_state.output_form_data["output_countries"] = st.session_state.output_countries_input
if 'output_country_other_input' in st.session_state:
    st.session_state.output_form_data["output_country_other"] = st.session_state.output_country_other_input
if 'output_city_dummy_input' in st.session_state:
    st.session_state.output_form_data["output_city_dummy"] = st.session_state.output_city_dummy_input
if 'years_selected_input' in st.session_state:
    st.session_state.output_form_data["years_selected"] = st.session_state.years_selected_input
if 'output_desc_input' in st.session_state:
    st.session_state.output_form_data["output_desc"] = st.session_state.output_desc_input
if 'output_contact_input' in st.session_state:
    st.session_state.output_form_data["output_contact"] = st.session_state.output_contact_input
if 'output_linkedin_input' in st.session_state:
    st.session_state.output_form_data["output_linkedin"] = st.session_state.output_linkedin_input
if 'project_url_for_output_input' in st.session_state:
    st.session_state.output_form_data["project_url_for_output"] = st.session_state.project_url_for_output_input
if 'country_for_city_input' in st.session_state:
    st.session_state.output_form_data["country_for_city"] = st.session_state.country_for_city_input

# Processar submissão
if submitted:
    form_data = st.session_state.output_form_data
    
    # Validações
    if not form_data["submitter_email"].strip():
        st.warning("Please provide the submitter email.")
        st.stop()
    if not form_data["output_title"].strip():
        st.warning("Please provide the Output Name.")
        st.stop()
    if not form_data["output_countries"]:
        st.warning("Please select at least one country for geographic coverage.")
        st.stop()
    if form_data["output_type_sel"] == "Dataset" and (not form_data["output_data_type"] or form_data["output_data_type"] == SELECT_PLACEHOLDER):
        st.warning("Please select a Data type for Dataset outputs.")
        st.stop()
    if is_other_project and not (form_data["city_list_output"] or form_data["new_project_countries"]):
        st.warning("For a new project (Other), please add at least one country/city.")
        st.stop()

    if form_data["output_type_sel"] != "Dataset":
        form_data["output_data_type"] = ""

    # 1) Registrar projeto se for "Other"
    if is_other_project:
        wsP, errP = ws_projects()
        if errP or wsP is None:
            st.error(errP or "Worksheet unavailable for projects.")
            st.stop()
        
        countries_to_process = form_data["new_project_countries"]
        cities_to_process = form_data["city_list_output"]
        
        ok_allP, msg_anyP = True, None
        
        # Países sem cidades específicas
        for country in countries_to_process:
            latp, lonp = COUNTRY_CENTER_FULL.get(country, (None, None))
            rowP = {
                "country": country, "city": "", "lat": latp, "lon": lonp,
                "project_name": form_data["project_tax_other"].strip(), "years": "",
                "status": "", "data_types": "", "description": "",
                "contact": form_data["new_project_contact"], "access": "", "url": form_data["new_project_url"],
                "submitter_email": form_data["submitter_email"],
                "is_edit": "FALSE", "edit_target": "", "edit_request": "New project via output submission",
                "approved": "FALSE",
                "created_at": datetime.utcnow().isoformat(timespec="seconds")+"Z",
            }
            okP2, msgP2 = _append_row(wsP, PROJECTS_HEADERS, rowP)
            ok_allP &= okP2; msg_anyP = msgP2
        
        # Cidades com coordenadas específicas
        for pair in cities_to_process:
            if "—" not in pair: continue
            country, city = [p.strip() for p in pair.split("—",1)]
            # Usa coordenadas da cidade se disponível
            coords = form_data["city_coordinates"].get(pair, (None, None))
            if coords[0] and coords[1]:
                latp, lonp = coords
            else:
                latp, lonp = COUNTRY_CENTER_FULL.get(country, (None, None))
            
            rowP = {
                "country": country, "city": city, "lat": latp, "lon": lonp,
                "project_name": form_data["project_tax_other"].strip(), "years": "",
                "status": "", "data_types": "", "description": "",
                "contact": form_data["new_project_contact"], "access": "", "url": form_data["new_project_url"],
                "submitter_email": form_data["submitter_email"],
                "is_edit": "FALSE", "edit_target": "", "edit_request": "New project via output submission",
                "approved": "FALSE",
                "created_at": datetime.utcnow().isoformat(timespec="seconds")+"Z",
            }
            okP2, msgP2 = _append_row(wsP, PROJECTS_HEADERS, rowP)
            ok_allP &= okP2; msg_anyP = msgP2
            
        if not ok_allP:
            st.error(f"⚠️ Project staging write error: {msg_anyP}")
            st.stop()

    # 2) Gravar output
    wsO, errO = ws_outputs()
    if errO or wsO is None:
        st.error(errO or "Worksheet unavailable for outputs.")
        st.stop()

    # Define coordenadas - prioriza cidades
    lat_o, lon_o = (None, None)
    available_countries_for_cities = [c for c in form_data["output_countries"] if c not in ["Global", "Other: ______"]]
    
    if not is_global and available_countries_for_cities:
        # Tenta usar coordenadas da primeira cidade
        if form_data["city_list_output"]:
            first_city = form_data["city_list_output"][0]
            coords = form_data["city_coordinates"].get(first_city, (None, None))
            if coords[0] and coords[1]:
                lat_o, lon_o = coords
        
        # Fallback para primeiro país
        if lat_o is None and available_countries_for_cities:
            first_country = available_countries_for_cities[0]
            if first_country in COUNTRY_CENTER_FULL:
                lat_o, lon_o = COUNTRY_CENTER_FULL[first_country]

    output_cities_str = ", ".join(form_data["city_list_output"]) if form_data["city_list_output"] else ""
    output_countries_str = ", ".join(form_data["output_countries"])

    # Preparar anos
    final_years_sorted_desc = sorted(set(form_data["years_selected"]), reverse=True)
    final_years_str = ",".join(str(y) for y in final_years_sorted_desc) if final_years_sorted_desc else ""

    rowO = {
        "project": (form_data["project_tax_other"].strip() if is_other_project else form_data["project_tax_sel"]),
        "output_title": form_data["output_title"],
        "output_type": ("" if form_data["output_type_sel"].startswith("Other") else form_data["output_type_sel"]),
        "output_type_other": (form_data["output_type_other"] if form_data["output_type_sel"].startswith("Other") else ""),
        "output_data_type": form_data["output_data_type"],
        "output_url": form_data["output_url"],
        "output_country": output_countries_str,
        "output_country_other": (form_data["output_country_other"] if "Other: ______" in form_data["output_countries"] else ""),
        "output_city": output_cities_str,
        "output_year": final_years_str,
        "output_desc": form_data["output_desc"],
        "output_contact": form_data["output_contact"],
        "output_email": "",
        "output_linkedin": form_data["output_linkedin"],
        "project_url": (form_data["project_url_for_output"] or (form_data["new_project_url"] if is_other_project else "")),
        "submitter_email": form_data["submitter_email"],
        "is_edit": "FALSE","edit_target":"","edit_request":"New submission",
        "approved": "FALSE",
        "created_at": datetime.utcnow().isoformat(timespec="seconds")+"Z",
        "lat": lat_o if lat_o is not None else "",
        "lon": lon_o if lon_o is not None else "",
    }
    okO2, msgO2 = _append_row(wsO, OUTPUTS_HEADERS, rowO)
    if okO2:
        st.session_state.show_success = True
        st.rerun()
    else:
        st.error(f"⚠️ {msgO2}")
