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
# 0.1) ESTADO GLOBAL / FLASH / POPUP
# ──────────────────────────────────────────────────────────────────────────────
ss = st.session_state
for k, v in {
    "_flash": None,
    "_post_submit": False,
    "_post_submit_msg": "",
    "_form_version": 1,
    "form_data": {"cities": []},
    "_edit_mode": False,
    "_edit_reason": "",
    "_edit_target_row": None,
    "_selected_output_idx": None,
    "_want_open_dialog": False,
    "_outputs_editor_key_version": 0,
    "_table_selection": None,
    "_action_reason": "",
}.items():
    if k not in ss:
        ss[k] = v

def wkey(name: str) -> str:
    return f"{name}__v{ss._form_version}"

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

def show_post_submit_dialog():
    if ss.get("_post_submit", False):
        try:
            @st.dialog("Submission received")
            def _dlg():
                st.success(ss.get("_post_submit_msg") or "✅ Output submission queued for review!")
                st.caption("Your submission is now in the review queue.")
                if st.button("OK", type="primary"):
                    ss._post_submit = False
                    ss._post_submit_msg = ""
                    st.rerun()
            _dlg()
        except Exception:
            with st.container(border=True):
                st.success(ss.get("_post_submit_msg") or "✅ Output submission queued for review!")
                if st.button("OK", type="primary"):
                    ss._post_submit = False
                    ss._post_submit_msg = ""
                    st.rerun()

show_flash()
show_post_submit_dialog()

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

# ===== Validations =====
def _missing_list_to_md(missing: list[str]) -> str:
    if not missing:
        return ""
    items = "\n".join([f"- {m}" for m in missing])
    return f"**Please complete the following before continuing:**\n\n{items}"

def _show_missing(missing: list[str]):
    if missing:
        st.warning(_missing_list_to_md(missing))

def _collect_missing_for_submit(state: dict, *, is_edit_mode: bool, cities: list[str]) -> list[str]:
    missing = []
    if not (state.get("submitter_email") or "").strip():
        missing.append("Submitter email")
    if not (state.get("output_title") or "").strip():
        missing.append("Output name")
    if not (state.get("output_countries") or []):
        missing.append("At least one country")
    if (state.get("output_type_sel") == "Dataset") and (state.get("output_data_type") in (None, "", SELECT_PLACEHOLDER)):
        missing.append("Data type (for datasets)")
    if (state.get("project_tax_sel") or "").startswith("Other") and not (state.get("project_tax_other") or "").strip():
        missing.append("Project name (when selecting 'Other')")
    if is_edit_mode:
        if not (ss.get("_edit_target_row") or None):
            missing.append("Select a row to edit (use 'Edit selected' above the form)")
        if not (ss.get("_edit_reason") or "").strip():
            missing.append("Reason for edit (provided when you clicked 'Edit selected')")
    return missing

def _collect_missing_for_table_action(selection_idx: int | None, reason: str, action_label: str) -> list[str]:
    missing = []
    if selection_idx is None:
        missing.append("Select one row in the table")
    if not (reason or "").strip():
        missing.append(f"Reason for {action_label.lower()}")
    return missing

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
    if err or ws is None:
        return pd.DataFrame(), False, err
    try:
        vals = ws.get_all_values()
        if not vals or len(vals) < 2:
            return pd.DataFrame(), True, None
        header = vals[0]
        rows = vals[1:]
        recs = []
        for i, r in enumerate(rows, start=2):  # sheet row index (header is 1)
            data = {h: (r[j] if j < len(r) else "") for j, h in enumerate(header)}
            data["sheet_row"] = i
            recs.append(data)
        df = pd.DataFrame(recs)

        for c in OUTPUTS_HEADERS:
            if c not in df.columns:
                df[c] = ""

        df["approved"] = df["approved"].astype(str).str.upper().isin(["TRUE","1","YES"])
        df = df[df["approved"]].copy()

        df["lat"] = df.get("lat", "").apply(_as_float)
        df["lon"] = df.get("lon", "").apply(_as_float)

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
# 8) Browse outputs — agregada por “mesmo output” (difere só em cidades)
# ──────────────────────────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("Browse outputs (approved only)")

