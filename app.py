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
# 6) Leitura de aprovados (para mapa/tabela)
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
        center_lat = dfc["lat"].mean() if not dfc.empty else 0
        center_lon = dfc["lon"].mean() if not dfc.empty else 0
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
# 8) Tabela de outputs aprovados
# ──────────────────────────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("Browse outputs (approved only)")

ss = st.session_state
if "_selected_output_idx" not in ss: ss._selected_output_idx = None
if "_want_open_dialog"  not in ss: ss._want_open_dialog  = False
if "_outputs_editor_key_version" not in ss: ss._outputs_editor_key_version = 0

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
            if c not in df_base.columns: df_base[c] = ""
        df_preview = df_base[preview_cols].copy()
        details_col = "See full information"
        df_preview[details_col] = False
        editor_key = f"outputs_editor_{ss._outputs_editor_key_version}"
        edited = st.data_editor(
            df_preview, key=editor_key, use_container_width=True, hide_index=True,
            disabled=preview_cols,
            column_config={details_col: st.column_config.CheckboxColumn(details_col, help="Open details")}
        )
        selected_idx_list = []
        if details_col in edited.columns:
            selected_idx_list = [i for i,v in enumerate(edited[details_col].tolist()) if bool(v)]
        if selected_idx_list and not ss._want_open_dialog:
            ss._selected_output_idx = int(selected_idx_list[0])
            ss._want_open_dialog = True
            ss._outputs_editor_key_version += 1
            st.rerun()
        def _render_full_info_md(row):
            show_cols = [
                ("project","Project"), ("project_url","Project URL"),
                ("output_title","Output title"), ("output_type","Output type"),
                ("output_data_type","Output data type"), ("output_url","Output URL"),
                ("output_country","Output country"), ("output_city","Output city"),
                ("output_year","Output year"), ("output_desc","Description"),
                ("output_contact","Contact"), ("output_linkedin","LinkedIn"),
            ]
            lines = []
            for key, nice in show_cols:
                val = str(row.get(key,"")).strip()
                if key in ("project_url","output_url") and val:
                    val = f"[{val}]({val})"
                lines.append(f"- **{nice}:** {val if val else '—'}")
            st.markdown("\n".join(lines))
        if ss._want_open_dialog:
            idx = ss._selected_output_idx
            if isinstance(idx,int) and (0 <= idx < len(df_base)):
                _render_full_info_md(df_base.iloc[idx])
            ss._want_open_dialog = False
            ss._selected_output_idx = None

# ──────────────────────────────────────────────────────────────────────────────
# 9) SUBMISSÃO DE OUTPUT — fora do form: Coverage + Cities (dinâmicos)
# ──────────────────────────────────────────────────────────────────────────────
st.markdown("---")
st.header("Submit Output (goes to review queue)")

# Estado para coverage/cities
if "output_countries"  not in ss: ss.output_countries  = []
if "city_list_output"  not in ss: ss.city_list_output  = []
if "is_global_sel"     not in ss: ss.is_global_sel     = False

st.subheader("Geographic Coverage")
countries_fixed = _countries_with_global_first(COUNTRY_NAMES) + ["Other: ______"]
ss.output_countries = st.multiselect(
    "Select countries (select 'Global' for worldwide coverage)*",
    options=countries_fixed,
    default=ss.output_countries
)

# lógica Global
ss.is_global_sel = "Global" in ss.output_countries
if ss.is_global_sel and ss.city_list_output:
    # se virou Global, limpa cidades para não enviar lixo
    ss.city_list_output = []

# Campo "Other"
output_country_other = ""
if "Other: ______" in ss.output_countries:
    output_country_other = st.text_input("Please specify other geographic coverage")

