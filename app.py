# =============================================================================
# IDEAMAPS Global Metadata Explorer 🌍
# =============================================================================
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
# 0) PAGE CONFIG + LOGO
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

if _logo_b64:
    st.markdown(
        f"""
        <link rel="icon" href="data:image/png;base64,{_logo_b64}">
        <link rel="apple-touch-icon" href="data:image/png;base64,{_logo_b64}">
        """,
        unsafe_allow_html=True,
    )

# =============================================================================
# 1) CONSTANTES / SHEETS
# =============================================================================
REQUIRED_HEADERS = [
    "country","city","lat","lon","project_name","years","status",
    "data_types","description","contact","access","url",
    "submitter_email","is_edit","edit_target","edit_request",
    "approved","created_at"
]
MESSAGES_SHEET_NAME = "Public Messages"
MESSAGE_HEADERS = ["name","email","message","approved","created_at"]

@st.cache_resource(show_spinner=False)
def _gs_client():
    try:
        creds_info = st.secrets.get("gcp_service_account")
        if not creds_info:
            return None, "Missing gcp_service_account in secrets."
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
        client = gspread.authorize(creds)
        return client, None
    except Exception as e:
        return None, f"Google Sheets auth error: {e}"

def _open_or_create_worksheet(ws_name: str, init_headers: Optional[List[str]] = None):
    client, err = _gs_client()
    if err or client is None:
        return None, err
    ss_id = st.secrets.get("SHEETS_SPREADSHEET_ID")
    if not ss_id:
        return None, "Missing SHEETS_SPREADSHEET_ID in secrets."
    try:
        ss = client.open_by_key(ss_id)
        try:
            ws = ss.worksheet(ws_name)
            return ws, None
        except gspread.exceptions.WorksheetNotFound:
            ncols = max(10, len(init_headers) if init_headers else 10)
            ws = ss.add_worksheet(title=ws_name, rows=200, cols=ncols)
            if init_headers:
                ws.update("A1", [init_headers])
            return ws, None
    except Exception as e:
        return None, f"Worksheet error: {e}"

def _ws_projects(): return _open_or_create_worksheet("projects", REQUIRED_HEADERS)
def _ws_messages(): return _open_or_create_worksheet(MESSAGES_SHEET_NAME, MESSAGE_HEADERS)

def _col_letter(idx0: int) -> str:
    n = idx0 + 1; s = ""
    while n: n, r = divmod(n - 1, 26); s = chr(r + 65) + s
    return s

# =============================================================================
# 2) LOAD COUNTRY CENTERS
# =============================================================================
COUNTRY_CSV_PATH = APP_DIR / "country-coord.csv"

@st.cache_data(show_spinner=False)
def load_country_centers():
    df = pd.read_csv(COUNTRY_CSV_PATH, dtype=str, encoding="utf-8", on_bad_lines="skip")
    df.columns = [c.strip().lower() for c in df.columns]
    df["lat"] = df["latitude (average)"].astype(float)
    df["lon"] = df["longitude (average)"].astype(float)
    mapping = {row["country"]: (row["lat"], row["lon"]) for _, row in df.iterrows()}
    return mapping, df

COUNTRY_CENTER_FULL, _ = load_country_centers()

# =============================================================================
# 3) HEADER
# =============================================================================
header_html = f"""
<div style='display:flex;align-items:center;gap:14px;
background:linear-gradient(90deg,#0f172a,#1e293b,#0f172a);
border-radius:12px;padding:12px 16px;margin-bottom:14px;'>
  <div style='width:44px;height:44px;border-radius:8px;overflow:hidden;'>
    {"<img src='data:image/png;base64,"+_logo_b64+"' style='width:100%;height:100%;object-fit:cover;'/>" if _logo_b64 else "🌍"}
  </div>
  <div>
    <div style='color:#fff;font-weight:700;font-size:1.1rem;'>IDEAMAPS Global Metadata Explorer</div>
    <div style='color:#94a3b8;font-size:0.9rem;'>Living catalogue of projects and datasets produced by the IDEAMAPS network and partners.</div>
  </div>
</div>
"""
st.markdown(header_html, unsafe_allow_html=True)
if _logo_img is not None:
    st.sidebar.image(_logo_img, caption="IDEAMAPS", use_container_width=True)

