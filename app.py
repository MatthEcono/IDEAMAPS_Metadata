# app.py
import base64
from pathlib import Path
from typing import Optional, List, Tuple
import gspread
import pandas as pd
import re
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
# 0.1) FLASH PERSISTENTE
# ──────────────────────────────────────────────────────────────────────────────
ss = st.session_state
if "_flash" not in ss:
    ss._flash = None

def flash(message: str, level: str = "success"):
    ss._flash = {"msg": message, "level": level}

def show_flash():
    data = ss.get("_flash")
    if not data:
        return
    level = data.get("level", "success")
    msg = data.get("msg", "")
    box = st.container(border=True)
    with box:
        (st.success if level=="success" else st.info if level=="info" else st.warning if level=="warning" else st.error)(msg)
        if st.button("Dismiss"):
            ss._flash = None
            st.rerun()

show_flash()

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
        ss_ = client.open_by_key(ss_id)
    except Exception as e:
        return None, f"Open spreadsheet error: {e}"
    try:
        ws = ss_.worksheet(ws_name)
    except gspread.exceptions.WorksheetNotFound:
        ncols = max(10, len(headers) if headers else 10)
        ws = ss_.add_worksheet(title=ws_name, rows=3000, cols=ncols)
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
    if "Global" in names:
        return ["Global"] + [n for n in names if n != "Global"]
    else:
        return ["Global"] + names

# ──────────────────────────────────────────────────────────────────────────────
# 4) Países (CSV local)
# ──────────────────────────────────────────────────────────────────────────────
COUNTRY_CSV_PATH = APP_DIR / "country-coord.csv"

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

COUNTRY_CENTER_FULL, _df_countries = load_country_centers()
COUNTRY_NAMES = sorted(COUNTRY_CENTER_FULL.keys()) if COUNTRY_CENTER_FULL else []

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
# 6) Carregamento (apenas aprovados)
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
    load_projects_public.clear(); load_outputs_public.clear(); load_country_centers.clear()
    st.rerun()

df_projects, okP, msgP = load_projects_public()
if not okP and msgP:
    st.caption(f"⚠️ {msgP}")

# ──────────────────────────────────────────────────────────────────────────────
# 7) Mapa (outputs aprovados)
# ──────────────────────────────────────────────────────────────────────────────
st.subheader("Projects & outputs map (approved outputs)")
df_outputs_map, okOm, msgOm = load_outputs_public()
if not okOm and msgOm:
    st.caption(f"⚠️ {msgOm}")
else:
    has_coords = (not df_outputs_map.empty) and (df_outputs_map[["lat","lon"]].dropna().shape[0] > 0)
    if has_coords:
        dfc = df_outputs_map.dropna(subset=["lat","lon"]).copy()
        center_lat, center_lon = (dfc["lat"].mean(), dfc["lon"].mean()) if not dfc.empty else (0, 0)
        m = folium.Map(location=[center_lat, center_lon], zoom_start=2, tiles="CartoDB dark_matter")
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
                        inner.append(f"{t} (<a href='{u}' target='_blank' style='color:#2563eb;text-decoration:none;'>link</a>)" if u else t)
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
# 8) Tabela de outputs aprovados
# ──────────────────────────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("Browse outputs (approved only)")

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
                details_col: st.column_config.CheckboxColumn(details_col, help="Open details for this row"),
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
                        on_click=lambda: ss.update({"_want_open_dialog": False, "_selected_output_idx": None})
                    )

        if ss._want_open_dialog:
            idx = ss._selected_output_idx
            if isinstance(idx, int) and (0 <= idx < len(df_base)):
                row = df_base.iloc[idx]
                _open_details(row)
            ss._want_open_dialog = False
            ss._selected_output_idx = None

# ──────────────────────────────────────────────────────────────────────────────
# 9) SUBMISSÃO DE OUTPUT - CORRIGIDA (SEM USO INCORRETO DO SESSION_STATE)
# ──────────────────────────────────────────────────────────────────────────────

st.markdown("---")
st.header("Submit Output (goes to review queue)")

# Inicialização do estado
if "form_data" not in ss:
    ss.form_data = {
        "cities": [],
        "show_city_section": False
    }

