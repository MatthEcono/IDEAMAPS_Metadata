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

# outputs têm lat/lon e output_linkedin
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
    "output_email",        # legado (mantemos vazio)
    "output_linkedin",     # novo
    "project_url",
    "submitter_email",
    "is_edit","edit_target","edit_request",
    "approved","created_at",
    "lat","lon"            # coords p/ mapear (centro do país)
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
# 6) Carregamento (projects só para eventual pré-população; outputs exibidos)
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
        # garante headers
        for c in OUTPUTS_HEADERS:
            if c not in df.columns:
                df[c] = ""
        # normaliza aprovado
        df["approved"] = df["approved"].astype(str).str.upper().isin(["TRUE","1","YES"])
        df = df[df["approved"]].copy()
        # tipa coords
        df["lat"] = df["lat"].apply(_as_float)
        df["lon"] = df["lon"].apply(_as_float)
        # fallback: se não houver lat/lon, tenta do país
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
# 7) Mapa (APENAS de OUTPUTS aprovados, usando lat/lon salvos no sheet)
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
            # agregação: lista de projetos e outputs
            proj_info = {}
            for _, r in g.iterrows():
                proj = (str(r.get("project","")).strip() or "(unnamed)")
                out_title = str(r.get("output_title","")).strip()
                out_url = _clean_url(r.get("output_url",""))
                proj_info.setdefault(proj, [])
                proj_info[proj].append((out_title, out_url))
            # tooltip/popup
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
# 8) Tabela de outputs (prévia + coluna [See full information] por linha)
#     - seleção exclusiva
#     - desmarca imediatamente ao abrir o pop-up (ou ao fechar)
# ──────────────────────────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("Browse outputs (approved only)")

# Estado auxiliar
ss = st.session_state
if "_selected_output_idx" not in ss:
    ss._selected_output_idx = None
if "_want_open_dialog" not in ss:
    ss._want_open_dialog = False           # sinaliza que, após o rerun, devemos abrir o modal
if "_outputs_editor_key_version" not in ss:
    ss._outputs_editor_key_version = 0     # força remontagem do editor (limpa checkboxes)

df_outputs, okO, msgO = load_outputs_public()
if not okO and msgO:
    st.caption(f"⚠️ {msgO}")
else:
    if df_outputs.empty:
        st.info("No outputs.")
    else:
        # Índice estável
        df_base = df_outputs.reset_index(drop=True).copy()

        preview_cols = ["project","output_country","output_city","output_type","output_data_type"]
        for c in preview_cols:
            if c not in df_base.columns:
                df_base[c] = ""

        # Dataframe de preview + coluna de ação
        df_preview = df_base[preview_cols].copy()
        details_col = "See full information"
        df_preview[details_col] = False

        # Key com versão para limpar seleção
        editor_key = f"outputs_editor_{ss._outputs_editor_key_version}"

        edited = st.data_editor(
            df_preview,
            key=editor_key,
            use_container_width=True,
            hide_index=True,
            disabled=preview_cols,  # só a coluna de ação é editável
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

        # Descobre qual linha foi marcada (primeira marcada)
        selected_idx_list = []
        if details_col in edited.columns:
            selected_idx_list = [i for i, v in enumerate(edited[details_col].tolist()) if bool(v)]

        # Se houve clique: salva índice, marca intenção de abrir, incrementa versão e rerun
        if selected_idx_list and not ss._want_open_dialog:
            ss._selected_output_idx = int(selected_idx_list[0])  # seleção exclusiva
            ss._want_open_dialog = True
            ss._outputs_editor_key_version += 1  # força limpar checkboxes no próximo render
            st.rerun()

        # Função para renderizar detalhes SEMPRE completos
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

        # Abre o pop-up após o rerun, com a tabela já limpa
        def _open_details(row):
            try:
                @st.dialog("Full information")
                def _dialog(rdict):
                    _render_full_info_md(rdict)
                _dialog(row.to_dict())
            except Exception:
                # Fallback inline se st.dialog não existir
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

        # Se devemos abrir (após a seleção e rerun), abre e desarma o flag
        if ss._want_open_dialog:
            idx = ss._selected_output_idx
            if isinstance(idx, int) and (0 <= idx < len(df_base)):
                row = df_base.iloc[idx]
                _open_details(row)
            # desarma para não reabrir em novos reruns (fechou no X = ok)
            ss._want_open_dialog = False
            ss._selected_output_idx = None

# ──────────────────────────────────────────────────────────────────────────────
# 9) SUBMISSÃO: somente OUTPUT (grava lat/lon + limpeza segura pós-submit)
# ──────────────────────────────────────────────────────────────────────────────

# --- helpers de reset ---
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
    "country_for_city", "output_countries", "city_list_output"
}

