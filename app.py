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
# 6) Carregamento (apenas aprovados para exibição pública)
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
# 7) Mapa (APENAS outputs aprovados)
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
# 9) SUBMISSÃO DE OUTPUT (Global permite cidades + múltiplas linhas)
# ──────────────────────────────────────────────────────────────────────────────
st.markdown("---")
st.header("Submit Output (goes to review queue)")

# ===== Estado/Init =====
if "city_list_output" not in st.session_state:
    st.session_state.city_list_output = []
if "_clear_city_field_out" not in st.session_state:
    st.session_state._clear_city_field_out = False
if "output_countries" not in st.session_state:
    st.session_state.output_countries = []

def add_city(country, city_csv):
    """Adiciona 1+ cidades (separadas por vírgula) para um país."""
    if country and country != SELECT_PLACEHOLDER and city_csv.strip():
        for c in [x.strip() for x in city_csv.split(",") if x.strip()]:
            pair = f"{country} — {c}"
            if pair not in st.session_state.city_list_output:
                st.session_state.city_list_output.append(pair)
        return True
    return False

def remove_city(i):
    if 0 <= i < len(st.session_state.city_list_output):
        st.session_state.city_list_output.pop(i)

def clear_form():
    st.session_state.city_list_output = []
    st.session_state._clear_city_field_out = False
    st.session_state.output_countries = []

with st.form("output_form", clear_on_submit=False):
    st.subheader("Basic Information")

    submitter_email = st.text_input(
        "Submitter email (required for review)*",
        placeholder="name@org.org"
    )

    project_tax_sel = st.selectbox(
        "Project Name (taxonomy)*",
        options=PROJECT_TAXONOMY
    )
    is_other_project = project_tax_sel.startswith("Other")
    project_tax_other = st.text_input("Please specify the project (taxonomy)*") if is_other_project else ""

    output_type_sel = st.selectbox("Output Type*", options=OUTPUT_TYPES)

    # >>> Data type travado e salvo em branco quando Output Type = Dataset
    if output_type_sel == "Dataset":
        st.selectbox(
            "Data type (for datasets) — locked for Dataset",
            options=[SELECT_PLACEHOLDER] + DATASET_DTYPES,
            index=0,
            disabled=True,
            key="output_data_type_locked"
        )
        output_data_type = ""  # gravar em branco quando travado
    else:
        output_data_type = st.selectbox(
            "Data type (for datasets)",
            options=[""] + DATASET_DTYPES
        )

    output_type_other = st.text_input("Please specify the output type*") if output_type_sel.startswith("Other") else ""

    output_title = st.text_input("Output Name*")
    output_url   = st.text_input("Output URL (optional)")

    # ── Geographic Coverage (vem logo após Output URL) ──
    st.subheader("Geographic Coverage")
    st.multiselect(
        "Select countries (select 'Global' for worldwide coverage)*",
        options=_countries_with_global_first(COUNTRY_NAMES) + ["Other: ______"],
        key="output_countries"
    )
    selected_countries = st.session_state.output_countries[:]
    is_global = "Global" in selected_countries

    output_country_other = ""
    if "Other: ______" in selected_countries:
        output_country_other = st.text_input("Please specify other geographic coverage")

    # ── Cidades (sempre disponível; mesmo com Global) ──
    st.write("**Add cities for this output (optional):**")

    # Lista de países disponíveis para o seletor de cidade:
    # 1) Se houver países reais selecionados → use-os (ignora Global/Other)
    # 2) Se só Global estiver selecionado → ofereça TODOS os países (COUNTRY_NAMES)
    selected_real = [c for c in selected_countries if c not in ["Global", "Other: ______"]]
    if selected_real:
        city_country_choices = [SELECT_PLACEHOLDER] + selected_real
    else:
        # Só Global (ou nada): permita escolher qualquer país para vincular a cidade
        city_country_choices = [SELECT_PLACEHOLDER] + COUNTRY_NAMES

    c1, c2, c3 = st.columns([2, 2, 1])
    with c1:
        city_country_select = st.selectbox(
            "Country for the city",
            options=city_country_choices,
            key="city_country_select"
        )
    with c2:
        if st.session_state._clear_city_field_out and "output_city_input" in st.session_state:
            del st.session_state["output_city_input"]
            st.session_state._clear_city_field_out = False
        output_city_input = st.text_input(
            "City name (you can paste multiple, comma-separated)",
            placeholder="e.g., Lagos, Kano",
            key="output_city_input"
        )
    with c3:
        st.write(""); st.write("")
        if st.form_submit_button("➕ Add City", use_container_width=True, key="add_city_btn"):
            if add_city(city_country_select, output_city_input):
                st.session_state._clear_city_field_out = True
                st.rerun()

    # Lista de cidades adicionadas
    if st.session_state.city_list_output:
        st.write("**Added cities:**")
        for i, pair in enumerate(st.session_state.city_list_output):
            c1, c2 = st.columns([4,1])
            with c1:
                st.write(f"📍 {pair}")
            with c2:
                if st.form_submit_button("🗑️ Remove", key=f"rm_city_{i}"):
                    remove_city(i); st.rerun()

    # ── Additional info ──
    st.subheader("Additional Information")
    current_year = datetime.utcnow().year
    base_years_desc = list(range(current_year, 1999, -1))
    years_selected = st.multiselect("Year of output release", base_years_desc)

    output_desc = st.text_area("Short description of output")
    output_contact = st.text_input("Name & institution of person responsible")
    output_linkedin = st.text_input("LinkedIn address of contact")
    project_url_for_output = st.text_input("Project URL (optional, if different)")

    c1, c2 = st.columns([1,1])
    with c1:
        submitted = st.form_submit_button("✅ Submit for Review", use_container_width=True, type="primary")
    with c2:
        if st.form_submit_button("🗑️ Clear Form", use_container_width=True, type="secondary"):
            clear_form(); st.rerun()