# Funções para gerenciar cidades
def add_city(country, city_name):
    """Adiciona uma cidade à lista"""
    if country and country != SELECT_PLACEHOLDER and city_name.strip():
        for c in [x.strip() for x in city_name.split(",") if x.strip()]:
            pair = f"{country} — {c}"
            if pair not in ss.form_data["cities"]:
                ss.form_data["cities"].append(pair)
        return True
    return False

def remove_city(index):
    """Remove uma cidade da lista"""
    if 0 <= index < len(ss.form_data["cities"]):
        ss.form_data["cities"].pop(index)
        return True
    return False

def clear_form():
    """Limpa o formulário"""
    ss.form_data = {
        "cities": [],
        "show_city_section": False
    }

# Formulário principal
with st.form("output_form", clear_on_submit=False):
    st.subheader("Basic Information")
    
    # Campos obrigatórios
    submitter_email = st.text_input(
        "Submitter email (required for review)*",
        placeholder="name@org.org"
    )
    
    project_tax_sel = st.selectbox(
        "Project Name (taxonomy)*",
        options=PROJECT_TAXONOMY
    )
    
    is_other_project = project_tax_sel.startswith("Other")
    project_tax_other = ""
    if is_other_project:
        project_tax_other = st.text_input("Please specify the project (taxonomy)*")
    
    output_type_sel = st.selectbox("Output Type*", options=OUTPUT_TYPES)
    
    # Campo condicional para Dataset - CORREÇÃO AQUI
    output_data_type = None
    if output_type_sel == "Dataset":
        output_data_type = st.selectbox(
            "Data type (for datasets)*", 
            options=[SELECT_PLACEHOLDER] + DATASET_DTYPES
        )
    else:
        output_data_type = SELECT_PLACEHOLDER
    
    output_type_other = ""
    if output_type_sel.startswith("Other"):
        output_type_other = st.text_input("Please specify the output type*")
    
    output_title = st.text_input("Output Name*")
    output_url = st.text_input("Output URL (optional)")
    
    # Seção para novo projeto
    if is_other_project:
        st.subheader("New Project Details")
        
        new_project_countries = st.multiselect(
            "Implementation countries (one or more)*",
            COUNTRY_NAMES
        )
        
        if new_project_countries:
            st.write("**Add cities for the new project:**")
            
            col_country, col_city, col_btn = st.columns([2, 2, 1])
            with col_country:
                new_country_select = st.selectbox(
                    "Select country",
                    options=[SELECT_PLACEHOLDER] + new_project_countries,
                    key="new_country_select"
                )
            with col_city:
                new_city_input = st.text_input(
                    "City name (accepts multiple, separated by commas)*",
                    placeholder="Enter city name",
                    key="new_city_input"
                )
            with col_btn:
                st.write("")
                st.write("")
                add_new_city = st.form_submit_button("➕ Add City", use_container_width=True)
                if add_new_city:
                    if add_city(new_country_select, new_city_input):
                        st.rerun()
        
        new_project_url = st.text_input("Project URL (optional)")
        new_project_contact = st.text_input("Project contact / institution (optional)")
    
    # Cobertura geográfica do output
    st.subheader("Geographic Coverage")
    
    output_countries = st.multiselect(
        "Select countries (select 'Global' for worldwide coverage)*",
        options=_countries_with_global_first(COUNTRY_NAMES) + ["Other: ______"]
    )
    
    is_global = "Global" in output_countries
    
    output_country_other = ""
    if "Other: ______" in output_countries:
        output_country_other = st.text_input("Please specify other geographic coverage")
    
    # SEÇÃO DE CIDADES - APARECE AUTOMATICAMENTE QUANDO PAÍSES SÃO SELECIONADOS
    if output_countries and not is_global:
        available_countries = [c for c in output_countries if c not in ["Global", "Other: ______"]]
        
        if available_countries:
            st.write("**Add cities for this output:**")
            
            col_country_out, col_city_out, col_btn_out = st.columns([2, 2, 1])
            with col_country_out:
                output_country_select = st.selectbox(
                    "Select country",
                    options=[SELECT_PLACEHOLDER] + available_countries,
                    key="output_country_select"
                )
            with col_city_out:
                output_city_input = st.text_input(
                    "City name (accepts multiple, separated by commas)*",
                    placeholder="Enter city name",
                    key="output_city_input"
                )
            with col_btn_out:
                st.write("")
                st.write("")
                add_output_city = st.form_submit_button("➕ Add City", use_container_width=True)
                if add_output_city:
                    if add_city(output_country_select, output_city_input):
                        st.rerun()
    
    # Mensagem informativa quando Global é selecionado
    if is_global:
        st.info("🌍 Global coverage selected - city selection is disabled")
    
    # Lista de cidades adicionadas
    if ss.form_data["cities"]:
        st.write("**Added cities:**")
        for i, city_pair in enumerate(ss.form_data["cities"]):
            col1, col2 = st.columns([4, 1])
            with col1:
                st.write(f"📍 {city_pair}")
            with col2:
                remove_btn = st.form_submit_button("🗑️ Remove", key=f"remove_{i}")
                if remove_btn:
                    if remove_city(i):
                        st.rerun()
    
    # Mapa de preview
    if ss.form_data["cities"] and not is_global:
        st.write("**Map Preview:**")
        
        # Encontrar centro do mapa baseado nos países selecionados
        available_countries = [c for c in output_countries if c not in ["Global", "Other: ______"]]
        if available_countries and available_countries[0] in COUNTRY_CENTER_FULL:
            center_lat, center_lon = COUNTRY_CENTER_FULL[available_countries[0]]
        else:
            center_lat, center_lon = 0, 0
        
        m = folium.Map(location=[center_lat, center_lon], zoom_start=3, tiles="CartoDB positron")
        
        # Adicionar círculos para países selecionados
        for country in output_countries:
            if country in COUNTRY_CENTER_FULL and country not in ["Global", "Other: ______"]:
                folium.CircleMarker(
                    location=COUNTRY_CENTER_FULL[country],
                    radius=10,
                    popup=country,
                    tooltip=country,
                    color="blue",
                    fill=True,
                    fill_opacity=0.6
                ).add_to(m)
        
        # Adicionar marcadores para cidades
        for pair in ss.form_data["cities"]:
            if "—" in pair:
                country, city = [p.strip() for p in pair.split("—", 1)]
                if country in COUNTRY_CENTER_FULL:
                    folium.Marker(
                        location=COUNTRY_CENTER_FULL[country],
                        popup=f"{city}, {country}",
                        tooltip=f"{city}, {country}",
                        icon=folium.Icon(color="red", icon="info-sign")
                    ).add_to(m)
        
        st_folium(m, height=300, width=None)
    
    # Informações adicionais
    st.subheader("Additional Information")
    
    current_year = datetime.utcnow().year
    base_years_desc = list(range(current_year, 1999, -1))
    years_selected = st.multiselect("Year of output release", base_years_desc)
    
    output_desc = st.text_area("Short description of output")
    output_contact = st.text_input("Name & institution of person responsible")
    output_linkedin = st.text_input("LinkedIn address of contact")
    project_url_for_output = st.text_input("Project URL (optional, if different)")
    
    # Botões de ação
    col1, col2 = st.columns([1, 1])
    with col1:
        submitted = st.form_submit_button(
            "✅ Submit for Review",
            use_container_width=True,
            type="primary"
        )
    with col2:
        clear_btn = st.form_submit_button(
            "🗑️ Clear Form",
            use_container_width=True,
            type="secondary"
        )
        if clear_btn:
            clear_form()
            st.rerun()