def _really_clear_output_form_state():
    """Apaga as chaves do formulário do session_state (modo seguro)."""
    for k in list(_FORM_KEYS):
        st.session_state.pop(k, None)
    st.session_state.pop("map_center", None)
    st.session_state["map_zoom"] = 2
    st.session_state["_clear_city_field_out"] = False
    st.session_state["_clear_city_field_newproj"] = False

# Flag de reset pendente (default)
if "_pending_form_reset" not in st.session_state:
    st.session_state._pending_form_reset = False

# ⚠️ Faça o reset **antes** de inicializar qualquer estado
if st.session_state._pending_form_reset:
    _really_clear_output_form_state()
    st.session_state._pending_form_reset = False

# ===== STATE INIT ===== (agora é seguro recriar padrões)
if "output_countries" not in st.session_state:
    st.session_state.output_countries = []
if "city_list_output" not in st.session_state:
    st.session_state.city_list_output = []
if "map_center" not in st.session_state:
    st.session_state.map_center = None
if "map_zoom" not in st.session_state:
    st.session_state.map_zoom = 2
if "_clear_city_field_out" not in st.session_state:
    st.session_state._clear_city_field_out = False
if "_clear_city_field_newproj" not in st.session_state:
    st.session_state._clear_city_field_newproj = False

st.markdown("---")
st.header("Submit Output (goes to review queue)")