def _normalize_city_list(city_cell: str) -> list[str]:
    """
    Recebe uma célula de 'output_city' (ex: 'Nigeria — Lagos, Nigeria — Abuja')
    e retorna lista de tokens únicos limpos, preservando 'País — Cidade' quando vier assim.
    """
    if not city_cell:
        return []
    parts = [p.strip() for p in city_cell.split(",") if p.strip()]
    uniq = []
    seen = set()
    for p in parts:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq

def _aggregate_outputs(df_raw: pd.DataFrame) -> pd.DataFrame:
    """
    Agrega linhas com todos os campos iguais exceto output_city.
    Mantém lista de sheet_rows originais em 'sheet_rows' (para Edit/Remove).
    """
    if df_raw.empty:
        return df_raw.copy()

    # Campos usados para comparar "mesmo output" (tudo, exceto cidade/coords/sheet_row)
    ignore_cols = {"output_city", "sheet_row", "lat", "lon"}
    base_cols = [c for c in df_raw.columns if c not in ignore_cols]

    # Para segurança, limitamos a um subconjunto que de fato define o output,
    # mas mantendo o que você precisa para ver na UI e para prepopular o form:
    key_cols = [
        "project","output_title","output_type","output_type_other","output_data_type",
        "output_url","output_country","output_country_other","output_year",
        "output_desc","output_contact","output_linkedin","project_url","submitter_email",
        "is_edit","edit_target","edit_request","approved","created_at"
    ]
    # Garante que existem:
    key_cols = [c for c in key_cols if c in df_raw.columns]
    # Se faltar algum (ex: planilha antiga), completa com vazio:
    for c in key_cols:
        if c not in df_raw.columns:
            df_raw[c] = ""

    # Monta chave textual estável para agrupar
    def _row_key(row):
        return tuple((row.get(c, "") or "") for c in key_cols)

    groups = {}
    for _, r in df_raw.iterrows():
        k = _row_key(r)
        entry = groups.get(k)
        cities_here = _normalize_city_list(r.get("output_city",""))
        if entry is None:
            groups[k] = {
                "row_proto": {c: r.get(c, "") for c in key_cols},
                "cities": cities_here[:],
                "sheet_rows": [int(r.get("sheet_row")) if str(r.get("sheet_row")).isdigit() else None],
            }
        else:
            # agrega cidades
            for c in cities_here:
                if c not in entry["cities"]:
                    entry["cities"].append(c)
            # agrega sheet_row
            sr = int(r.get("sheet_row")) if str(r.get("sheet_row")).isdigit() else None
            if sr not in entry["sheet_rows"]:
                entry["sheet_rows"].append(sr)

    # Constrói DataFrame agregado
    out_records = []
    for k, bundle in groups.items():
        rec = dict(bundle["row_proto"])
        # cidades unificadas (ordena alfabeticamente para estabilidade)
        cities_sorted = sorted([c for c in bundle["cities"] if c], key=lambda x: x.lower())
        rec["output_city"] = ", ".join(cities_sorted)
        # mantém lat/lon como vazio (não exibimos no browser); para editar vamos usar países mesmo
        rec["sheet_rows"] = [sr for sr in bundle["sheet_rows"] if sr]
        out_records.append(rec)

    df_ag = pd.DataFrame(out_records)
    # Ordena por projeto / output_title
    sort_cols = [c for c in ["project","output_title","output_type","output_country"] if c in df_ag.columns]
    if sort_cols:
        df_ag = df_ag.sort_values(sort_cols, kind="stable").reset_index(drop=True)
    return df_ag

df_outputs, okO, msgO = load_outputs_public()
if not okO and msgO:
    st.caption(f"⚠️ {msgO}")