# Cities covered (aparece imediatamente quando há países e não é Global)
if ss.output_countries and not ss.is_global_sel:
    st.subheader("Cities covered")
    available_countries_for_cities = [c for c in ss.output_countries if c not in ["Global","Other: ______"]]
    col1, col2, col3 = st.columns([2,2,1])
    with col1:
        sel_country_for_city = st.selectbox(
            "Country for the city",
            options=[SELECT_PLACEHOLDER] + available_countries_for_cities,
            index=0,
            key="country_for_city"
        )
    with col2:
        city_input = st.text_input(
            "City (accepts multiple, separated by commas)",
            key="output_city_input"
        )
    with col3:
        st.write("")
        if st.button("➕ Add city to OUTPUT", use_container_width=True):
            if sel_country_for_city and sel_country_for_city != SELECT_PLACEHOLDER and city_input.strip():
                for c in [x.strip() for x in city_input.split(",") if x.strip()]:
                    pair = f"{sel_country_for_city} — {c}"
                    if pair not in ss.city_list_output:
                        ss.city_list_output.append(pair)
                st.session_state.output_city_input = ""  # limpa campo
            else:
                st.warning("Choose a valid country and type a city.")

    if ss.city_list_output:
        st.caption("Cities added to OUTPUT:")
        for i, it in enumerate(ss.city_list_output):
            c1, c2 = st.columns([6,1])
            with c1: st.write(f"- {it}")
            with c2:
                if st.button("Remove", key=f"rm_city_out_{i}"):
                    ss.city_list_output.pop(i); st.experimental_rerun()
else:
    st.info("🌍 Global selected OR no country yet — city fields are disabled.")

# Preview map (países marcados; cidades usam centro do país)
if (not ss.is_global_sel) and ss.output_countries:
    avail = [c for c in ss.output_countries if c not in ["Global","Other: ______"]]
    if avail:
        center = COUNTRY_CENTER_FULL.get(avail[0], (0,0))
        m = folium.Map(location=center, zoom_start=3, tiles="CartoDB positron")
        for country in avail:
            if country in COUNTRY_CENTER_FULL:
                folium.CircleMarker(
                    location=COUNTRY_CENTER_FULL[country],
                    radius=8, color="#2563eb", fill=True, fill_opacity=0.9,
                    tooltip=country
                ).add_to(m)
        for pair in ss.city_list_output:
            if "—" in pair:
                ctry, cty = [p.strip() for p in pair.split("—",1)]
                latlon = COUNTRY_CENTER_FULL.get(ctry)
                if latlon:
                    folium.Marker(location=latlon, tooltip=f"{cty} ({ctry})").add_to(m)
        st_folium(m, height=320, width=None)

# ──────────────────────────────────────────────────────────────────────────────
# 10) Demais campos + submit (em form)
# ──────────────────────────────────────────────────────────────────────────────
with st.form("OUTPUT_FORM_STATIC", clear_on_submit=False):
    st.subheader("Basic information")
    submitter_email = st.text_input("Submitter email (required for review)*", placeholder="name@org.org")
    project_tax_sel = st.selectbox("Project Name (taxonomy)*", options=PROJECT_TAXONOMY, key="project_tax_sel")
    is_other_project = project_tax_sel.startswith("Other")
    project_tax_other = st.text_input("Please specify the project (taxonomy)*") if is_other_project else ""

    output_type_sel = st.selectbox("Output Type*", options=OUTPUT_TYPES, key="output_type_sel")

    # ATENÇÃO à regra solicitada:
    # Se Output Type = Dataset ⇒ travar Data type e salvar em branco
    if output_type_sel == "Dataset":
        st.text_input("Data type (for datasets) — locked (saved blank)", value="", disabled=True, key="output_data_type_locked")
        output_data_type = ""  # salva em branco
    else:
        output_data_type = st.selectbox("Data type (optional)", options=[""] + DATASET_DTYPES, key="output_data_type_free")

    output_type_other = st.text_input("Please specify the output type*") if output_type_sel.startswith("Other") else ""

    output_title = st.text_input("Output Name*")
    output_url   = st.text_input("Output URL (optional)")

    st.subheader("Additional Information")
    current_year = datetime.utcnow().year
    base_years_desc = list(range(current_year, 1999, -1))
    years_selected = st.multiselect("Year of output release", base_years_desc, key="years_selected")
    output_desc = st.text_area("Short description of output")
    output_contact = st.text_input("Name & institution of person responsible")
    output_linkedin = st.text_input("LinkedIn address of contact")
    project_url_for_output = st.text_input("Project URL (optional, if different)")

    colA, colB = st.columns([1,1])
    with colA:
        submitted = st.form_submit_button("✅ Submit for Review", type="primary", use_container_width=True)
    with colB:
        if st.form_submit_button("🗑️ Clear Form", use_container_width=True):
            # reset seguro
            for k in ["output_countries","city_list_output","country_for_city","output_city_input",
                      "project_tax_sel","output_type_sel","output_data_type_free","output_data_type_locked",
                      "years_selected"]:
                st.session_state.pop(k, None)
            st.experimental_rerun()

