# app.py
import base64
from pathlib import Path
from typing import Optional, List

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

# =============================================================================
# 0) PAGE CONFIG + LOGO (favicon em base64 para evitar cache do ícone vermelho)
# =============================================================================
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

# Força favicon no <head>
if _logo_b64:
    st.markdown(
        f"""
        <link rel="icon" href="data:image/png;base64,{_logo_b64}">
        <link rel="apple-touch-icon" href="data:image/png;base64,{_logo_b64}">
        """,
        unsafe_allow_html=True,
    )

# =============================================================================
# 1) CONSTANTES / SHEETS / EMAILJS
# =============================================================================
REQUIRED_HEADERS = [
    "country","city","lat","lon","project_name","years","status",
    "data_types","description","contact","access","url",
    "submitter_email","is_edit","edit_target","edit_request",
    "approved","created_at"
]

MESSAGES_SHEET_NAME = "Public Messages"
MESSAGE_HEADERS = ["name","email","message","approved","created_at"]

SELECT_PLACEHOLDER = "— Select a country —"

@st.cache_resource(show_spinner=False)
def _gs_client():
    try:
        creds_info = st.secrets.get("gcp_service_account")
        if not creds_info:
            return None, "Please configure gcp_service_account in secrets."
        scopes = ["https://www.googleapis.com/auth/spreadsheets",
                  "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
        client = gspread.authorize(creds)
        return client, None
    except Exception as e:
        return None, f"Google Sheets auth error: {e}"

def _open_or_create_worksheet(ws_name: str, init_headers: Optional[List[str]] = None):
    client, err = _gs_client()
    if err or client is None:
        return None, err or "Client unavailable."
    ss_id = st.secrets.get("SHEETS_SPREADSHEET_ID")
    if not ss_id:
        return None, "Please set SHEETS_SPREADSHEET_ID in secrets."
    try:
        ss = client.open_by_key(ss_id)
    except Exception as e:
        return None, f"Failed to open spreadsheet: {e}"
    try:
        ws = ss.worksheet(ws_name)
        return ws, None
    except gspread.exceptions.WorksheetNotFound:
        try:
            ncols = max(10, len(init_headers) if init_headers else 10)
            ws = ss.add_worksheet(title=ws_name, rows=200, cols=ncols)
            if init_headers:
                ws.update('A1', [init_headers])
            return ws, None
        except Exception as e:
            return None, f"Could not create worksheet '{ws_name}': {e}"
    except Exception as e:
        return None, f"Failed to open worksheet '{ws_name}': {e}"

def _ws_projects():
    ws_name = st.secrets.get("SHEETS_WORKSHEET_NAME") or "projects"
    return _open_or_create_worksheet(ws_name, REQUIRED_HEADERS)

def _ws_messages():
    return _open_or_create_worksheet(MESSAGES_SHEET_NAME, MESSAGE_HEADERS)

def _col_letter(idx0: int) -> str:
    n = idx0 + 1
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(r + 65) + s
    return s

def ensure_lat_lon_text_columns():
    ws, err = _ws_projects()
    if err or ws is None:
        return False, err or "Worksheet unavailable."
    try:
        header = ws.row_values(1)
        if not header:
            return False, "Projects sheet header (row 1) is empty."
        hdr_lower = [h.strip().lower() for h in header]
        if "lat" not in hdr_lower or "lon" not in hdr_lower:
            return False, "Projects header must contain 'lat' and 'lon'."
        lat_idx = hdr_lower.index("lat")
        lon_idx = hdr_lower.index("lon")
        lat_col = _col_letter(lat_idx)
        lon_col = _col_letter(lon_idx)
        ws.format(f"{lat_col}:{lat_col}", {"numberFormat": {"type": "TEXT"}})
        ws.format(f"{lon_col}:{lon_col}", {"numberFormat": {"type": "TEXT"}})
        return True, "lat/lon columns set to TEXT."
    except Exception as e:
        return False, f"Failed to format lat/lon columns: {e}"

def ensure_headers(required_headers=REQUIRED_HEADERS):
    ws, err = _ws_projects()
    if err or ws is None:
        return False, err or "Worksheet unavailable."
    try:
        header = ws.row_values(1)
        header = [h.strip() for h in header] if header else []
        missing = [h for h in required_headers if h not in header]
        if missing:
            new_header = header + missing
            ws.update('A1', [new_header])
        return True, "Projects header OK."
    except Exception as e:
        return False, f"Failed to adjust projects header: {e}"

def ensure_message_headers():
    ws, err = _ws_messages()
    if err or ws is None:
        return False, err or "Messages worksheet unavailable."
    try:
        header = ws.row_values(1)
        header = [h.strip() for h in header] if header else []
        missing = [h for h in MESSAGE_HEADERS if h not in header]
        if missing or not header:
            new_header = header + missing
            ws.update('A1', [new_header])
        return True, "Messages header OK."
    except Exception as e:
        return False, f"Failed to adjust messages header: {e}"

def try_send_email_via_emailjs(template_params: dict) -> tuple[bool, str]:
    svc = st.secrets.get("EMAILJS_SERVICE_ID")
    tpl = st.secrets.get("EMAILJS_TEMPLATE_ID")
    key = st.secrets.get("EMAILJS_PUBLIC_KEY")
    if not (svc and tpl and key):
        return False, "EmailJS is not configured."
    try:
        resp = requests.post(
            "https://api.emailjs.com/api/v1.0/email/send",
            json={"service_id": svc, "template_id": tpl, "user_id": key, "template_params": template_params},
            timeout=12,
        )
        return (True, "Notification email sent.") if resp.status_code == 200 else (False, f"EmailJS {resp.status_code}.")
    except Exception as e:
        return False, f"Email error: {e}"

def _parse_number_loose(x):
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    s = str(x).strip().strip("'").strip('"')
    if not s:
        return None
    if ("," in s) or ("." in s):
        last_comma = s.rfind(",")
        last_dot   = s.rfind(".")
        last_sep   = max(last_comma, last_dot)
        if last_sep >= 0:
            int_part  = re.sub(r"[^\d\-\+]", "", s[:last_sep]) or "0"
            frac_part = re.sub(r"\D", "", s[last_sep+1:]) or "0"
            try:
                return float(f"{int_part}.{frac_part}")
            except Exception:
                pass
    raw = re.sub(r"[^\d\-\+]", "", s)
    try:
        return float(raw)
    except Exception:
        try:
            return float(s.replace(",", "."))
        except Exception:
            return None

# =============================================================================
# 2) LOAD COUNTRIES CSV (LOCAL ONLY)
# =============================================================================
COUNTRY_CSV_PATH = APP_DIR / "country-coord.csv"

@st.cache_data(show_spinner=False)
def load_country_centers():
    df = pd.read_csv(COUNTRY_CSV_PATH, dtype=str, encoding="utf-8", on_bad_lines="skip")
    df.columns = [c.strip().lower() for c in df.columns]
    c_country = "country"
    c_lat = "latitude (average)"
    c_lon = "longitude (average)"
    if c_country not in df.columns or c_lat not in df.columns or c_lon not in df.columns:
        raise RuntimeError("CSV must contain: 'Country', 'Latitude (average)', 'Longitude (average)'.")
    df["lat"] = df[c_lat].apply(_parse_number_loose)
    df["lon"] = df[c_lon].apply(_parse_number_loose)
    df = df.dropna(subset=["lat", "lon"])
    mapping = {row[c_country]: (float(row["lat"]), float(row["lon"])) for _, row in df.iterrows()}
    return mapping, df

COUNTRY_CENTER_FULL, _df_countries = load_country_centers()

# =============================================================================
# 3) HEADER COM LOGO
# =============================================================================
header_html = f"""
<div style="
  display:flex; align-items:center; gap:16px;
  background: linear-gradient(90deg,#0f172a,#1e293b,#0f172a);
  border:1px solid #334155; border-radius:14px;
  padding:14px 16px; margin-bottom:14px;
">
  <div style="
    width:44px; height:44px; border-radius:10px; overflow:hidden;
    background:#0b1220; display:flex; align-items:center; justify-content:center;
    flex:0 0 auto;
  ">
    {"<img src='data:image/png;base64," + _logo_b64 + "' style='width:100%;height:100%;object-fit:cover;'/>" if _logo_b64 else "🌍"}
  </div>
  <div style="display:flex; flex-direction:column;">
    <div style="color:#fff; font-weight:700; font-size:1.2rem;">
      IDEAMAPS Global Metadata Explorer
    </div>
    <div style="color:#94a3b8; font-size:0.90rem; line-height:1.3; margin-top:2px;">
      Living catalogue of projects and datasets (spatial / quantitative / qualitative) produced by the IDEAMAPS network and partners.
    </div>
  </div>
</div>
"""
st.markdown(header_html, unsafe_allow_html=True)

if _logo_img is not None:
    st.sidebar.image(_logo_img, caption="IDEAMAPS", use_container_width=True)

# =============================================================================
# 4) CARREGA PROJETOS APROVADOS + MAPA
# =============================================================================
@st.cache_data(show_spinner=False)
def load_approved_projects():
    ws, err = _ws_projects()
    if err or ws is None:
        return pd.DataFrame(), False, err
    try:
        rows = ws.get_all_records()
        df = pd.DataFrame(rows)
        if df.empty:
            return pd.DataFrame(), False, "Projects sheet is empty."
        for c in REQUIRED_HEADERS:
            if c not in df.columns:
                df[c] = ""
        df = df[df["approved"].astype(str).str.upper().eq("TRUE")].copy()
        if "lat" in df.columns:
            df["lat"] = df["lat"].apply(_parse_number_loose)
        if "lon" in df.columns:
            df["lon"] = df["lon"].apply(_parse_number_loose)
        return df, True, None
    except Exception as e:
        return pd.DataFrame(), False, f"Error reading projects: {e}"

if st.sidebar.button("🔄 Check updates"):
    load_approved_projects.clear()
    load_country_centers.clear()
    ensure_headers()
    ensure_lat_lon_text_columns()
    ensure_message_headers()
    st.session_state["_last_refresh"] = datetime.utcnow().isoformat()
    st.rerun()

df_projects, from_sheets, debug_msg = load_approved_projects()
if not from_sheets and debug_msg:
    st.caption(f"⚠️ {debug_msg}")

def _as_float(x):
    v = _parse_number_loose(x)
    return float(v) if v is not None else None

def _clean_url(u):
    s = (u or "").strip()
    return s if (s.startswith("http://") or s.startswith("https://")) else s

if not df_projects.empty:
    if "lat" in df_projects.columns:
        df_projects["lat"] = df_projects["lat"].apply(_as_float)
    if "lon" in df_projects.columns:
        df_projects["lon"] = df_projects["lon"].apply(_as_float)
    df_map = df_projects.dropna(subset=["lat", "lon"]).copy()

    if df_map.empty:
        st.info("No valid points to plot (missing lat/lon).")
    else:
        groups = df_map.groupby(["country", "lat", "lon"], as_index=False)
        m = folium.Map(
            location=[df_map["lat"].mean(), df_map["lon"].mean()],
            zoom_start=2,
            tiles="CartoDB dark_matter"
        )
        for (country, lat, lon), g in groups:
            proj_dict = {}
            for _, r in g.iterrows():
                pname = str(r.get("project_name", "")).strip() or "(unnamed project)"
                city = str(r.get("city", "")).strip()
                url = _clean_url(r.get("url", ""))
                if pname not in proj_dict:
                    proj_dict[pname] = {"cities": set(), "urls": set()}
                if city:
                    proj_dict[pname]["cities"].add(city)
                if url:
                    proj_dict[pname]["urls"].add(url)
            lines = [
                "<div style='font-size:0.9rem; color:#0f172a;'>",
                f"<b>{country}</b>",
                "<ul style='padding-left:1rem; margin:0;'>"
            ]
            for pname, info in proj_dict.items():
                cities_txt = ", ".join(sorted(info["cities"])) if info["cities"] else "—"
                url_html = ""
                if info["urls"]:
                    u_any = sorted(info["urls"])[0]
                    url_html = (
                        " — "
                        f"<a href='{u_any}' target='_blank' "
                        "style='color:#2563eb; text-decoration:none;'>link</a>"
                    )
                lines.append(f"<li><b>{pname}</b> — {cities_txt}{url_html}</li>")
            lines.append("</ul></div>")
            html_block = "".join(lines)
            any_active = any(str(x).lower() == "active" for x in g["status"].tolist())
            color = "#38bdf8" if any_active else "#facc15"
            folium.CircleMarker(
                location=[lat, lon],
                radius=6,
                color=color,
                fill=True,
                fill_opacity=0.9,
                tooltip=folium.Tooltip(
                    html_block, sticky=True, direction='top',
                    style="background:#ffffff; color:#0f172a; border:1px solid #cbd5e1; border-radius:8px; padding:8px;"
                ),
                popup=folium.Popup(html_block, max_width=380),
            ).add_to(m)
        st_folium(m, height=520, width=None)
else:
    st.info("No approved projects found.")

st.markdown("---")

# =============================================================================
# 5) BROWSE & LOAD FOR EDIT / REQUEST DELETE
# =============================================================================
st.subheader("Browse existing entries")
if df_projects.empty:
    st.info("No data to display yet.")
else:
    def _mk_key(row: pd.Series) -> str:
        return " || ".join([
            str(row.get("project_name","")).strip() or "(unnamed)",
            str(row.get("country","")).strip() or "—",
            str(row.get("city","")).strip() or "—",
        ])

    table_cols = ["project_name","country","city","years","data_types","contact","access","url","lat","lon"]
    table_cols = [c for c in table_cols if c in df_projects.columns]
    df_view = df_projects[table_cols].copy()
    df_view["__key__"] = df_projects.apply(_mk_key, axis=1)

    st.dataframe(
        df_view.drop(columns=["__key__"]),
        use_container_width=True,
        hide_index=True
    )

    options = df_view["__key__"].tolist()
    selected_key = st.selectbox(
        "Select a row to edit or delete (loads into the submission form below)",
        options=[""] + options,
        index=0
    )

    col_a, col_b = st.columns([1, 1])

    with col_a:
        if selected_key and st.button("✎ Edit this row", use_container_width=True):
            sel = df_view[df_view["__key__"] == selected_key].iloc[0].to_dict()
            idx_sel = df_view.index[df_view["__key__"] == selected_key][0]
            st.session_state["_edit_mode"]  = True
            st.session_state["_before_row"] = df_projects.loc[idx_sel].to_dict()
            st.session_state["prefill_project_name"] = str(sel.get("project_name",""))
            st.session_state["countries_sel"] = [str(sel.get("country",""))] if sel.get("country","") else []
            st.session_state["city_list"] = []
            if sel.get("country","") and str(sel.get("city","")).strip():
                st.session_state["city_list"] = [f"{sel['country']} — {sel['city']}"]
            st.session_state["prefill_years"]       = str(sel.get("years",""))
            st.session_state["prefill_data_types"]  = str(sel.get("data_types",""))
            st.session_state["prefill_description"] = str(df_projects.loc[idx_sel].get("description",""))
            st.session_state["prefill_contact"]     = str(sel.get("contact",""))
            st.session_state["prefill_access"]      = str(sel.get("access",""))
            st.session_state["prefill_url"]         = str(sel.get("url",""))
            st.session_state["prefill_lat"]         = sel.get("lat", "")
            st.session_state["prefill_lon"]         = sel.get("lon", "")
            st.info("Loaded into the submission form below. Make your changes and submit to queue an **edit**.")
            st.rerun()

    with col_b:
        if selected_key and st.button("🗑 Request deletion", use_container_width=True, type="secondary"):
            st.session_state["_delete_target"] = selected_key

    if st.session_state.get("_delete_target"):
        st.markdown("### 🗑 Request deletion")
        st.warning(f"You are requesting deletion of: `{st.session_state['_delete_target']}`")
        target_sel = df_view[df_view["__key__"] == st.session_state["_delete_target"]]
        if target_sel.empty:
            st.error("Selected row not found anymore. Please refresh.")
        else:
            idx_sel = target_sel.index[0]
            full_row = df_projects.loc[idx_sel].to_dict()
            del_reason = st.text_area("Please describe the reason for deletion", placeholder="Explain why this entry should be removed.")
            del_email = st.text_input("Your email (required for follow-up)", key="del_email", placeholder="name@org.org")
            c1, c2 = st.columns([1, 4])
            with c1:
                confirm = st.button("Confirm deletion request", type="primary")
            with c2:
                cancel  = st.button("Cancel")
            if cancel:
                st.session_state.pop("_delete_target", None)
                st.rerun()
            if confirm:
                if not del_email.strip():
                    st.error("Please provide your email.")
                elif not del_reason.strip():
                    st.error("Please provide a reason for deletion.")
                else:
                    payload = {
                        "country": str(full_row.get("country","")),
                        "city": str(full_row.get("city","")),
                        "lat": full_row.get("lat",""),
                        "lon": full_row.get("lon",""),
                        "project_name": str(full_row.get("project_name","")),
                        "years": str(full_row.get("years","")),
                        "status": str(full_row.get("status","")),
                        "data_types": str(full_row.get("data_types","")),
                        "description": str(full_row.get("description","")),
                        "contact": str(full_row.get("contact","")),
                        "access": str(full_row.get("access","")),
                        "url": str(full_row.get("url","")),
                        "submitter_email": del_email.strip(),
                        "is_edit": True,
                        "edit_target": str(full_row.get("project_name","")),
                        "edit_request": f"Request deletion — {del_reason.strip()}",
                    }
                    ok_sheet, msg_sheet = append_submission_to_sheet(payload)
                    if ok_sheet:
                        try_send_email_via_emailjs({
                            "project_name": payload["project_name"],
                            "entries": f"{payload['country']} — {payload['city']}",
                            "status": "",
                            "years": payload["years"],
                            "url": payload["url"],
                            "submitter_email": payload["submitter_email"],
                            "is_edit": "yes",
                            "edit_target": payload["edit_target"],
                            "edit_request": payload["edit_request"],
                        })
                        st.success("✅ Deletion request submitted for review.")
                        st.session_state.pop("_delete_target", None)
                        load_approved_projects.clear()
                        st.rerun()
                    else:
                        st.error(f"⚠️ Failed to record deletion request: {msg_sheet}")

st.markdown("---")

# =============================================================================
# 6) ADD / EDIT PROJECT (sempre vai para revisão)
# =============================================================================
st.header("Add / Edit project (goes to review queue)")

if "city_list" not in st.session_state:
    st.session_state.city_list = []
if "_edit_mode" not in st.session_state:
    st.session_state["_edit_mode"] = False
if "_before_row" not in st.session_state:
    st.session_state["_before_row"] = {}
if "_reset_city_inputs" not in st.session_state:
    st.session_state["_reset_city_inputs"] = False

def _add_city_entry(country, city):
    if country and city:
        pair = f"{country} — {city}"
        if pair not in st.session_state.city_list:
            st.session_state.city_list.append(pair)

countries_options = sorted(COUNTRY_CENTER_FULL.keys())
st.session_state.countries_sel = st.multiselect(
    "Implementation countries (one or more)",
    options=countries_options,
    default=st.session_state.get("countries_sel", []),
    help="Select all countries where this project is implemented."
)

options_for_city = st.session_state.get("countries_sel", [])

# Limpeza adiada dos widgets do form
if st.session_state.get("_reset_city_inputs", False):
    st.session_state.pop("city_to_add", None)
    st.session_state.pop("country_for_city", None)
    st.session_state["_reset_city_inputs"] = False

with st.form("add_project_form", clear_on_submit=False):
    if st.session_state["_edit_mode"]:
        st.markdown("🟦 **Editing an existing entry** — your submission will be queued as an **edit** to the catalogue.")

    new_name = st.text_input(
        "Project name",
        value=st.session_state.get("prefill_project_name",""),
        placeholder="e.g., IDEAMAPS Lagos / Urban Deprivation Mapping"
    )
    submitter_email = st.text_input("Submitter email (required for review)", placeholder="name@org.org")

    st.markdown("**Cities covered**")
    st.caption("Tip: add a city to one selected country or add to **all** selected countries. Use commas to add multiple cities at once.")

    colc1, colc2, colc3 = st.columns([2, 2, 1])

    with colc1:
        _opts = [SELECT_PLACEHOLDER] + (options_for_city if options_for_city else [])
        selected_country_for_city = st.selectbox(
            "Select implementation country for the city",
            options=_opts,
            index=0,
            disabled=not bool(options_for_city),
            key="country_for_city",
        )

    with colc2:
        city_to_add = st.text_input(
            "City (accepts multiple, separated by commas)",
            key="city_to_add",
            placeholder="e.g., Lagos, Ibadan"
        )

    with colc3:
        st.write("")
        add_one = st.form_submit_button(
            "➕ Add to this country",
            use_container_width=True,
            disabled=not options_for_city
        )

    # ADD ONE
    if add_one:
        selected_country = st.session_state.get("country_for_city")
        city_input = st.session_state.get("city_to_add", "").strip()
        if not selected_country or selected_country == SELECT_PLACEHOLDER:
            st.warning("Please select a valid country first.")
        elif not city_input:
            st.warning("Please type a city name.")
        else:
            cities = [c.strip() for c in city_input.split(",") if c.strip()]
            for c in cities:
                _add_city_entry(selected_country, c)
            st.session_state["_reset_city_inputs"] = True
            st.rerun()

    add_all = st.form_submit_button(
        "➕ Add to ALL selected countries",
        use_container_width=True,
        disabled=not (options_for_city and st.session_state.get("city_to_add", "").strip())
    )

    if add_all:
        cities_bulk = [c.strip() for c in st.session_state.get("city_to_add", "").split(",") if c.strip()]
        for ctry in options_for_city:
            for c in cities_bulk:
                _add_city_entry(ctry, c)
        st.session_state["_reset_city_inputs"] = True
        st.rerun()

    if st.session_state.city_list:
        st.caption("Cities added (country — city):")
        to_remove_idx = None
        for i, item in enumerate(st.session_state.city_list):
            c1, c2 = st.columns([6,1])
            with c1:
                st.write(f"- {item}")
            with c2:
                if st.form_submit_button("Remove", key=f"rm_{i}"):
                    to_remove_idx = i
        if to_remove_idx is not None:
            st.session_state.city_list.pop(to_remove_idx)
            st.rerun()

        if st.checkbox("Clear all cities"):
            st.session_state.city_list = []

    new_years  = st.text_input("Years (e.g. 2022–2024)", value=st.session_state.get("prefill_years",""))
    new_types  = st.text_area("Data types (Spatial? Quantitative? Qualitative?)", value=st.session_state.get("prefill_data_types",""))
    new_desc   = st.text_area("Short description", value=st.session_state.get("prefill_description",""))
    new_contact= st.text_input("Contact / Responsible institution", value=st.session_state.get("prefill_contact",""))
    new_access = st.text_input("Access / License / Ethics", value=st.session_state.get("prefill_access",""))
    new_url    = st.text_input("Project URL (optional)", value=st.session_state.get("prefill_url",""))
    submitted  = st.form_submit_button("Submit for review")

def append_submission_to_sheet(payload: dict) -> tuple[bool, str]:
    ws, err = _ws_projects()
    if err or ws is None:
        return False, err
    try:
        ensure_headers(); ensure_lat_lon_text_columns()
        def _fmt_num_str(v):
            try:
                return f"{float(v):.6f}"
            except Exception:
                return ""
        row = {
            "country": payload.get("country", ""),
            "city": payload.get("city", ""),
            "lat": _fmt_num_str(payload.get("lat", "")),
            "lon": _fmt_num_str(payload.get("lon", "")),
            "project_name": payload.get("project_name", ""),
            "years": payload.get("years", ""),
            "status": payload.get("status", ""),
            "data_types": payload.get("data_types", ""),
            "description": payload.get("description", ""),
            "contact": payload.get("contact", ""),
            "access": payload.get("access", ""),
            "url": payload.get("url", ""),
            "submitter_email": payload.get("submitter_email", ""),
            "is_edit": "TRUE" if payload.get("is_edit") else "FALSE",
            "edit_target": payload.get("edit_target", ""),
            "edit_request": payload.get("edit_request", ""),
            "approved": "FALSE",
            "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
        header = ws.row_values(1)
        values = [row.get(col, "") for col in header] if header else list(row.values())
        ws.append_row(values, value_input_option="RAW")
        return True, "Saved to Google Sheets."
    except Exception as e:
        return False, f"Write error: {e}"

def _summarize_changes(before: dict, after: dict) -> str:
    cols = ["country","city","lat","lon","project_name","years","status","data_types","description","contact","access","url"]
    changes = []
    for c in cols:
        bv = before.get(c, "")
        av = after.get(c, "")
        if str(bv) != str(av):
            bv_s = "" if (bv is None or (isinstance(bv, float) and pd.isna(bv))) else str(bv)
            av_s = "" if (av is None or (isinstance(av, float) and pd.isna(av))) else str(av)
            if len(bv_s) > 120: bv_s = bv_s[:117]+"…"
            if len(av_s) > 120: av_s = av_s[:117]+"…"
            changes.append(f"{c}: '{bv_s}' → '{av_s}'")
    return "; ".join(changes) if changes else "No visible change"

if submitted:
    if not st.session_state.get("countries_sel"):
        st.warning("Please select at least one implementation country.")
    elif not (st.session_state.city_list or st.session_state.get("countries_sel")):
        st.warning("Please add at least one (country — city) pair or select countries.")
    elif not submitter_email.strip():
        st.warning("Please provide a submitter email.")
    elif not new_name.strip():
        st.warning("Please provide a Project name.")
    else:
        total_rows, ok_all, msg_any = 0, True, None
        entries_preview = []
        pairs = st.session_state.city_list[:] if st.session_state.city_list else [f"{c} — " for c in st.session_state.get("countries_sel", [])]
        before = st.session_state.get("_before_row", {}) if st.session_state.get("_edit_mode") else {}
        for pair in pairs:
            if "—" not in pair:
                continue
            country, city = [p.strip() for p in pair.split("—", 1)]
            lat, lon = COUNTRY_CENTER_FULL.get(country, (None, None)) if country else (None, None)
            after_payload = {
                "country": country,
                "city": city,
                "lat": lat,
                "lon": lon,
                "project_name": new_name,
                "years": st.session_state.get("prefill_years","") if st.session_state.get("_edit_mode") else st.session_state.get("prefill_years","") or "",
                "status": "",
                "data_types": st.session_state.get("prefill_data_types","") if st.session_state.get("_edit_mode") else st.session_state.get("prefill_data_types","") or "",
                "description": st.session_state.get("prefill_description","") if st.session_state.get("_edit_mode") else st.session_state.get("prefill_description","") or "",
                "contact": st.session_state.get("prefill_contact","") if st.session_state.get("_edit_mode") else st.session_state.get("prefill_contact","") or "",
                "access": st.session_state.get("prefill_access","") if st.session_state.get("_edit_mode") else st.session_state.get("prefill_access","") or "",
                "url": st.session_state.get("prefill_url","") if st.session_state.get("_edit_mode") else st.session_state.get("prefill_url","") or "",
                "submitter_email": submitter_email,
            }
            if st.session_state.get("_edit_mode"):
                edit_target = before.get("project_name", new_name)
                edit_request = _summarize_changes(before, after_payload)
                payload = {**after_payload, "is_edit": True, "edit_target": edit_target, "edit_request": edit_request}
            else:
                payload = {**after_payload, "is_edit": False, "edit_target": "", "edit_request": "New submission"}
            ok_sheet, msg_sheet = append_submission_to_sheet(payload)
            ok_all &= ok_sheet
            msg_any = msg_sheet
            total_rows += 1
            entries_preview.append(payload)
        if ok_all:
            st.success(f"✅ {'Edit queued' if st.session_state.get('_edit_mode') else 'Submission saved'} ({total_rows} row(s) added).")
            for k in list(st.session_state.keys()):
                if str(k).startswith("prefill_"):
                    st.session_state[k] = ""
            st.session_state["_edit_mode"] = False
            st.session_state["_before_row"] = {}
            st.session_state["city_list"] = []
        else:
            st.warning(f"⚠️ Some rows failed: {msg_any}")
        ok_mail, msg_mail = try_send_email_via_emailjs({
            "project_name": new_name,
            "entries": ", ".join([f"{p['country']} — {p['city']}" for p in entries_preview]),
            "status": "",
            "years": "",
            "url": "",
            "submitter_email": submitter_email,
            "is_edit": "yes" if st.session_state.get("_edit_mode") else "no",
            "edit_target": st.session_state.get("_before_row", {}).get("project_name",""),
        })
        if ok_mail:
            st.info("📨 Notification email sent.")
        else:
            st.caption(msg_mail)
        st.markdown("**Submission preview (first rows):**")
        st.code(entries_preview[:3], language="python")

st.markdown("---")

# =============================================================================
# 7) COMMUNITY MESSAGE BOARD (SUBMIT → APPROVAL → DISPLAY)
# =============================================================================
st.header("Community message board")

with st.form("message_form"):
    msg_name = st.text_input("Your full name", placeholder="First Last")
    msg_email = st.text_input("Email (optional)", placeholder="name@org.org")
    msg_text = st.text_area("Your message", placeholder="Write your note to the IDEAMAPS team / partners...")
    send_msg = st.form_submit_button("Send message for approval")

def append_message_to_sheet(name: str, email: str, message: str) -> tuple[bool, str]:
    ws, err = _ws_messages()
    if err or ws is None:
        return False, err or "Messages worksheet unavailable."
    try:
        ensure_message_headers()
        row = {
            "name": name.strip(),
            "email": (email or "").strip(),
            "message": message.strip(),
            "approved": "FALSE",
            "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
        header = ws.row_values(1)
        if not header:
            ws.update('A1', [MESSAGE_HEADERS])
            header = MESSAGE_HEADERS
        values = [row.get(col, "") for col in header]
        ws.append_row(values, value_input_option="RAW")
        return True, "Message queued for approval."
    except Exception as e:
        return False, f"Write error: {e}"

if send_msg:
    if not msg_name.strip():
        st.warning("Please provide your full name.")
    elif not msg_text.strip():
        st.warning("Please write a message.")
    else:
        ok, msg = append_message_to_sheet(msg_name, msg_email, msg_text)
        if ok:
            st.success("✅ Message sent for approval.")
        else:
            st.warning(f"⚠️ {msg}")

@st.cache_data(show_spinner=False)
def _load_messages():
    ws, err = _ws_messages()
    if err or ws is None:
        return pd.DataFrame(), False, err
    try:
        rows = ws.get_all_records()
        df = pd.DataFrame(rows)
        if df.empty:
            for c in MESSAGE_HEADERS:
                if c not in df.columns:
                    df[c] = ""
            return df, True, None
        for c in MESSAGE_HEADERS:
            if c not in df.columns:
                df[c] = ""
        df["approved"] = df["approved"].astype(str).str.upper().isin(["TRUE", "YES", "1"])
        if "created_at" in df.columns:
            df = df.sort_values("created_at", ascending=False)
        return df, True, None
    except Exception as e:
        return pd.DataFrame(), False, f"Error reading messages: {e}"

st.subheader("Public messages")

df_msgs, ok_msgs, err_msgs = _load_messages()
if not ok_msgs:
    st.caption(f"⚠️ {err_msgs or 'Could not load messages.'}")
elif df_msgs.empty:
    st.info("No messages yet.")
else:
    tab_pub, tab_all = st.tabs(["Public (Approved)", "All (Admin view)"])
    with tab_pub:
        df_pub = df_msgs[df_msgs["approved"]]
        if df_pub.empty:
            st.info("No approved messages yet.")
        else:
            for _, r in df_pub.head(50).iterrows():
                name   = (r.get("name","") or "").strip() or "Anonymous"
                email  = (r.get("email","") or "").strip()
                created= (r.get("created_at","") or "").strip()
                body   = (r.get("message","") or "").strip()
                st.markdown(
                    f"""
                    <div style="border:1px solid #334155;background:#0b1220;border-radius:12px;padding:12px;margin-bottom:10px;">
                      <div style="color:#e5e7eb;font-weight:600;">{name}
                        <span style="color:#64748b;font-weight:400;font-size:0.85rem;">{(' • '+email) if email else ''}</span>
                      </div>
                      <div style="color:#94a3b8;font-size:0.75rem;margin-top:2px;">{created}</div>
                      <div style="color:#cbd5e1;margin-top:8px; white-space:pre-wrap;">{body}</div>
                    </div>
                    """,
                    unsafe_allow_html=True
                )
    with tab_all:
        show_cols = ["name","email","message","approved","created_at"]
        present = [c for c in show_cols if c in df_msgs.columns]
        st.dataframe(
            df_msgs[present].reset_index(drop=True),
            use_container_width=True,
            hide_index=True
        )

# =============================================================================
# 8) OUTPUTS — DATA MODEL + SHEETS HELPERS (NOVA)
# =============================================================================
def section_8_outputs_model(st, _open_or_create_worksheet, datetime, pd):
    """
    Cria/acessa a aba 'outputs' no Google Sheets e expõe utilitários:
      - load_approved_outputs()
      - append_output_to_sheet(payload)
    Também define listas fixas pedidas nas sugestões.
    """
    OUTPUTS_SHEET_NAME = st.secrets.get("SHEETS_OUTPUTS_WORKSHEET_NAME", "outputs")

    SEC8_HEADERS = [
        "project",
        "output_title",
        "output_type",
        "output_data_type",      # usado só se output_type == "Dataset"
        "output_url",
        "output_country",
        "output_city",
        "output_year",
        "output_desc",
        "output_contact",
        "output_email",
        "project_url",
        # workflow
        "submitter_email",
        "is_edit",
        "edit_target",
        "edit_request",
        "approved",
        "created_at",
    ]

    SEC8_PROJECT_OPTIONS = [
        "IDEAMAPS Networking Grant",
        "IDEAMAPSudan",
        "SLUMAP",
        "Data4HumanRights",
        "IDEAMAPS Data Ecosystem",
        "Night Watch",
        "ONEKANA",
        "Space4All",
        "IDEAtlas",
        "DEPRIMAP",
        "URBE Latem",
        "Other: ______",
    ]

    SEC8_OUTPUT_TYPE_OPTIONS = [
        "Dataset",
        "Code / App / Tool",
        "Document",
        "Academic Paper",
        "Other: ________",
    ]

    SEC8_DATA_TYPE_OPTIONS = [
        "Spatial (eg shapefile)",
        "Qualitative (eg audio recording)",
        "Quantitative (eg survey results)",
    ]

    # ---------- Google Sheets ----------
    def _ws_outputs():
        return _open_or_create_worksheet(OUTPUTS_SHEET_NAME, SEC8_HEADERS)

    def ensure_output_headers():
        ws, err = _ws_outputs()
        if err or ws is None:
            return False, err or "Outputs worksheet unavailable."
        try:
            header = ws.row_values(1)
            header = [h.strip() for h in header] if header else []
            missing = [h for h in SEC8_HEADERS if h not in header]
            if missing:
                ws.update('A1', [header + missing])
            return True, "Outputs header OK."
        except Exception as e:
            return False, f"Failed to adjust outputs header: {e}"

    @st.cache_data(show_spinner=False)
    def load_approved_outputs():
        ws, err = _ws_outputs()
        if err or ws is None:
            return pd.DataFrame(), False, err
        try:
            rows = ws.get_all_records()
            df = pd.DataFrame(rows)
            if df.empty:
                for c in SEC8_HEADERS:
                    if c not in df.columns:
                        df[c] = ""
                return df, True, None
            for c in SEC8_HEADERS:
                if c not in df.columns:
                    df[c] = ""
            df = df[df["approved"].astype(str).str.upper().eq("TRUE")].copy()
            return df, True, None
        except Exception as e:
            return pd.DataFrame(), False, f"Error reading outputs: {e}"

    def append_output_to_sheet(payload: dict) -> tuple[bool, str]:
        ws, err = _ws_outputs()
        if err or ws is None:
            return False, err
        try:
            ensure_output_headers()
            row = {
                "project": payload.get("project",""),
                "output_title": payload.get("output_title",""),
                "output_type": payload.get("output_type",""),
                "output_data_type": payload.get("output_data_type",""),
                "output_url": payload.get("output_url",""),
                "output_country": payload.get("output_country",""),
                "output_city": payload.get("output_city",""),
                "output_year": payload.get("output_year",""),
                "output_desc": payload.get("output_desc",""),
                "output_contact": payload.get("output_contact",""),
                "output_email": payload.get("output_email",""),
                "project_url": payload.get("project_url",""),
                "submitter_email": payload.get("submitter_email",""),
                "is_edit": "TRUE" if payload.get("is_edit") else "FALSE",
                "edit_target": payload.get("edit_target",""),
                "edit_request": payload.get("edit_request",""),
                "approved": "FALSE",
                "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            }
            header = ws.row_values(1)
            values = [row.get(col, "") for col in header] if header else list(row.values())
            ws.append_row(values, value_input_option="RAW")
            return True, "Saved output to Google Sheets."
        except Exception as e:
            return False, f"Write error: {e}"

    return {
        "HEADERS": SEC8_HEADERS,
        "PROJECT_OPTIONS": SEC8_PROJECT_OPTIONS,
        "OUTPUT_TYPE_OPTIONS": SEC8_OUTPUT_TYPE_OPTIONS,
        "DATA_TYPE_OPTIONS": SEC8_DATA_TYPE_OPTIONS,
        "load_approved_outputs": load_approved_outputs,
        "append_output_to_sheet": append_output_to_sheet,
    }

# =============================================================================
# 9) OUTPUTS — UI (Background/CTA + Browse + Add/Edit + Deletion) (NOVA)
# =============================================================================
def section_9_outputs_ui(
    st,
    COUNTRY_CENTER_FULL,
    try_send_email_via_emailjs,
    _open_or_create_worksheet,
    datetime,
    pd,
):
    # Garante helpers da seção 8 (uma única instância)
    sec8 = section_8_outputs_model(st, _open_or_create_worksheet, datetime, pd)

    # ---------- Background + Call to Action ----------
    st.markdown(
        """
        <div style="border:1px solid #334155;background:#0b1220;border-radius:14px;padding:16px; margin-bottom:10px;">
          <div style="color:#e2e8f0; font-weight:700; font-size:1.05rem; margin-bottom:6px;">Background</div>
          <div style="color:#cbd5e1; line-height:1.5;">
            The IDEAMAPS Network brings together diverse “slum” mapping traditions to co-produce new ways of understanding and addressing urban inequalities.
            Network projects connect data scientists, communities, local governments, and other stakeholders through feedback loops that produce routine, accurate,
            and comparable citywide maps of area deprivations and assets. These outputs support upgrading, advocacy, monitoring, and other efforts to improve
            urban conditions.
            <br><br>
            This form gathers information on datasets, code, apps, training materials, community profiles, policy briefs, academic papers, and other outputs
            from IDEAMAPS and related projects. The resulting inventory will help members identify existing resources, strengthen collaboration, and develop
            new analyses and initiatives that build on the Network’s collective work.
            <br><br>
            <b>Call to Action:</b> If you or your team have produced relevant data, tools, or materials, please share them here. Your contributions will expand the Network’s
            shared evidence base and create new opportunities for collaboration.
          </div>
        </div>
        """,
        unsafe_allow_html=True
    )

    st.markdown("### Outputs (beta)")

    # ---------- Browse Existing Entries ----------
    st.subheader("Browse existing entries")
    df_outputs, ok, err = sec8["load_approved_outputs"]()
    if not ok:
        st.caption(f"⚠️ {err or 'Could not load outputs.'}")
        df_outputs = pd.DataFrame()

    # Tabela com colunas na ordem pedida
    order_cols = ["project","output_country","output_city","output_type","output_data_type","output_desc"]
    if df_outputs.empty:
        st.info("No outputs to display yet.")
        table_df = pd.DataFrame(columns=order_cols)
    else:
        for c in order_cols:
            if c not in df_outputs.columns:
                df_outputs[c] = ""
        table_df = df_outputs.copy()

    # chave humana para seleção
    def _mk_output_key(row: pd.Series) -> str:
        return " || ".join([
            str(row.get("project","")).strip() or "(no project)",
            str(row.get("output_title","")).strip() or "(no title)",
            str(row.get("output_country","")).strip() or "—",
            str(row.get("output_city","")).strip() or "—",
        ])

    if not table_df.empty:
        table_df["__key__"] = table_df.apply(_mk_output_key, axis=1)
        st.dataframe(
            table_df[order_cols],
            use_container_width=True,
            hide_index=True
        )
        options_out = table_df["__key__"].tolist()
    else:
        options_out = []

    selected_key_out = st.selectbox(
        "Select a row to edit or delete (loads into the submission form below)",
        options=[""] + options_out,
        index=0
    )

    colx, coly = st.columns([1, 1])

    if selected_key_out and not table_df.empty:
        picked = table_df[table_df["__key__"] == selected_key_out]
    else:
        picked = pd.DataFrame()

    # Estado isolado desta seção
    if "_OUT_edit_mode" not in st.session_state:
        st.session_state["_OUT_edit_mode"] = False
    if "_OUT_before" not in st.session_state:
        st.session_state["_OUT_before"] = {}

    with colx:
        if selected_key_out and not picked.empty and st.button("✎ Edit this output", use_container_width=True):
            sel = picked.iloc[0].to_dict()
            st.session_state["_OUT_edit_mode"] = True
            st.session_state["_OUT_before"] = sel
            # prefill
            st.session_state["OUT_pref_project"] = sel.get("project","")
            st.session_state["OUT_pref_title"] = sel.get("output_title","")
            st.session_state["OUT_pref_type"] = sel.get("output_type","")
            st.session_state["OUT_pref_dtype"] = sel.get("output_data_type","")
            st.session_state["OUT_pref_url"] = sel.get("output_url","")
            st.session_state["OUT_pref_country"] = sel.get("output_country","")
            st.session_state["OUT_pref_city"] = sel.get("output_city","")
            st.session_state["OUT_pref_year"] = sel.get("output_year","")
            st.session_state["OUT_pref_desc"] = sel.get("output_desc","")
            st.session_state["OUT_pref_contact"] = sel.get("output_contact","")
            st.session_state["OUT_pref_email"] = sel.get("output_email","")
            st.session_state["OUT_pref_project_url"] = sel.get("project_url","")
            st.info("Loaded into the submission form below. Make your changes and submit to queue an **edit**.")
            st.rerun()

    with coly:
        if selected_key_out and not picked.empty and st.button("🗑 Request deletion (output)", use_container_width=True, type="secondary"):
            st.session_state["_OUT_delete_key"] = selected_key_out

    # Deletion request modal
    if st.session_state.get("_OUT_delete_key"):
        st.markdown("### 🗑 Request deletion (output)")
        st.warning(f"You are requesting deletion of: `{st.session_state['_OUT_delete_key']}`")
        target_sel = table_df[table_df["__key__"] == st.session_state["_OUT_delete_key"]]
        if target_sel.empty:
            st.error("Selected row not found anymore. Please refresh.")
        else:
            full_row = target_sel.iloc[0].to_dict()
            del_reason = st.text_area("Please describe the reason for deletion", placeholder="Explain why this output should be removed.")
            del_email = st.text_input("Your email (required for follow-up)", key="OUT_del_email", placeholder="name@org.org")
            c1, c2 = st.columns([1, 4])
            with c1:
                confirm = st.button("Confirm deletion request", type="primary", key="OUT_confirm_del")
            with c2:
                cancel  = st.button("Cancel", key="OUT_cancel_del")
            if cancel:
                st.session_state.pop("_OUT_delete_key", None)
                st.rerun()
            if confirm:
                if not del_email.strip():
                    st.error("Please provide your email.")
                elif not del_reason.strip():
                    st.error("Please provide a reason for deletion.")
                else:
                    payload = {
                        "project": full_row.get("project",""),
                        "output_title": full_row.get("output_title",""),
                        "output_type": full_row.get("output_type",""),
                        "output_data_type": full_row.get("output_data_type",""),
                        "output_url": full_row.get("output_url",""),
                        "output_country": full_row.get("output_country",""),
                        "output_city": full_row.get("output_city",""),
                        "output_year": full_row.get("output_year",""),
                        "output_desc": full_row.get("output_desc",""),
                        "output_contact": full_row.get("output_contact",""),
                        "output_email": full_row.get("output_email",""),
                        "project_url": full_row.get("project_url",""),
                        "submitter_email": del_email.strip(),
                        "is_edit": True,
                        "edit_target": str(full_row.get("output_title","")),
                        "edit_request": f"Request deletion — {del_reason.strip()}",
                    }
                    ok_sheet, msg_sheet = sec8["append_output_to_sheet"](payload)
                    if ok_sheet:
                        try_send_email_via_emailjs({
                            "project_name": payload["project"],
                            "entries": f"{payload['output_country']} — {payload['output_city']}",
                            "status": "",
                            "years": payload.get("output_year",""),
                            "url": payload.get("output_url",""),
                            "submitter_email": payload["submitter_email"],
                            "is_edit": "yes",
                            "edit_target": payload["edit_target"],
                            "edit_request": payload["edit_request"],
                        })
                        st.success("✅ Deletion request submitted for review.")
                        st.session_state.pop("_OUT_delete_key", None)
                        sec8["load_approved_outputs"].clear()
                        st.rerun()
                    else:
                        st.error(f"⚠️ Failed to record deletion request: {msg_sheet}")

    st.markdown("---")
    st.subheader("Add / Edit Entry (goes to review queue)")

    # Países: "Global" primeiro
    def _countries_with_global_first(mapping: dict):
        names = list(mapping.keys())
        if "Global" in names:
            return ["Global"] + sorted([n for n in names if n != "Global"])
        else:
            return ["Global"] + sorted(names)

    countries_options = _countries_with_global_first(COUNTRY_CENTER_FULL)

    with st.form("OUT_form", clear_on_submit=False):
        if st.session_state.get("_OUT_edit_mode"):
            st.markdown("🟦 **Editing an existing output** — this will queue an **edit** for review.")

        project_sel = st.selectbox(
            "Project Name",
            options=sec8["PROJECT_OPTIONS"],
            index=sec8["PROJECT_OPTIONS"].index(st.session_state.get("OUT_pref_project","IDEAMAPS Networking Grant"))
                  if st.session_state.get("OUT_pref_project") in sec8["PROJECT_OPTIONS"] else 0
        )

        output_title = st.text_input("Output Name", value=st.session_state.get("OUT_pref_title",""))

        output_type = st.selectbox(
            "Output Type",
            options=sec8["OUTPUT_TYPE_OPTIONS"],
            index=sec8["OUTPUT_TYPE_OPTIONS"].index(st.session_state.get("OUT_pref_type","Dataset"))
                  if st.session_state.get("OUT_pref_type") in sec8["OUTPUT_TYPE_OPTIONS"] else 0
        )

        if output_type == "Dataset":
            output_data_type = st.selectbox(
                "Data type",
                options=sec8["DATA_TYPE_OPTIONS"],
                index=sec8["DATA_TYPE_OPTIONS"].index(st.session_state.get("OUT_pref_dtype","Spatial (eg shapefile)"))
                      if st.session_state.get("OUT_pref_dtype") in sec8["DATA_TYPE_OPTIONS"] else 0
            )
        else:
            output_data_type = ""

        output_url = st.text_input("Output URL (optional)", value=st.session_state.get("OUT_pref_url",""))

        output_country = st.selectbox(
            "Geographic coverage of output",
            options=countries_options,
            index=countries_options.index(st.session_state.get("OUT_pref_country","Global"))
                  if st.session_state.get("OUT_pref_country") in countries_options else 0
        )

        output_city = st.text_input(
            "",
            placeholder="City (optional — follows formatting of the current 'Cities covered' question)",
            value=st.session_state.get("OUT_pref_city","")
        )

        output_year = st.text_input("Year of output release", value=st.session_state.get("OUT_pref_year",""))
        output_desc = st.text_area("Short description of output", value=st.session_state.get("OUT_pref_desc",""))
        output_contact = st.text_input("Name & institution of person responsible", value=st.session_state.get("OUT_pref_contact",""))
        output_email = st.text_input("Email of person responsible", value=st.session_state.get("OUT_pref_email",""))
        project_url = st.text_input("Project URL (optional)", value=st.session_state.get("OUT_pref_project_url",""))

        submitter_email_outputs = st.text_input("Submitter email (required for review)", placeholder="name@org.org")

        submitted_output = st.form_submit_button("Submit output for review")

    def _summarize_output_changes(before: dict, after: dict) -> str:
        cols = [
            "project","output_title","output_type","output_data_type","output_url",
            "output_country","output_city","output_year","output_desc",
            "output_contact","output_email","project_url"
        ]
        changes = []
        for c in cols:
            if str(before.get(c,"")) != str(after.get(c,"")):
                bv = str(before.get(c,""))
                av = str(after.get(c,""))
                if len(bv) > 120: bv = bv[:117]+"…"
                if len(av) > 120: av = av[:117]+"…"
                changes.append(f"{c}: '{bv}' → '{av}'")
        return "; ".join(changes) if changes else "No visible change"

    if submitted_output:
        if not submitter_email_outputs.strip():
            st.warning("Please provide the submitter email.")
        elif not output_title.strip():
            st.warning("Please provide the Output Name.")
        else:
            after_payload = {
                "project": project_sel,
                "output_title": output_title,
                "output_type": output_type,
                "output_data_type": output_data_type,
                "output_url": output_url,
                "output_country": output_country,
                "output_city": output_city,
                "output_year": output_year,
                "output_desc": output_desc,
                "output_contact": output_contact,
                "output_email": output_email,
                "project_url": project_url,
                "submitter_email": submitter_email_outputs,
            }
            if st.session_state.get("_OUT_edit_mode"):
                before = st.session_state.get("_OUT_before", {})
                edit_target = before.get("output_title", output_title)
                edit_request = _summarize_output_changes(before, after_payload)
                payload = {**after_payload, "is_edit": True, "edit_target": edit_target, "edit_request": edit_request}
            else:
                payload = {**after_payload, "is_edit": False, "edit_target": "", "edit_request": "New output submission"}

            ok_sheet, msg_sheet = sec8["append_output_to_sheet"](payload)
            if ok_sheet:
                st.success("✅ Output submission saved to review queue.")
                st.session_state["_OUT_edit_mode"] = False
                st.session_state["_OUT_before"] = {}
                try_send_email_via_emailjs({
                    "project_name": payload["project"],
                    "entries": f"{payload['output_country']} — {payload['output_city']}",
                    "status": "",
                    "years": payload.get("output_year",""),
                    "url": payload.get("output_url",""),
                    "submitter_email": payload["submitter_email"],
                    "is_edit": "yes" if payload["is_edit"] else "no",
                    "edit_target": payload.get("edit_target",""),
                })
                sec8["load_approved_outputs"].clear()
            else:
                st.error(f"⚠️ {msg_sheet}")

# =============================================================================
# 10) RENDERIZA A NOVA SEÇÃO DE OUTPUTS (CHAMADA EXPLÍCITA)
#     >>> esta linha faz a "Outputs (beta)" aparecer no site <<<
# =============================================================================
section_9_outputs_ui(
    st=st,
    COUNTRY_CENTER_FULL=COUNTRY_CENTER_FULL,
    try_send_email_via_emailjs=try_send_email_via_emailjs,
    _open_or_create_worksheet=_open_or_create_worksheet,
    datetime=datetime,
    pd=pd,
)