else:
    if df_outputs.empty:
        st.info("No outputs.")
    else:
        # Agrega (linhas iguais exceto cidades)
        df_aggr = _aggregate_outputs(df_outputs)

        # Prepara preview: SEM sheet_row
        preview_cols = ["project","output_country","output_city","output_type","output_data_type"]
        for c in preview_cols:
            if c not in df_aggr.columns:
                df_aggr[c] = ""

        df_preview = df_aggr[preview_cols].copy()

        details_col = "See full information"
        SELECT_COL  = "Select"
        df_preview[details_col] = False
        df_preview[SELECT_COL]  = False

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
                SELECT_COL:  st.column_config.CheckboxColumn(SELECT_COL, help="Select one row to edit/remove"),
            }
        )

        # detalhes
        if details_col in edited.columns:
            det_idx = [i for i, v in enumerate(edited[details_col].tolist()) if bool(v)]
            if det_idx and not ss._want_open_dialog:
                ss._selected_output_idx = int(det_idx[0])
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
                ("output_city","Output city (aggregated)"),
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
            if isinstance(idx, int) and (0 <= idx < len(df_aggr)):
                row = df_aggr.iloc[idx]
                _open_details(row)
            ss._want_open_dialog = False
            ss._selected_output_idx = None

        # seleção única (na agregada)
        sel_idxs = [i for i, v in enumerate(edited[SELECT_COL].tolist()) if bool(v)] if SELECT_COL in edited.columns else []
        if sel_idxs:
            ss._table_selection = int(sel_idxs[0])

        # Campo Reason + botões
        st.write("")  # espaçamento
        ss._action_reason = st.text_input("Reason (required for Edit or Remove)", value=ss._action_reason)
        colA, colB = st.columns([1,1])

        # ----- EDIT SELECTED (usa primeira sheet_row do grupo) -----
        with colA:
            if st.button("✏️ Edit selected", use_container_width=True):
                missing = _collect_missing_for_table_action(ss._table_selection, ss._action_reason, "Edit")
                if missing:
                    _show_missing(missing)
                else:
                    base_row = df_aggr.iloc[ss._table_selection].to_dict()
                    # pega a primeira linha original como alvo
                    sheet_rows = df_aggr.iloc[ss._table_selection].get("sheet_rows", [])
                    sheet_row = int(sheet_rows[0]) if sheet_rows else None

                    # Pré-popula o formulário com cidades agregadas
                    ss[wkey("submitter_email")] = ""  # quem edita informa seu e-mail
                    proj_name = (base_row.get("project") or "").strip()
                    ss[wkey("project_tax_sel")] = proj_name if proj_name in PROJECT_TAXONOMY else "Other: ______"
                    if ss[wkey("project_tax_sel")].startswith("Other"):
                        ss[wkey("project_tax_other")] = proj_name
                    ss[wkey("output_type_sel")] = (base_row.get("output_type") or "") or OUTPUT_TYPES[0]
                    ss[wkey("output_type_other")] = (base_row.get("output_type_other") or "")
                    if ss[wkey("output_type_sel")] == "Dataset":
                        ss[wkey("output_data_type")] = (base_row.get("output_data_type") or SELECT_PLACEHOLDER)
                    ss[wkey("output_title")] = (base_row.get("output_title") or "")
                    ss[wkey("output_url")]   = (base_row.get("output_url") or "")
                    # países (em agregada já estão unificados em 1 célula; mantemos como veio)
                    countries = []
                    oc = (base_row.get("output_country") or "").strip()
                    if oc:
                        parts = [p.strip() for p in oc.split(",") if p.strip()]
                        countries = parts if len(parts) > 1 else [oc]
                    ss[wkey("output_countries")] = countries
                    # cidades agregadas
                    ss.form_data["cities"] = []
                    ocity = (base_row.get("output_city") or "").strip()
                    if ocity:
                        for p in [p.strip() for p in ocity.split(",") if p.strip()]:
                            ss.form_data["cities"].append(p)
                    # anos
                    years_txt = (base_row.get("output_year") or "").strip()
                    years = []
                    if years_txt:
                        for y in years_txt.split(","):
                            y = y.strip()
                            if y.isdigit():
                                years.append(int(y))
                    ss[wkey("years_selected")] = years
                    # outros
                    ss[wkey("output_desc")]            = (base_row.get("output_desc") or "")
                    ss[wkey("output_contact")]         = (base_row.get("output_contact") or "")
                    ss[wkey("output_linkedin")]        = (base_row.get("output_linkedin") or "")
                    ss[wkey("project_url_for_output")] = (base_row.get("project_url") or "")
                    # flags de edição
                    ss["_edit_mode"]       = True
                    ss["_edit_reason"]     = ss._action_reason.strip()
                    ss["_edit_target_row"] = int(sheet_row) if sheet_row else None

                    flash("✏️ Edit mode enabled. The form below is pre-filled — complete your email and submit.", "info")
                    ss._outputs_editor_key_version += 1
                    ss._table_selection = None
                    ss._action_reason = ""
                    st.rerun()

        # ----- REMOVE SELECTED (gera 1 solicitação usando a primeira sheet_row) -----
        with colB:
            if st.button("🗑️ Remove selected", use_container_width=True):
                missing = _collect_missing_for_table_action(ss._table_selection, ss._action_reason, "Remove")
                if missing:
                    _show_missing(missing)
                else:
                    base_row = df_aggr.iloc[ss._table_selection].to_dict()
                    sheet_rows = df_aggr.iloc[ss._table_selection].get("sheet_rows", [])
                    sheet_row = int(sheet_rows[0]) if sheet_rows else None

                    wsO, errO = ws_outputs()
                    if errO or wsO is None:
                        st.error(errO or "Worksheet unavailable for outputs.")
                    else:
                        rowO = {
                            "project": (base_row.get("project") or ""),
                            "output_title": (base_row.get("output_title") or ""),
                            "output_type": (base_row.get("output_type") or ""),
                            "output_type_other": (base_row.get("output_type_other") or ""),
                            "output_data_type": (base_row.get("output_data_type") or ""),
                            "output_url": (base_row.get("output_url") or ""),
                            "output_country": (base_row.get("output_country") or ""),
                            "output_country_other": (base_row.get("output_country_other") or ""),
                            "output_city": (base_row.get("output_city") or ""),  # já agregado
                            "output_year": (base_row.get("output_year") or ""),
                            "output_desc": (base_row.get("output_desc") or ""),
                            "output_contact": (base_row.get("output_contact") or ""),
                            "output_email": "",
                            "output_linkedin": (base_row.get("output_linkedin") or ""),
                            "project_url": (base_row.get("project_url") or ""),
                            "submitter_email": "",
                            "is_edit": "TRUE",
                            "edit_target": str(sheet_row or ""),
                            "edit_request": f"REMOVE REQUEST: {ss._action_reason.strip()}",
                            "approved": "FALSE",
                            "created_at": datetime.utcnow().isoformat(timespec="seconds")+"Z",
                            "lat": "", "lon": "",
                        }
                        _append_row(wsO, OUTPUTS_HEADERS, rowO)
                        flash("🗑️ Removal request sent for review.", "success")
                        ss._outputs_editor_key_version += 1
                        ss._table_selection = None
                        ss._action_reason = ""
                        st.rerun()