# Processar submissão
if submitted:
    # Validações
    errors = []
    
    if not submitter_email.strip():
        errors.append("❌ Submitter email is required")
    if not output_title.strip():
        errors.append("❌ Output name is required")
    if not output_countries:
        errors.append("❌ At least one country must be selected")
    if output_type_sel == "Dataset" and output_data_type == SELECT_PLACEHOLDER:
        errors.append("❌ Data type is required for datasets")
    if is_other_project and not project_tax_other.strip():
        errors.append("❌ Project name is required when selecting 'Other'")
    if is_other_project and not ss.form_data["cities"] and not new_project_countries:
        errors.append("❌ For new projects, please add at least one country or city")
    
    if errors:
        for error in errors:
            st.error(error)
        st.stop()
    
    # Processar dados
    try:
        # 1) Registrar projeto se for "Other"
        if is_other_project:
            wsP, errP = ws_projects()
            if errP or wsP is None:
                st.error(errP or "Worksheet unavailable for projects.")
                st.stop()
            
            # Países sem cidades específicas
            for country in new_project_countries:
                latp, lonp = COUNTRY_CENTER_FULL.get(country, (None, None))
                rowP = {
                    "country": country, 
                    "city": "", 
                    "lat": latp, 
                    "lon": lonp,
                    "project_name": project_tax_other.strip(), 
                    "years": "",
                    "status": "", 
                    "data_types": "", 
                    "description": "",
                    "contact": new_project_contact, 
                    "access": "", 
                    "url": new_project_url,
                    "submitter_email": submitter_email,
                    "is_edit": "FALSE", 
                    "edit_target": "", 
                    "edit_request": "New project via output submission",
                    "approved": "FALSE",
                    "created_at": datetime.utcnow().isoformat(timespec="seconds")+"Z",
                }
                _append_row(wsP, PROJECTS_HEADERS, rowP)
            
            # Cidades
            for city_pair in ss.form_data["cities"]:
                if "—" in city_pair:
                    country, city = [p.strip() for p in city_pair.split("—",1)]
                    latp, lonp = COUNTRY_CENTER_FULL.get(country, (None, None))
                    
                    rowP = {
                        "country": country, 
                        "city": city, 
                        "lat": latp, 
                        "lon": lonp,
                        "project_name": project_tax_other.strip(), 
                        "years": "",
                        "status": "", 
                        "data_types": "", 
                        "description": "",
                        "contact": new_project_contact, 
                        "access": "", 
                        "url": new_project_url,
                        "submitter_email": submitter_email,
                        "is_edit": "FALSE", 
                        "edit_target": "", 
                        "edit_request": "New project via output submission",
                        "approved": "FALSE",
                        "created_at": datetime.utcnow().isoformat(timespec="seconds")+"Z",
                    }
                    _append_row(wsP, PROJECTS_HEADERS, rowP)
        
        # 2) Gravar output
        wsO, errO = ws_outputs()
        if errO or wsO is None:
            st.error(errO or "Worksheet unavailable for outputs.")
            st.stop()
        
        # Determinar coordenadas
        lat_o, lon_o = (None, None)
        available_countries = [c for c in output_countries if c not in ["Global", "Other: ______"]]
        
        if not is_global and available_countries:
            # Usa centro do primeiro país selecionado
            if available_countries[0] in COUNTRY_CENTER_FULL:
                lat_o, lon_o = COUNTRY_CENTER_FULL[available_countries[0]]
        
        # Preparar dados
        output_cities_str = ", ".join(ss.form_data["cities"])
        output_countries_str = ", ".join(output_countries)
        
        final_years_sorted_desc = sorted(set(years_selected), reverse=True)
        final_years_str = ",".join(str(y) for y in final_years_sorted_desc) if final_years_sorted_desc else ""
        
        if output_type_sel != "Dataset":
            output_data_type = ""
        
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
            "project_url": (project_url_for_output or (new_project_url if is_other_project else "")),
            "submitter_email": submitter_email,
            "is_edit": "FALSE", "edit_target": "", "edit_request": "New submission",
            "approved": "FALSE",
            "created_at": datetime.utcnow().isoformat(timespec="seconds")+"Z",
            "lat": lat_o if lat_o is not None else "",
            "lon": lon_o if lon_o is not None else "",
        }
        
        okO2, msgO2 = _append_row(wsO, OUTPUTS_HEADERS, rowO)
        if okO2:
            st.success("✅ Output submission queued for review!")
            st.balloons()
            clear_form()
        else:
            st.error(f"⚠️ Error saving output: {msgO2}")
            
    except Exception as e:
        st.error(f"An error occurred: {str(e)}")