# ──────────────────────────────────────────────────────────────────────────────
# 11) Processar submissão
# ──────────────────────────────────────────────────────────────────────────────
if submitted:
    errors = []
    if not submitter_email.strip(): errors.append("❌ Submitter email is required")
    if not output_title.strip():    errors.append("❌ Output name is required")
    if not ss.output_countries:     errors.append("❌ Select at least one country (or Global)")
    if is_other_project and not project_tax_other.strip():
        errors.append("❌ Please specify the project when selecting 'Other'")
    if errors:
        for e in errors: st.error(e)
        st.stop()

    # destino
    wsO, errO = ws_outputs()
    if errO or wsO is None:
        st.error(errO or "Worksheet unavailable for outputs."); st.stop()

    # anos
    years_desc = sorted(set(st.session_state.get("years_selected", [])), reverse=True)
    years_str = ",".join(str(y) for y in years_desc) if years_desc else ""

    # Se existirem cidades adicionadas → uma linha por cidade; senão, por país
    rows_to_write: List[dict] = []

    if ss.city_list_output:
        for pair in ss.city_list_output:
            if "—" not in pair: continue
            ctry, cty = [p.strip() for p in pair.split("—",1)]
            lat_o, lon_o = COUNTRY_CENTER_FULL.get(ctry, (None, None))
            rows_to_write.append({
                "project": (project_tax_other.strip() if is_other_project else project_tax_sel),
                "output_title": output_title,
                "output_type": ("" if output_type_sel.startswith("Other") else output_type_sel),
                "output_type_other": (output_type_other if output_type_sel.startswith("Other") else ""),
                "output_data_type": output_data_type,   # pode ser "" (travado)
                "output_url": output_url,
                "output_country": ctry,
                "output_country_other": (output_country_other if "Other: ______" in ss.output_countries else ""),
                "output_city": cty,
                "output_year": years_str,
                "output_desc": output_desc,
                "output_contact": output_contact,
                "output_email": "",
                "output_linkedin": output_linkedin,
                "project_url": project_url_for_output,
                "submitter_email": submitter_email,
                "is_edit": "FALSE","edit_target":"","edit_request":"New submission",
                "approved": "FALSE",
                "created_at": datetime.utcnow().isoformat(timespec="seconds")+"Z",
                "lat": lat_o if lat_o is not None else "",
                "lon": lon_o if lon_o is not None else "",
            })
    else:
        for ctry in ss.output_countries:
            if ctry in ["Global","Other: ______"]: continue
            lat_o, lon_o = COUNTRY_CENTER_FULL.get(ctry, (None, None))
            rows_to_write.append({
                "project": (project_tax_other.strip() if is_other_project else project_tax_sel),
                "output_title": output_title,
                "output_type": ("" if output_type_sel.startswith("Other") else output_type_sel),
                "output_type_other": (output_type_other if output_type_sel.startswith("Other") else ""),
                "output_data_type": output_data_type,
                "output_url": output_url,
                "output_country": ctry,
                "output_country_other": (output_country_other if "Other: ______" in ss.output_countries else ""),
                "output_city": "",
                "output_year": years_str,
                "output_desc": output_desc,
                "output_contact": output_contact,
                "output_email": "",
                "output_linkedin": output_linkedin,
                "project_url": project_url_for_output,
                "submitter_email": submitter_email,
                "is_edit": "FALSE","edit_target":"","edit_request":"New submission",
                "approved": "FALSE",
                "created_at": datetime.utcnow().isoformat(timespec="seconds")+"Z",
                "lat": lat_o if lat_o is not None else "",
                "lon": lon_o if lon_o is not None else "",
            })

    # gravação
    ok_all, msg_any = True, None
    for row in rows_to_write:
        ok, msg = _append_row(wsO, OUTPUTS_HEADERS, row)
        ok_all &= ok; msg_any = msg
    if ok_all:
        st.success(f"✅ {len(rows_to_write)} row(s) queued for review")
        # reset seguro pós-submit
        for k in ["output_countries","city_list_output","country_for_city","output_city_input",
                  "project_tax_sel","output_type_sel","output_data_type_free","output_data_type_locked",
                  "years_selected","is_global_sel"]:
            st.session_state.pop(k, None)
        st.experimental_rerun()
    else:
        st.error(f"⚠️ {msg_any}")