# ──────────────────────────────────────────────────────────────────────────────
# 9) SUBMISSÃO — Reativo + Reuso países/cidades + 1 linha por país
# ──────────────────────────────────────────────────────────────────────────────
st.markdown("---")
st.header("Submit Output (goes to review queue)")

def add_city(country, city_name):
    if country and country != SELECT_PLACEHOLDER and (city_name or "").strip():
        for c in [x.strip() for x in city_name.split(",") if x.strip()]:
            pair = f"{country} — {c}"
            if pair not in ss.form_data["cities"]:
                ss.form_data["cities"].append(pair)
        return True
    return False

def remove_city(index):
    if 0 <= index < len(ss.form_data["cities"]):
        ss.form_data["cities"].pop(index)
        return True
    return False

def render_cities_list(title="Added cities"):
    if ss.form_data["cities"]:
        st.markdown(f"**{title}:**")
        for i, city_pair in enumerate(ss.form_data["cities"]):
            col1, col2 = st.columns([4, 1])
            with col1:
                st.write(f"📍 {city_pair}")
            with col2:
                st.button("🗑️ Remove", key=wkey(f"remove_{title}_{i}"),
                          on_click=lambda i=i: remove_city(i))

def hard_reset_form():
    ss.form_data = {"cities": []}
    ss._edit_mode = False
    ss._edit_reason = ""
    ss._edit_target_row = None
    ss._form_version += 1

