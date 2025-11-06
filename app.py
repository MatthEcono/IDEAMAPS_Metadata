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

# ──────────────────────────────────────────────────────────────────────────────
# 4) Países e Cidades (CSV local/remoto)
# ──────────────────────────────────────────────────────────────────────────────
COUNTRY_CSV_PATH = APP_DIR / "country-coord.csv"
CITIES_CSV_PATH = APP_DIR / "world_cities.csv"
WORLD_CITIES_URL = "https://raw.githubusercontent.com/joelacus/world-cities/master/world-cities.csv"

@st.cache_data(show_spinner=False)
def load_world_cities():
    """Carrega o arquivo de cidades do mundo com coordenadas - baixa do GitHub se necessário"""
    try:
        # Tenta carregar localmente primeiro
        if CITIES_CSV_PATH.exists():
            df = pd.read_csv(CITIES_CSV_PATH, dtype=str, encoding="utf-8", on_bad_lines="skip")
        else:
            # Se não existe localmente, baixa do GitHub
            st.info("📥 Baixando dados de cidades do mundo...")
            df = pd.read_csv(WORLD_CITIES_URL, dtype=str, encoding="utf-8", on_bad_lines="skip")
            
            # Salva localmente para uso futuro
            CITIES_CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(CITIES_CSV_PATH, index=False, encoding="utf-8")
            st.success("✅ Dados de cidades baixados e salvos localmente")
        
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
            st.write("Colunas disponíveis:", df.columns.tolist())
            return pd.DataFrame()
        
        # Converte coordenadas
        df["lat"] = df["lat"].apply(_parse_number_loose)
        df["lon"] = df["lon"].apply(_parse_number_loose)
        df = df.dropna(subset=["lat", "lon"])
        
        return df[['country', 'city', 'lat', 'lon']]
    except Exception as e:
        st.error(f"Erro ao carregar cidades: {e}")
        # Fallback: retorna DataFrame vazio mas com as colunas esperadas
        return pd.DataFrame(columns=['country', 'city', 'lat', 'lon'])

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
        m = folium.Map(
            location=[dfc["lat"].mean(), dfc["lon"].mean()],
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

# app.py (apenas a seção 9 - SUBMISSÃO DE OUTPUT com as correções)
# ... (código anterior permanece igual)

# app.py (apenas a seção 9 - SUBMISSÃO DE OUTPUT com as correções)
# ... (código anterior permanece igual)

# ──────────────────────────────────────────────────────────────────────────────
# 9) SUBMISSÃO DE OUTPUT
# ──────────────────────────────────────────────────────────────────────────────

_FORM_KEYS = {
    "submitter_email","project_tax_sel","project_tax_other",
    "new_project_url","new_project_contact","new_project_countries",
    "selected_country_city","city_add_proj","_clear_cities_flag",
    "output_type_sel","output_type_other","output_data_type",
    "output_title","output_url",
    "output_country","output_country_other",
    "output_city_dummy",
    "years_selected",
    "output_desc","output_contact","output_linkedin","project_url_for_output",
    "country_for_city", "output_countries", "city_list_output",
    "_countries_applied"  # Nova chave para controlar se países foram aplicados
}

def _really_clear_output_form_state():
    for k in list(_FORM_KEYS):
        st.session_state.pop(k, None)
    st.session_state.pop("map_center", None)
    st.session_state["map_zoom"] = 2
    st.session_state["_clear_city_field_out"] = False
    st.session_state["_clear_city_field_newproj"] = False
    if "city_coordinates" in st.session_state:
        st.session_state.city_coordinates = {}

if "_pending_form_reset" not in st.session_state:
    st.session_state._pending_form_reset = False

if st.session_state._pending_form_reset:
    _really_clear_output_form_state()
    st.session_state._pending_form_reset = False

# Estado inicial
if "output_countries" not in st.session_state:
    st.session_state.output_countries = []
if "city_list_output" not in st.session_state:
    st.session_state.city_list_output = []
if "city_coordinates" not in st.session_state:
    st.session_state.city_coordinates = {}
if "map_center" not in st.session_state:
    st.session_state.map_center = None
if "map_zoom" not in st.session_state:
    st.session_state.map_zoom = 2
if "_clear_city_field_out" not in st.session_state:
    st.session_state._clear_city_field_out = False
if "_clear_city_field_newproj" not in st.session_state:
    st.session_state._clear_city_field_newproj = False
if "_countries_applied" not in st.session_state:
    st.session_state._countries_applied = False  # Nova flag

st.markdown("---")
st.header("Submit Output (goes to review queue)")

with st.form("OUTPUT_FORM", clear_on_submit=False):
    submitter_email = st.text_input(
        "Submitter email (required for review)",
        key="submitter_email",
        placeholder="name@org.org"
    )

    project_tax_sel = st.selectbox(
        "Project Name (taxonomy)",
        options=PROJECT_TAXONOMY,
        key="project_tax_sel"
    )
    is_other_project = project_tax_sel.startswith("Other")
    project_tax_other = ""
    if is_other_project:
        project_tax_other = st.text_input("Please specify the project (taxonomy)", key="project_tax_other")

    new_project_url = ""
    new_project_contact = ""
    if is_other_project:
        st.markdown("**New project details (required if not in taxonomy)**")
        countries_sel = st.multiselect(
            "Implementation countries (one or more)",
            COUNTRY_NAMES,
            key="new_project_countries"
        )
        colc1, colc2, colc3 = st.columns([2,2,1])
        with colc1:
            selected_country_city = st.selectbox(
                "Select implementation country for the city",
                options=[SELECT_PLACEHOLDER] + countries_sel if countries_sel else [SELECT_PLACEHOLDER],
                index=0, disabled=not bool(countries_sel),
                key="selected_country_city"
            )
        with colc2:
            # CORREÇÃO: Campo de texto livre para cidade (sem autocompletar)
            if st.session_state._clear_city_field_newproj and "city_add_proj" in st.session_state:
                del st.session_state["city_add_proj"]
                st.session_state._clear_city_field_newproj = False
            city_input_proj = st.text_input(
                "City name (type manually)",
                key="city_add_proj",
                placeholder="Enter city name"
            )
        with colc3:
            st.write("")
            if st.form_submit_button("➕ Add city to NEW project"):
                if selected_country_city and selected_country_city != SELECT_PLACEHOLDER and city_input_proj.strip():
                    for c in [x.strip() for x in city_input_proj.split(",") if x.strip()]:
                        pair = f"{selected_country_city} — {c}"
                        if pair not in st.session_state.city_list_output:
                            st.session_state.city_list_output.append(pair)
                            # Tenta encontrar coordenadas, mas não é obrigatório
                            lat, lon = find_city_coordinates(selected_country_city, c)
                            if lat and lon:
                                st.session_state.city_coordinates[pair] = (lat, lon)
                    st.session_state._clear_city_field_newproj = True
                    st.rerun()
                else:
                    st.warning("Select a valid country and type a city name.")

        if st.session_state.get("city_list_output"):
            st.caption("Cities added to NEW project:")
            for i, it in enumerate(st.session_state.city_list_output):
                c1, c2 = st.columns([6,1])
                with c1: 
                    coords = st.session_state.city_coordinates.get(it, (None, None))
                    if coords[0] and coords[1]:
                        st.write(f"- {it} (📍 {coords[0]:.4f}, {coords[1]:.4f})")
                    else:
                        st.write(f"- {it} (⚠️ coordinates not found)")
                with c2:
                    if st.form_submit_button("Remove", key=f"rm_city_newproj_{i}"):
                        st.session_state.city_list_output.pop(i)
                        if it in st.session_state.city_coordinates:
                            del st.session_state.city_coordinates[it]
                        st.rerun()

        new_project_url = st.text_input("Project URL (optional)", key="new_project_url")
        new_project_contact = st.text_input("Project contact / institution (optional)", key="new_project_contact")

    output_type_sel = st.selectbox("Output Type", options=OUTPUT_TYPES, key="output_type_sel")
    
    output_data_type = ""
    if output_type_sel == "Dataset":
        output_data_type = st.selectbox(
            "Data type (for datasets) *", 
            options=[SELECT_PLACEHOLDER] + DATASET_DTYPES,
            key="output_data_type"
        )
    else:
        output_data_type = ""

    output_type_other = ""
    if output_type_sel.startswith("Other"):
        output_type_other = st.text_input("Please specify the output type", key="output_type_other")

    output_title = st.text_input("Output Name *", key="output_title")
    output_url   = st.text_input("Output URL (optional)", key="output_url")

    st.markdown("**Geographic coverage of output**")
    countries_fixed = _countries_with_global_first(COUNTRY_NAMES) + ["Other: ______"]
    output_countries = st.multiselect(
        "Select one or more countries (select 'Global' for worldwide coverage) *",
        options=countries_fixed,
        key="output_countries"
    )
    
    # NOVO: Botão para aplicar a seleção de países
    col_apply1, col_apply2 = st.columns([3, 1])
    with col_apply1:
        if st.form_submit_button("✅ Apply countries selection", use_container_width=True):
            if output_countries:
                st.session_state._countries_applied = True
                st.session_state.output_countries = output_countries.copy()
                st.rerun()
            else:
                st.warning("Please select at least one country first")
    
    with col_apply2:
        if st.form_submit_button("🔄 Clear selection", use_container_width=True):
            st.session_state._countries_applied = False
            st.session_state.output_countries = []
            st.session_state.city_list_output = []
            if "city_coordinates" in st.session_state:
                st.session_state.city_coordinates = {}
            st.rerun()
    
    # Mostra status da seleção
    if st.session_state._countries_applied and output_countries:
        st.success(f"✅ Countries applied: {', '.join(output_countries)}")
    
    is_global = "Global" in output_countries
    if is_global:
        st.info("Global coverage selected - city fields will be disabled")
        if len(output_countries) > 1:
            st.session_state.output_countries = ["Global"]
            st.rerun()
    
    output_country_other = ""
    if "Other: ______" in output_countries:
        output_country_other = st.text_input("Please specify other geographic coverage", key="output_country_other")

    # CORREÇÃO: Cidades para OUTPUT - campo de texto livre
    st.markdown("**Cities covered**")
    
    # Só mostra campos de cidade se países foram aplicados e não é Global
    available_countries_for_cities = []
    if st.session_state._countries_applied and output_countries:
        available_countries_for_cities = [c for c in output_countries if c not in ["Global", "Other: ______"]]
    
    if not st.session_state._countries_applied:
        st.info("👆 Please select countries above and click 'Apply countries selection' to add cities")
    
    colx1, colx2, colx3 = st.columns([2,2,1])
    with colx1:
        country_for_city = st.selectbox(
            "Country for the city",
            options=[SELECT_PLACEHOLDER] + available_countries_for_cities,
            index=0,
            key="country_for_city",
            disabled=is_global or not available_countries_for_cities or not st.session_state._countries_applied
        )
    with colx2:
        # CORREÇÃO: Campo de texto livre para cidade (sem autocompletar)
        if st.session_state._clear_city_field_out and "output_city_dummy" in st.session_state:
            del st.session_state["output_city_dummy"]
            st.session_state._clear_city_field_out = False
        city_input_out = st.text_input(
            "City name (type manually)",
            key="output_city_dummy",
            placeholder="Enter city name",
            disabled=is_global or not st.session_state._countries_applied
        )
    with colx3:
        st.write("")
        # CORREÇÃO: Botão só habilitado quando países foram aplicados e há seleção válida
        add_city_disabled = (is_global or 
                           not st.session_state._countries_applied or 
                           not country_for_city or 
                           country_for_city == SELECT_PLACEHOLDER or 
                           not city_input_out.strip())
        
        if st.form_submit_button("➕ Add city to OUTPUT", disabled=add_city_disabled):
            if (st.session_state._countries_applied and 
                not is_global and 
                country_for_city and 
                country_for_city != SELECT_PLACEHOLDER and 
                city_input_out.strip()):
                
                for c in [x.strip() for x in city_input_out.split(",") if x.strip()]:
                    pair = f"{country_for_city} — {c}"
                    if pair not in st.session_state.city_list_output:
                        st.session_state.city_list_output.append(pair)
                        # Tenta encontrar coordenadas, mas não é obrigatório
                        lat, lon = find_city_coordinates(country_for_city, c)
                        if lat and lon:
                            st.session_state.city_coordinates[pair] = (lat, lon)
                st.session_state._clear_city_field_out = True
                st.rerun()
            elif is_global:
                st.warning("Cannot add cities for global coverage")
            elif not st.session_state._countries_applied:
                st.warning("Please apply countries selection first")
            else:
                st.warning("Choose a valid country and type a city name.")

    if st.session_state.get("city_list_output") and not is_global and st.session_state._countries_applied:
        st.caption("Cities added to OUTPUT:")
        for i, it in enumerate(st.session_state.city_list_output):
            c1, c2 = st.columns([6,1])
            with c1: 
                coords = st.session_state.city_coordinates.get(it, (None, None))
                if coords[0] and coords[1]:
                    st.write(f"- {it} (📍 {coords[0]:.4f}, {coords[1]:.4f})")
                else:
                    st.write(f"- {it} (⚠️ coordinates not found)")
            with c2:
                if st.form_submit_button("Remove", key=f"rm_city_out_{i}"):
                    st.session_state.city_list_output.pop(i)
                    if it in st.session_state.city_coordinates:
                        del st.session_state.city_coordinates[it]
                    st.rerun()

    # Mapa com cidades
    if (st.session_state._countries_applied and 
        not is_global and 
        available_countries_for_cities):
        
        if available_countries_for_cities:
            first_country = available_countries_for_cities[0]
            if first_country in COUNTRY_CENTER_FULL:
                st.session_state.map_center = COUNTRY_CENTER_FULL[first_country]
                st.session_state.map_zoom = 3
                
            if st.session_state.get("map_center"):
                m = folium.Map(
                    location=st.session_state.map_center,
                    zoom_start=st.session_state.map_zoom,
                    tiles="CartoDB positron"
                )
                
                # Marca países
                for country in available_countries_for_cities:
                    if country in COUNTRY_CENTER_FULL:
                        latlon = COUNTRY_CENTER_FULL[country]
                        folium.CircleMarker(
                            location=latlon, radius=8, color="#2563eb",
                            fill=True, fill_opacity=0.9, tooltip=f"{country}"
                        ).add_to(m)
                
                # Marca cidades com coordenadas específicas
                for pair in st.session_state.get("city_list_output", []):
                    if "—" in pair:
                        ctry, cty = [p.strip() for p in pair.split("—",1)]
                        coords = st.session_state.city_coordinates.get(pair, None)
                        if coords and coords[0] is not None and coords[1] is not None:
                            # Usa coordenadas específicas da cidade
                            folium.Marker(
                                location=coords, 
                                tooltip=f"{cty} ({ctry})",
                                icon=folium.Icon(color="green", icon="info-sign")
                            ).add_to(m)
                        else:
                            # Fallback para centro do país
                            latlon = COUNTRY_CENTER_FULL.get(ctry)
                            if latlon:
                                folium.Marker(
                                    location=latlon, 
                                    tooltip=f"{cty} ({ctry})",
                                    icon=folium.Icon(color="orange", icon="info-sign")
                                ).add_to(m)
                st_folium(m, height=320, width=None)
    elif is_global and st.session_state._countries_applied:
        st.info("Map preview not available for global coverage")

    current_year = datetime.utcnow().year
    base_years_desc = list(range(current_year, 1999, -1))
    years_selected = st.multiselect("Year of output release", base_years_desc, key="years_selected")
    final_years_sorted_desc = sorted(set(years_selected), reverse=True)
    final_years_str = ",".join(str(y) for y in final_years_sorted_desc) if final_years_sorted_desc else ""

    output_desc = st.text_area("Short description of output", key="output_desc")
    output_contact = st.text_input("Name & institution of person responsible", key="output_contact")
    output_linkedin = st.text_input("LinkedIn address of contact", key="output_linkedin")
    project_url_for_output = st.text_input("Project URL (optional, if different)", key="project_url_for_output")

    submitted = st.form_submit_button("Submit for review (Output)")

    if submitted:
        if not submitter_email.strip():
            st.warning("Please provide the submitter email."); st.stop()
        if not output_title.strip():
            st.warning("Please provide the Output Name."); st.stop()
        if not output_countries:
            st.warning("Please select at least one country for geographic coverage."); st.stop()
        if output_type_sel == "Dataset" and (not output_data_type or output_data_type == SELECT_PLACEHOLDER):
            st.warning("Please select a Data type for Dataset outputs."); st.stop()
        if is_other_project and not (st.session_state.get("city_list_output") or st.session_state.get("new_project_countries")):
            st.warning("For a new project (Other), please add at least one country/city."); st.stop()

        if output_type_sel != "Dataset":
            output_data_type = ""

        # 1) Registrar projeto se for "Other"
        if is_other_project:
            wsP, errP = ws_projects()
            if errP or wsP is None:
                st.error(errP or "Worksheet unavailable for projects."); st.stop()
            
            countries_to_process = st.session_state.get("new_project_countries", [])
            cities_to_process = st.session_state.get("city_list_output", [])
            
            ok_allP, msg_anyP = True, None
            
            # Países sem cidades específicas
            for country in countries_to_process:
                latp, lonp = COUNTRY_CENTER_FULL.get(country, (None, None))
                rowP = {
                    "country": country, "city": "", "lat": latp, "lon": lonp,
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
            
            # Cidades com coordenadas específicas
            for pair in cities_to_process:
                if "—" not in pair: continue
                country, city = [p.strip() for p in pair.split("—",1)]
                # Usa coordenadas da cidade se disponível
                coords = st.session_state.city_coordinates.get(pair, (None, None))
                if coords[0] and coords[1]:
                    latp, lonp = coords
                else:
                    latp, lonp = COUNTRY_CENTER_FULL.get(country, (None, None))
                
                rowP = {
                    "country": country, "city": city, "lat": latp, "lon": lonp,
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
                st.error(f"⚠️ Project staging write error: {msg_anyP}"); st.stop()

        # 2) Gravar output
        wsO, errO = ws_outputs()
        if errO or wsO is None:
            st.error(errO or "Worksheet unavailable for outputs."); st.stop()

        # Define coordenadas - prioriza cidades
        lat_o, lon_o = (None, None)
        if not is_global and available_countries_for_cities:
            # Tenta usar coordenadas da primeira cidade
            if st.session_state.get("city_list_output"):
                first_city = st.session_state.city_list_output[0]
                coords = st.session_state.city_coordinates.get(first_city, (None, None))
                if coords[0] and coords[1]:
                    lat_o, lon_o = coords
            
            # Fallback para primeiro país
            if lat_o is None and available_countries_for_cities:
                first_country = available_countries_for_cities[0]
                if first_country in COUNTRY_CENTER_FULL:
                    lat_o, lon_o = COUNTRY_CENTER_FULL[first_country]

        output_cities_str = ", ".join(st.session_state.get("city_list_output", [])) if st.session_state.get("city_list_output") else ""
        output_countries_str = ", ".join(output_countries)

        rowO = {
            "project": (project_tax_other.strip() if is_other_project else project_tax_sel),
            "output_title": output_title,
            "output_type": ("" if output_type_sel.startswith("Other") else output_type_sel),
            "output_type_other": (output_type_other if output_type_sel.startswith("Other") else ""),
            "output_data_type": output_data_type,
            "output_url": output_url,
            "output_country": output_countries_str,
            "output_country_other": (output_country_other if "Other: ______" in output_countries else ""),
            "output_city": output_cities_str,
            "output_year": final_years_str,
            "output_desc": output_desc,
            "output_contact": output_contact,
            "output_email": "",
            "output_linkedin": output_linkedin,
            "project_url": (project_url_for_output or (st.session_state.get("new_project_url","") if is_other_project else "")),
            "submitter_email": submitter_email,
            "is_edit": "FALSE","edit_target":"","edit_request":"New submission",
            "approved": "FALSE",
            "created_at": datetime.utcnow().isoformat(timespec="seconds")+"Z",
            "lat": lat_o if lat_o is not None else "",
            "lon": lon_o if lon_o is not None else "",
        }
        okO2, msgO2 = _append_row(wsO, OUTPUTS_HEADERS, rowO)
        if okO2:
            st.success("✅ Output submission queued for review")
            _really_clear_output_form_state()
            st.session_state._pending_form_reset = True
            st.rerun()
        else:
            st.error(f"⚠️ {msgO2}")