# ──────────────────────────────────────────────────────────────────────────────
# Processamento do envio (Global com cidades permitido)
# ──────────────────────────────────────────────────────────────────────────────
if submitted:
    errors = []
    if not submitter_email.strip():
        errors.append("❌ Submitter email is required")
    if not output_title.strip():
        errors.append("❌ Output name is required")
    if not st.session_state.output_countries:
        errors.append("❌ At least one country must be selected (Global counts)")
    # Data type já travado/“” quando Dataset; sem validação extra.

    if is_other_project and not project_tax_other.strip():
        errors.append("❌ Project name is required when selecting 'Other'")

    if errors:
        for e in errors: st.error(e)
        st.stop()

    try:
        wsO, errO = ws_outputs()
        if errO or wsO is None:
            st.error(errO or "Worksheet unavailable for outputs."); st.stop()

        # anos
        final_years_sorted_desc = sorted(set(years_selected), reverse=True)
        final_years_str = ",".join(str(y) for y in final_years_sorted_desc) if final_years_sorted_desc else ""

        # Helper para gravar uma linha
        def save_row(country, city, lat, lon):
            rowO = {
                "project": (project_tax_other.strip() if is_other_project else project_tax_sel),
                "output_title": output_title,
                "output_type": ("" if output_type_sel.startswith("Other") else output_type_sel),
                "output_type_other": (output_type_other if output_type_sel.startswith("Other") else ""),
                "output_data_type": output_data_type,  # "" se Dataset
                "output_url": output_url,
                "output_country": country,
                "output_country_other": (output_country_other if "Other: ______" in st.session_state.output_countries else ""),
                "output_city": city or "",
                "output_year": final_years_str,
                "output_desc": output_desc,
                "output_contact": output_contact,
                "output_email": "",
                "output_linkedin": output_linkedin,
                "project_url": project_url_for_output or "",
                "submitter_email": submitter_email,
                "is_edit": "FALSE","edit_target":"","edit_request":"New submission",
                "approved": "FALSE",
                "created_at": datetime.utcnow().isoformat(timespec="seconds")+"Z",
                "lat": lat if lat is not None else "",
                "lon": lon if lon is not None else "",
            }
            ok, msg = _append_row(wsO, OUTPUTS_HEADERS, rowO)
            if not ok:
                st.error(f"⚠️ {msg}"); st.stop()

        # Lógica de gravação:
        selected_countries = st.session_state.output_countries[:]
        has_cities = len(st.session_state.city_list_output) > 0

        if "Global" in selected_countries:
            if has_cities:
                # Global + cidades → grava por cidade (sem linha extra Global)
                for pair in st.session_state.city_list_output:
                    if "—" not in pair: continue
                    ctry, cty = [p.strip() for p in pair.split("—", 1)]
                    if ctry == "Other: ______": continue
                    lat_o, lon_o = COUNTRY_CENTER_FULL.get(ctry, (None, None))
                    save_row(ctry, cty, lat_o, lon_o)
            else:
                # Só Global, sem cidades → uma linha Global
                save_row("Global", "", None, None)
        else:
            # Sem Global → se houver cidades, grava por cidade; senão, por país
            if has_cities:
                for pair in st.session_state.city_list_output:
                    if "—" not in pair: continue
                    ctry, cty = [p.strip() for p in pair.split("—", 1)]
                    if ctry in ["Global", "Other: ______"]: continue
                    lat_o, lon_o = COUNTRY_CENTER_FULL.get(ctry, (None, None))
                    save_row(ctry, cty, lat_o, lon_o)
            else:
                for ctry in [c for c in selected_countries if c not in ["Global","Other: ______"]]:
                    lat_o, lon_o = COUNTRY_CENTER_FULL.get(ctry, (None, None))
                    save_row(ctry, "", lat_o, lon_o)

        st.success("✅ Output submission queued for review!")
        clear_form()
        st.rerun()

    except Exception as e:
        st.error(f"An error occurred: {e}")