# Campos básicos
st.subheader("Basic Information")

submitter_email = st.text_input(
    "Submitter email (required for review)*",
    placeholder="name@org.org",
    key=wkey("submitter_email")
)

project_tax_sel = st.selectbox(
    "Project Name (taxonomy)*",
    options=PROJECT_TAXONOMY,
    key=wkey("project_tax_sel")
)
is_other_project = project_tax_sel.startswith("Other")
project_tax_other = st.text_input(
    "Please specify the project (taxonomy)*",
    key=wkey("project_tax_other")
) if is_other_project else ""

output_type_sel = st.selectbox("Output Type*", options=OUTPUT_TYPES, key=wkey("output_type_sel"))

if output_type_sel == "Dataset":
    output_data_type = st.selectbox(
        "Data type (for datasets)*",
        options=[SELECT_PLACEHOLDER] + DATASET_DTYPES,
        key=wkey("output_data_type")
    )
else:
    output_data_type = ""

output_type_other = ""
if output_type_sel.startswith("Other"):
    output_type_other = st.text_input("Please specify the output type*", key=wkey("output_type_other"))

output_title = st.text_input("Output Name*", key=wkey("output_title"))
output_url = st.text_input("Output URL (optional)", key=wkey("output_url"))

# Cobertura geográfica (única para output e para projeto "Other")
st.subheader("Geographic Coverage")
output_countries = st.multiselect(
    "Select countries (select 'Global' for worldwide coverage)*",
    options=_countries_with_global_first(COUNTRY_NAMES) + ["Other: ______"],
    key=wkey("output_countries")
)
is_global = "Global" in (output_countries or [])
output_country_other = st.text_input(
    "Please specify other geographic coverage",
    key=wkey("output_country_other")
) if ("Other: ______" in (output_countries or [])) else ""

# Cidades (reativo)
if output_countries and not is_global:
    available_countries = [c for c in output_countries if c not in ["Global", "Other: ______"]]
    if available_countries:
        st.write("**Add cities (used for output and for new project if 'Other')**")
        col_country_out, col_city_out, col_btn_out = st.columns([2, 2, 1])
        with col_country_out:
            st.selectbox(
                "Select country",
                options=[SELECT_PLACEHOLDER] + available_countries,
                key=wkey("output_country_select")
            )
        with col_city_out:
            st.text_input(
                "City name (accepts multiple, separated by commas)*",
                placeholder="Enter city name",
                key=wkey("output_city_input")
            )
        def _cb_add_output_city():
            if add_city(ss.get(wkey("output_country_select")), ss.get(wkey("output_city_input"), "")):
                ss[wkey("output_city_input")] = ""
        with col_btn_out:
            st.write(""); st.write("")
            st.button("➕ Add City", use_container_width=True, on_click=_cb_add_output_city, key=wkey("btn_add_city"))

        render_cities_list("Added cities")
elif is_global:
    st.info("🌍 Global coverage selected - city selection is disabled")

# Preview mapa
if ss.form_data["cities"] and not is_global:
    st.write("**Map Preview:**")
    available_countries = [c for c in (output_countries or []) if c not in ["Global", "Other: ______"]]
    if available_countries and available_countries[0] in COUNTRY_CENTER_FULL:
        center_lat, center_lon = COUNTRY_CENTER_FULL[available_countries[0]]
    else:
        center_lat, center_lon = 0, 0
    m = folium.Map(location=[center_lat, center_lon], zoom_start=3, tiles="CartoDB positron")
    for country in (output_countries or []):
        if country in COUNTRY_CENTER_FULL and country not in ["Global", "Other: ______"]:
            folium.CircleMarker(
                location=COUNTRY_CENTER_FULL[country],
                radius=10, popup=country, tooltip=country,
                color="blue", fill=True, fill_opacity=0.6
            ).add_to(m)
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

# Info adicionais
st.subheader("Additional Information")
current_year = datetime.utcnow().year
base_years_desc = list(range(current_year, 1999, -1))
years_selected = st.multiselect("Year of output release", base_years_desc, key=wkey("years_selected"))