with st.form("OUTPUT_FORM", clear_on_submit=False):
    submitter_email = st.text_input(
        "Submitter email (required for review)",
        key="submitter_email",
        placeholder="name@org.org"
    )

    # Projeto (taxonomia)
    project_tax_sel = st.selectbox(
        "Project Name (taxonomy)",
        options=PROJECT_TAXONOMY,
        key="project_tax_sel"
    )
    is_other_project = project_tax_sel.startswith("Other")
    project_tax_other = ""
    if is_other_project:
        project_tax_other = st.text_input("Please specify the project (taxonomy)", key="project_tax_other")

    # Novo projeto (se Other)
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
            if st.session_state._clear_city_field_newproj and "city_add_proj" in st.session_state:
                del st.session_state["city_add_proj"]
                st.session_state._clear_city_field_newproj = False
            city_input_proj = st.text_input(
                "City (accepts multiple, separated by commas)",
                key="city_add_proj"
            )
        with colc3:
            st.write("")
            if st.form_submit_button("➕ Add city to NEW project"):
                if selected_country_city and selected_country_city != SELECT_PLACEHOLDER and city_input_proj.strip():
                    for c in [x.strip() for x in city_input_proj.split(",") if x.strip()]:
                        pair = f"{selected_country_city} — {c}"
                        if pair not in st.session_state.city_list_output:
                            st.session_state.city_list_output.append(pair)
                    st.session_state._clear_city_field_newproj = True
                    st.rerun()
                else:
                    st.warning("Select a valid country and type a city.")

        if st.session_state.get("city_list_output"):
            st.caption("Cities added to NEW project:")
            for i, it in enumerate(st.session_state.city_list_output):
                c1, c2 = st.columns([6,1])
                with c1: st.write(f"- {it}")
                with c2:
                    if st.form_submit_button("Remove", key=f"rm_city_newproj_{i}"):
                        st.session_state.city_list_output.pop(i); st.rerun()
        if st.checkbox("Clear all cities", key="_clear_cities_flag"):
            st.session_state.city_list_output = []

        new_project_url = st.text_input("Project URL (optional)", key="new_project_url")
        new_project_contact = st.text_input("Project contact / institution (optional)", key="new_project_contact")

    # Tipo de output
    output_type_sel = st.selectbox("Output Type", options=OUTPUT_TYPES, key="output_type_sel")
    output_type_other = ""
    if output_type_sel.startswith("Other"):
        output_type_other = st.text_input("Please specify the output type", key="output_type_other")

    output_data_type = ""
    if output_type_sel == "Dataset":
        output_data_type = st.selectbox("Data type (for datasets)", options=DATASET_DTYPES, key="output_data_type")

    output_title = st.text_input("Output Name", key="output_title")
    output_url   = st.text_input("Output URL (optional)", key="output_url")

    # CORREÇÃO: Países de cobertura como multiselect
    st.markdown("**Geographic coverage of output**")
    countries_fixed = _countries_with_global_first(COUNTRY_NAMES) + ["Other: ______"]
    output_countries = st.multiselect(
        "Select one or more countries (select 'Global' for worldwide coverage)",
        options=countries_fixed,
        key="output_countries"
    )
    
    # Lógica para Global vs países específicos
    is_global = "Global" in output_countries
    if is_global:
        st.info("Global coverage selected - city fields will be disabled")
        # Limpa outros países se Global for selecionado
        if len(output_countries) > 1:
            st.session_state.output_countries = ["Global"]
            st.rerun()
    
    output_country_other = ""
    if "Other: ______" in output_countries:
        output_country_other = st.text_input("Please specify other geographic coverage", key="output_country_other")

    # Cities covered (OUTPUT) - só habilitado se não for Global
    st.markdown("**Cities covered**")
    
    # Prepara países disponíveis para seleção de cidades (exclui Global e Other)
    available_countries_for_cities = [c for c in output_countries if c not in ["Global", "Other: ______"]]
    
    colx1, colx2, colx3 = st.columns([2,2,1])
    with colx1:
        country_for_city = st.selectbox(
            "Country for the city",
            options=[SELECT_PLACEHOLDER] + available_countries_for_cities,
            index=0,
            key="country_for_city",
            disabled=is_global or not available_countries_for_cities
        )
    with colx2:
        if st.session_state._clear_city_field_out and "output_city_dummy" in st.session_state:
            del st.session_state["output_city_dummy"]
            st.session_state._clear_city_field_out = False
        city_input_out = st.text_input(
            "City (accepts multiple, separated by commas)",
            key="output_city_dummy",
            disabled=is_global
        )
    with colx3:
        st.write("")
        add_city_disabled = is_global or not country_for_city or country_for_city == SELECT_PLACEHOLDER or not city_input_out.strip()
        if st.form_submit_button("➕ Add city to OUTPUT", disabled=add_city_disabled):
            if not is_global and country_for_city and country_for_city != SELECT_PLACEHOLDER and city_input_out.strip():
                for c in [x.strip() for x in city_input_out.split(",") if x.strip()]:
                    pair = f"{country_for_city} — {c}"
                    if pair not in st.session_state.city_list_output:
                        st.session_state.city_list_output.append(pair)
                st.session_state._clear_city_field_out = True
                st.rerun()
            elif is_global:
                st.warning("Cannot add cities for global coverage")
            else:
                st.warning("Choose a valid country and type a city.")

    if st.session_state.get("city_list_output") and not is_global:
        st.caption("Cities added to OUTPUT:")
        for i, it in enumerate(st.session_state.city_list_output):
            c1, c2 = st.columns([6,1])
            with c1: st.write(f"- {it}")
            with c2:
                if st.form_submit_button("Remove", key=f"rm_city_out_{i}"):
                    st.session_state.city_list_output.pop(i); st.rerun()

    # Mapa de previsualização - só se não for Global
    if not is_global and available_countries_for_cities:
        first_country = available_countries_for_cities[0]
        if first_country in COUNTRY_CENTER_FULL:
            st.session_state.map_center = COUNTRY_CENTER_FULL[first_country]
            st.session_state.map_zoom = 4
            
        if st.session_state.get("map_center"):
            m = folium.Map(
                location=st.session_state.map_center,
                zoom_start=st.session_state.map_zoom,
                tiles="CartoDB positron"
            )
            # Marca o centro do primeiro país
            folium.CircleMarker(
                location=st.session_state.map_center, radius=6, color="#2563eb",
                fill=True, fill_opacity=0.9, tooltip=f"{first_country}"
            ).add_to(m)
            
            # Adiciona marcadores para cidades
            for pair in st.session_state.get("city_list_output", []):
                if "—" in pair:
                    ctry, cty = [p.strip() for p in pair.split("—",1)]
                    latlon = COUNTRY_CENTER_FULL.get(ctry)
                    if latlon:
                        folium.Marker(location=latlon, tooltip=f"{cty} ({ctry})").add_to(m)
            st_folium(m, height=320, width=None)
    elif is_global:
        st.info("Map preview not available for global coverage")

    # Anos (desc)
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
        # validações
        if not submitter_email.strip():
            st.warning("Please provide the submitter email."); st.stop()
        if not output_title.strip():
            st.warning("Please provide the Output Name."); st.stop()
        if not output_countries:
            st.warning("Please select at least one country for geographic coverage."); st.stop()
        if is_other_project and not (st.session_state.get("city_list_output") or st.session_state.get("new_project_countries")):
            st.warning("For a new project (Other), please add at least one country/city."); st.stop()

        # 1) Se "Other", registrar projeto (fila)
        if is_other_project:
            wsP, errP = ws_projects()
            if errP or wsP is None:
                st.error(errP or "Worksheet unavailable for projects."); st.stop()
            pairsP = st.session_state.get("city_list_output", [])[:] or [
                f"{c} — " for c in (st.session_state.get("new_project_countries") or [])
            ]
            ok_allP, msg_anyP = True, None
            for pair in pairsP:
                if "—" not in pair: continue
                country, city = [p.strip() for p in pair.split("—",1)]
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

        # 2) Grava o output (fila) com lat/lon
        wsO, errO = ws_outputs()
        if errO or wsO is None:
            st.error(errO or "Worksheet unavailable for outputs."); st.stop()

        # coords - usa o primeiro país selecionado (exceto se for Global)
        lat_o, lon_o = (None, None)
        if not is_global and available_countries_for_cities:
            first_country = available_countries_for_cities[0]
            if first_country in COUNTRY_CENTER_FULL:
                lat_o, lon_o = COUNTRY_CENTER_FULL[first_country]

        output_cities_str = ", ".join(st.session_state.get("city_list_output", [])) if st.session_state.get("city_list_output") else ""

        # Prepara string de países para armazenamento
        output_countries_str = ", ".join(output_countries)

        rowO = {
            "project": (project_tax_other.strip() if is_other_project else project_tax_sel),
            "output_title": output_title,
            "output_type": ("" if output_type_sel.startswith("Other") else output_type_sel),
            "output_type_other": (output_type_other if output_type_sel.startswith("Other") else ""),
            "output_data_type": (output_data_type if (output_type_sel=="Dataset") else ""),
            "output_url": output_url,
            "output_country": output_countries_str,
            "output_country_other": (output_country_other if "Other: ______" in output_countries else ""),
            "output_city": output_cities_str,
            "output_year": final_years_str,
            "output_desc": output_desc,
            "output_contact": output_contact,
            "output_email": "",  # legado vazio
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
            st.success("✅ Output submission queued")
            # Limpeza completa do estado do formulário
            _really_clear_output_form_state()
            st.session_state._pending_form_reset = True
            st.rerun()
        else:
            st.error(f"⚠️ {msgO2}")