# =============================================================================
# 4) MAPA DE PROJETOS
# =============================================================================
@st.cache_data(show_spinner=False)
def load_approved_projects():
    ws, err = _ws_projects()
    if err or ws is None:
        return pd.DataFrame(), False, err
    try:
        df = pd.DataFrame(ws.get_all_records())
        if df.empty: return pd.DataFrame(), False, "Empty sheet."
        df = df[df["approved"].astype(str).str.upper().eq("TRUE")].copy()
        for c in ["lat","lon"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        return df, True, None
    except Exception as e:
        return pd.DataFrame(), False, f"Read error: {e}"

if st.sidebar.button("🔄 Check updates"):
    load_approved_projects.clear(); load_country_centers.clear(); st.rerun()

df_projects, ok, msg = load_approved_projects()
if not ok and msg: st.caption(f"⚠️ {msg}")

if not df_projects.empty:
    m = folium.Map(location=[df_projects["lat"].mean(), df_projects["lon"].mean()],
                   zoom_start=2, tiles="CartoDB dark_matter")
    for _, r in df_projects.iterrows():
        tooltip = f"<b>{r['project_name']}</b><br>{r['city']}, {r['country']}"
        folium.CircleMarker(
            location=[r["lat"], r["lon"]], radius=6,
            color="#38bdf8" if r["status"].lower() == "active" else "#facc15",
            fill=True, fill_opacity=0.9, popup=tooltip
        ).add_to(m)
    st_folium(m, height=520)
else:
    st.info("No approved projects yet.")
st.markdown("---")
# =============================================================================
# 5) BROWSE EXISTING ENTRIES (EDIT / DELETE)
# =============================================================================
st.subheader("Browse existing entries")

if df_projects.empty:
    st.info("No data to display yet.")
else:
    def _mk_key(row):
        return " || ".join([
            str(row.get("project_name","")).strip(),
            str(row.get("country","")).strip(),
            str(row.get("city","")).strip()
        ])

    table_cols = ["project_name","country","city","years","data_types","contact","access","url","lat","lon"]
    df_projects["__key__"] = df_projects.apply(_mk_key, axis=1)
    df_view = df_projects[table_cols + ["__key__"]].copy()

    st.dataframe(df_view.drop(columns="__key__"), use_container_width=True, hide_index=True)

    options = df_view["__key__"].tolist()
    selected_key = st.selectbox("Select a row to edit or delete", [""] + options, index=0)
    col_a, col_b = st.columns([1,1])

    # EDIT BUTTON
    with col_a:
        if selected_key and st.button("✎ Edit this row", use_container_width=True):
            sel = df_view[df_view["__key__"] == selected_key].iloc[0].to_dict()
            idx_sel = df_view.index[df_view["__key__"] == selected_key][0]

            st.session_state["_edit_mode"] = True
            st.session_state["_before_row"] = df_projects.loc[idx_sel].to_dict()
            st.session_state["prefill_project_name"] = sel.get("project_name","")
            st.session_state["countries_sel"] = [sel.get("country","")]
            st.session_state["city_list"] = [f"{sel['country']} — {sel['city']}"]
            st.session_state["prefill_years"] = sel.get("years","")
            st.session_state["prefill_data_types"] = sel.get("data_types","")
            st.session_state["prefill_description"] = df_projects.loc[idx_sel].get("description","")
            st.session_state["prefill_contact"] = sel.get("contact","")
            st.session_state["prefill_access"] = sel.get("access","")
            st.session_state["prefill_url"] = sel.get("url","")
            st.session_state["prefill_lat"] = sel.get("lat","")
            st.session_state["prefill_lon"] = sel.get("lon","")
            st.info("Loaded into the submission form below for editing.")
            st.rerun()

    # DELETE BUTTON
    with col_b:
        if selected_key and st.button("🗑 Request deletion", use_container_width=True, type="secondary"):
            st.session_state["_delete_target"] = selected_key

    # MOTIVO DE EXCLUSÃO
    if st.session_state.get("_delete_target"):
        st.markdown("### 🗑 Request deletion")
        st.warning(f"Requesting deletion of: `{st.session_state['_delete_target']}`")
        reason = st.text_area("Reason for deletion", placeholder="Explain why this entry should be removed.")
        confirm = st.button("Confirm deletion request", type="primary")

        if confirm:
            target_key = st.session_state["_delete_target"]
            sel = df_view[df_view["__key__"] == target_key].iloc[0].to_dict()
            payload = {
                "country": sel.get("country",""),
                "city": sel.get("city",""),
                "lat": sel.get("lat",""),
                "lon": sel.get("lon",""),
                "project_name": sel.get("project_name",""),
                "years": sel.get("years",""),
                "status": "",
                "data_types": sel.get("data_types",""),
                "description": sel.get("description",""),
                "contact": sel.get("contact",""),
                "access": sel.get("access",""),
                "url": sel.get("url",""),
                "submitter_email": st.text_input("Your email (for follow-up)", key="del_email", placeholder="name@org.org"),
                "is_edit": True,
                "edit_target": sel.get("project_name",""),
                "edit_request": f"Request deletion — {reason.strip() or '(no reason provided)'}",
            }
            ok, msg = _open_or_create_worksheet("projects")[0].append_row(list(payload.values()), value_input_option="RAW")
            st.success("✅ Deletion request submitted for review.")
            st.session_state.pop("_delete_target", None)

st.markdown("---")

# =============================================================================
# 6) ADD / EDIT PROJECT — with GeoNames integration
# =============================================================================
st.header("Add / Edit project (goes to review queue)")

SELECT_PLACEHOLDER = "— Select a country —"

if "city_list" not in st.session_state: st.session_state.city_list = []
if "_edit_mode" not in st.session_state: st.session_state["_edit_mode"] = False
if "_before_row" not in st.session_state: st.session_state["_before_row"] = {}
if "_reset_city_inputs" not in st.session_state: st.session_state["_reset_city_inputs"] = False

def _add_city_entry(country, city):
    if country and city:
        pair = f"{country} — {city}"
        if pair not in st.session_state.city_list:
            st.session_state.city_list.append(pair)

# limpa campos antigos
if st.session_state.get("_reset_city_inputs", False):
    for k in ["city_to_add","city_to_add_free","country_for_city"]:
        st.session_state.pop(k, None)
    st.session_state["_reset_city_inputs"] = False

countries_options = sorted(COUNTRY_CENTER_FULL.keys())
st.session_state.countries_sel = st.multiselect(
    "Implementation countries (one or more)",
    options=countries_options,
    default=st.session_state.get("countries_sel", []),
)

options_for_city = st.session_state.get("countries_sel", [])

@st.cache_data(show_spinner=False)
def fetch_cities_geonames(country_name):
    try:
        username = "mattheusr36"
        url = f"http://api.geonames.org/searchJSON?q={country_name}&featureClass=P&maxRows=200&username={username}"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return sorted({g["name"] for g in data.get("geonames", []) if "name" in g})
        return []
    except Exception:
        return []

with st.form("add_project_form", clear_on_submit=False):
    if st.session_state["_edit_mode"]:
        st.markdown("🟦 **Editing an existing entry** — your submission will be queued as an **edit** to the catalogue.")

    new_name = st.text_input("Project name", value=st.session_state.get("prefill_project_name",""))
    submitter_email = st.text_input("Submitter email (required)", placeholder="name@org.org")

    st.markdown("**Cities covered**")
    st.caption("Select or type a city. Suggestions come from GeoNames (top 200 cities).")

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
        suggested_cities = []
        if selected_country_for_city and selected_country_for_city != SELECT_PLACEHOLDER:
            with st.spinner("Fetching cities from GeoNames..."):
                suggested_cities = fetch_cities_geonames(selected_country_for_city)
        city_suggestion = st.selectbox(
            "City (choose from list or type manually below)",
            options=[""] + suggested_cities,
            index=0,
            key="city_to_add"
        )
        city_free = st.text_input("Or type a new city name", key="city_to_add_free")

    with colc3:
        st.write("")
        add_one = st.form_submit_button("➕ Add to this country", use_container_width=True, disabled=not options_for_city)

    # adiciona cidade
    if add_one:
        selected_country = st.session_state.get("country_for_city")
        city_input = (st.session_state.get("city_to_add","") or st.session_state.get("city_to_add_free","")).strip()
        if not selected_country or selected_country == SELECT_PLACEHOLDER:
            st.warning("Select a valid country.")
        elif not city_input:
            st.warning("Select or type a city.")
        else:
            _add_city_entry(selected_country, city_input)
            st.session_state["_reset_city_inputs"] = True
            st.rerun()

    add_all = st.form_submit_button("➕ Add to ALL selected countries", use_container_width=True)
    if add_all:
        city_input = (st.session_state.get("city_to_add","") or st.session_state.get("city_to_add_free","")).strip()
        if city_input:
            for ctry in options_for_city:
                _add_city_entry(ctry, city_input)
        st.session_state["_reset_city_inputs"] = True
        st.rerun()
    # =============================================================
    # CAMPOS ADICIONAIS DO FORMULÁRIO
    # =============================================================
    if st.session_state.city_list:
        st.caption("Cities added (country — city):")
        to_remove_idx = None
        for i, item in enumerate(st.session_state.city_list):
            c1, c2 = st.columns([6,1])
            with c1: st.write(f"- {item}")
            with c2:
                if st.form_submit_button("Remove", key=f"rm_{i}"):
                    to_remove_idx = i
        if to_remove_idx is not None:
            st.session_state.city_list.pop(to_remove_idx)
            st.rerun()

        if st.checkbox("Clear all cities"):
            st.session_state.city_list = []

    new_years  = st.text_input("Years (e.g. 2022–2024)", value=st.session_state.get("prefill_years",""))
    new_types  = st.text_area("Data types", value=st.session_state.get("prefill_data_types",""))
    new_desc   = st.text_area("Short description", value=st.session_state.get("prefill_description",""))
    new_contact= st.text_input("Contact / Responsible institution", value=st.session_state.get("prefill_contact",""))
    new_access = st.text_input("Access / License / Ethics", value=st.session_state.get("prefill_access",""))
    new_url    = st.text_input("Project URL (optional)", value=st.session_state.get("prefill_url",""))
    submitted  = st.form_submit_button("Submit for review")

# =============================================================================
# 7) SAVE SUBMISSION TO GOOGLE SHEETS
# =============================================================================
def append_submission_to_sheet(payload: dict) -> tuple[bool, str]:
    ws, err = _ws_projects()
    if err or ws is None:
        return False, err
    try:
        header = ws.row_values(1)
        if not header:
            ws.update("A1", [REQUIRED_HEADERS])
            header = REQUIRED_HEADERS
        row = [payload.get(col, "") for col in header]
        ws.append_row(row, value_input_option="RAW")
        return True, "Saved."
    except Exception as e:
        return False, str(e)

def _summarize_changes(before: dict, after: dict) -> str:
    changes = []
    for c in ["country","city","lat","lon","project_name","years","status","data_types","description","contact","access","url"]:
        bv = str(before.get(c,"") or "")
        av = str(after.get(c,"") or "")
        if bv != av:
            changes.append(f"{c}: '{bv}' → '{av}'")
    return "; ".join(changes) if changes else "No visible change"

if submitted:
    if not st.session_state.get("countries_sel"):
        st.warning("Please select at least one implementation country.")
    elif not (st.session_state.city_list or st.session_state.get("countries_sel")):
        st.warning("Please add at least one (country — city) pair.")
    elif not submitter_email.strip():
        st.warning("Please provide a submitter email.")
    elif not new_name.strip():
        st.warning("Please provide a Project name.")
    else:
        total_rows = 0
        ok_all = True
        msg_any = None
        pairs = st.session_state.city_list[:] if st.session_state.city_list else [f"{c} — " for c in st.session_state.get("countries_sel", [])]
        before = st.session_state.get("_before_row", {}) if st.session_state.get("_edit_mode") else {}

        for pair in pairs:
            if "—" not in pair:
                continue
            country, city = [p.strip() for p in pair.split("—", 1)]
            lat, lon = COUNTRY_CENTER_FULL.get(country, (None, None))

            after_payload = {
                "country": country,
                "city": city,
                "lat": lat or "",
                "lon": lon or "",
                "project_name": new_name,
                "years": new_years,
                "status": "",
                "data_types": new_types,
                "description": new_desc,
                "contact": new_contact,
                "access": new_access,
                "url": new_url,
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

# =============================================================================
# 8) COMMUNITY MESSAGE BOARD
# =============================================================================
st.markdown("---")
st.header("Community message board")

with st.form("message_form"):
    msg_name = st.text_input("Your full name", placeholder="First Last")
    msg_email = st.text_input("Email (optional)", placeholder="name@org.org")
    msg_text = st.text_area("Your message", placeholder="Write your note to the IDEAMAPS team...")
    send_msg = st.form_submit_button("Send message for approval")

def append_message_to_sheet(name: str, email: str, message: str) -> tuple[bool, str]:
    ws, err = _ws_messages()
    if err or ws is None:
        return False, err
    try:
        header = ws.row_values(1)
        if not header:
            ws.update("A1", [MESSAGE_HEADERS])
            header = MESSAGE_HEADERS
        row = {
            "name": name.strip(),
            "email": (email or "").strip(),
            "message": message.strip(),
            "approved": "FALSE",
            "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
        values = [row.get(c, "") for c in header]
        ws.append_row(values, value_input_option="RAW")
        return True, "Message queued."
    except Exception as e:
        return False, str(e)

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
        df = pd.DataFrame(ws.get_all_records())
        if df.empty:
            return pd.DataFrame(), True, None
        df["approved"] = df["approved"].astype(str).str.upper().isin(["TRUE","YES","1"])
        if "created_at" in df.columns:
            df = df.sort_values("created_at", ascending=False)
        return df, True, None
    except Exception as e:
        return pd.DataFrame(), False, str(e)

st.subheader("Public messages")
df_msgs, ok_msgs, err_msgs = _load_messages()
if not ok_msgs:
    st.caption(f"⚠️ {err_msgs}")
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
                st.markdown(
                    f"""
                    <div style='border:1px solid #334155;background:#0b1220;border-radius:12px;padding:12px;margin-bottom:10px;'>
                      <div style='color:#e5e7eb;font-weight:600;'>{r.get('name','Anonymous')}
                        <span style='color:#64748b;font-size:0.85rem;'> {r.get('email','')}</span>
                      </div>
                      <div style='color:#94a3b8;font-size:0.75rem;'>{r.get('created_at','')}</div>
                      <div style='color:#cbd5e1;margin-top:8px;white-space:pre-wrap;'>{r.get('message','')}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
    with tab_all:
        show_cols = ["name","email","message","approved","created_at"]
        present = [c for c in show_cols if c in df_msgs.columns]
        st.dataframe(df_msgs[present], use_container_width=True, hide_index=True)