output_desc = st.text_area("Short description of output", key=wkey("output_desc"))
output_contact = st.text_input("Name & institution of person responsible", key=wkey("output_contact"))
output_linkedin = st.text_input("LinkedIn address of contact", key=wkey("output_linkedin"))
project_url_for_output = st.text_input("Project URL (optional, if different)", key=wkey("project_url_for_output"))

# Se projeto é "Other": reuso países/cidades do coverage
if is_other_project:
    st.subheader("New Project Details (countries/cities reused from coverage above)")
    new_project_url = st.text_input("Project URL (optional)", key=wkey("new_project_url"))
    new_project_contact = st.text_input("Project contact / institution (optional)", key=wkey("new_project_contact"))
else:
    new_project_url = ""
    new_project_contact = ""

# Ações
col1, col2 = st.columns([1, 1])

def _cb_clear():
    hard_reset_form()
    st.rerun()

def _cb_submit():
    state = {k: ss.get(wkey(k)) for k in [
        "submitter_email","project_tax_sel","project_tax_other","output_type_sel",
        "output_data_type","output_title","output_url","output_countries",
        "output_country_other","years_selected","output_desc","output_contact",
        "output_linkedin","project_url_for_output","new_project_url","new_project_contact",
        "output_type_other"
    ]}

    is_edit_mode_local = bool(ss.get("_edit_mode"))
    missing = _collect_missing_for_submit(
        state,
        is_edit_mode=is_edit_mode_local,
        cities=ss.form_data.get("cities", [])
    )
    if missing:
        _show_missing(missing)
        return

    try:
        # 1) Projeto "Other": grava por país (e por cidade)
        is_other_project_local = (state["project_tax_sel"] or "").startswith("Other")
        if is_other_project_local:
            wsP, errP = ws_projects()
            if errP or wsP is None:
                st.error(errP or "Worksheet unavailable for projects.")
                return

            def _cities_for_country(country_name: str):
                out = []
                for pair in ss.form_data["cities"]:
                    if "—" in pair:
                        ctry, city = [p.strip() for p in pair.split("—", 1)]
                        if ctry == country_name:
                            out.append(city)
                return out

            normal_countries = [c for c in (state["output_countries"] or []) if c not in ["Global", "Other: ______"]]
            if normal_countries:
                for country in normal_countries:
                    latp, lonp = COUNTRY_CENTER_FULL.get(country, (None, None))
                    rowP_country = {
                        "country": country, "city": "", "lat": latp, "lon": lonp,
                        "project_name": (state["project_tax_other"] or "").strip(),
                        "years": "", "status": "", "data_types": "", "description": "",
                        "contact": state["new_project_contact"] or "",
                        "access": "", "url": state["new_project_url"] or "",
                        "submitter_email": state["submitter_email"] or "",
                        "is_edit": "FALSE","edit_target": "","edit_request": "New project via output submission",
                        "approved": "FALSE",
                        "created_at": datetime.utcnow().isoformat(timespec="seconds")+"Z",
                    }
                    _append_row(wsP, PROJECTS_HEADERS, rowP_country)
                    for city in _cities_for_country(country):
                        rowP_city = {
                            "country": country, "city": city, "lat": latp, "lon": lonp,
                            "project_name": (state["project_tax_other"] or "").strip(),
                            "years": "", "status": "", "data_types": "", "description": "",
                            "contact": state["new_project_contact"] or "",
                            "access": "", "url": state["new_project_url"] or "",
                            "submitter_email": state["submitter_email"] or "",
                            "is_edit": "FALSE","edit_target": "","edit_request": "New project via output submission",
                            "approved": "FALSE",
                            "created_at": datetime.utcnow().isoformat(timespec="seconds")+"Z",
                        }
                        _append_row(wsP, PROJECTS_HEADERS, rowP_city)

        # 2) Output — grava 1 linha por país (e Global/Other)
        wsO, errO = ws_outputs()
        if errO or wsO is None:
            st.error(errO or "Worksheet unavailable for outputs.")
            return

        output_countries_list = state["output_countries"] or []
        final_years_sorted_desc = sorted(set(state["years_selected"] or []), reverse=True)
        final_years_str = ",".join(str(y) for y in final_years_sorted_desc) if final_years_sorted_desc else ""

        def _cities_for_country_full(country_name: str):
            out = []
            for pair in ss.form_data["cities"]:
                if "—" in pair:
                    ctry, city = [p.strip() for p in pair.split("—", 1)]
                    if ctry == country_name:
                        out.append(pair)  # mantém "País — Cidade"
            return ", ".join(out)

        def _row_base(country_value: str, lat_o, lon_o, other_txt=""):
            rb = {
                "project": ((state["project_tax_other"] or "").strip() if (state["project_tax_sel"] or "").startswith("Other") else state["project_tax_sel"]),
                "output_title": state["output_title"] or "",
                "output_type": ("" if ((state["output_type_sel"] or "").startswith("Other")) else (state["output_type_sel"] or "")),
                "output_type_other": ((state["output_type_other"] or "") if ((state["output_type_sel"] or "").startswith("Other")) else ""),
                "output_data_type": ((state["output_data_type"] or "") if state["output_type_sel"]=="Dataset" else ""),
                "output_url": state["output_url"] or "",
                "output_country": country_value,
                "output_country_other": other_txt,
                "output_year": final_years_str,
                "output_desc": state["output_desc"] or "",
                "output_contact": state["output_contact"] or "",
                "output_email": "",
                "output_linkedin": state["output_linkedin"] or "",
                "project_url": (state["project_url_for_output"] or (state["new_project_url"] if (state["project_tax_sel"] or "").startswith("Other") else "")),
                "submitter_email": state["submitter_email"] or "",
                "created_at": datetime.utcnow().isoformat(timespec="seconds")+"Z",
                "lat": lat_o if lat_o is not None else "",
                "lon": lon_o if lon_o is not None else "",
            }
            if ss.get("_edit_mode"):
                rb["is_edit"] = "TRUE"
                rb["edit_target"] = str(ss.get("_edit_target_row") or "")
                rb["edit_request"] = f"EDIT REQUEST: {ss.get('_edit_reason') or 'No reason provided'}"
            else:
                rb["is_edit"] = "FALSE"
                rb["edit_target"] = ""
                rb["edit_request"] = "New submission"
            rb["approved"] = "FALSE"
            return rb

        wrote_any = False

        if "Global" in output_countries_list:
            rowO = _row_base("Global", None, None, "")
            rowO["output_city"] = ", ".join(ss.form_data["cities"])
            _append_row(wsO, OUTPUTS_HEADERS, rowO)
            wrote_any = True

        if "Other: ______" in output_countries_list:
            other_txt = (state["output_country_other"] or "").strip() or "Other"
            rowO = _row_base(other_txt, None, None, other_txt)
            rowO["output_city"] = ", ".join(ss.form_data["cities"])
            _append_row(wsO, OUTPUTS_HEADERS, rowO)
            wrote_any = True

        normal_countries = [c for c in output_countries_list if c not in ["Global", "Other: ______"]]
        for country in normal_countries:
            lat_o, lon_o = COUNTRY_CENTER_FULL.get(country, (None, None))
            rowO = _row_base(country, lat_o, lon_o, "")
            rowO["output_city"] = _cities_for_country_full(country)
            _append_row(wsO, OUTPUTS_HEADERS, rowO)
            wrote_any = True

        if wrote_any:
            ss._post_submit = True
            ss._post_submit_msg = "✅ Output submission queued for review!"
            ss["_edit_mode"] = False
            ss["_edit_reason"] = ""
            ss["_edit_target_row"] = None
            hard_reset_form()
            st.rerun()
        else:
            st.error("⚠️ Could not write any output rows. Check your selections.")
    except Exception as e:
        st.error(f"An error occurred: {str(e)}")

with col1:
    st.button("✅ Submit for Review", use_container_width=True, type="primary",
              on_click=_cb_submit, key=wkey("btn_submit"))
with col2:
    st.button("🗑️ Clear Form", use_container_width=True, type="secondary",
              on_click=_cb_clear, key=wkey("btn_clear"))



