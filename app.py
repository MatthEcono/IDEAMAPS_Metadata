# app.py
import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from datetime import datetime
from pathlib import Path
import re
import uuid
import requests
import gspread
from google.oauth2.service_account import Credentials

# =============================================================================
# 0) PAGE CONFIG
# =============================================================================
st.set_page_config(
    page_title="IDEAMAPS Global Metadata Explorer",
    layout="wide",
    page_icon="🌍",
)

# =============================================================================
# 1) GOOGLE SHEETS / EMAILJS
# =============================================================================
REQUIRED_HEADERS = [
    "country","city","lat","lon","project_name","years","status",
    "data_types","description","contact","access","url",
    "submitter_email","is_edit","edit_target","edit_request",
    "action","edit_group","previous_key","approved","created_at"
]
MESSAGE_HEADERS = ["name","email","message","approved","created_at"]

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

def _gs_open_worksheet(ws_name: str):
    client, err = _gs_client()
    if err or client is None:
        return None, err or "Client unavailable."
    try:
        ss_id = st.secrets.get("SHEETS_SPREADSHEET_ID")
        if not ss_id:
            return None, "Please set SHEETS_SPREADSHEET_ID in secrets."
        ws = client.open_by_key(ss_id).worksheet(ws_name)
        return ws, None
    except Exception as e:
        return None, f"Failed to open worksheet '{ws_name}': {e}"

def _ws_projects():
    ws_name = st.secrets.get("SHEETS_WORKSHEET_NAME") or "projects"
    return _gs_open_worksheet(ws_name)

def _ws_messages():
    ws_name = st.secrets.get("SHEETS_MESSAGES_WS") or "messages"
    return _gs_open_worksheet(ws_name)

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
# 2) LOAD COUNTRIES CSV
# =============================================================================
COUNTRY_CSV_PATH = Path(__file__).parent / "country-coord.csv"

@st.cache_data(show_spinner=False)
def load_country_centers():
    df = pd.read_csv(COUNTRY_CSV_PATH, dtype=str, encoding="utf-8", on_bad_lines="skip")
    df.columns = [c.strip().lower() for c in df.columns]
    df["lat"] = df[df.columns[1]].apply(_parse_number_loose)
    df["lon"] = df[df.columns[2]].apply(_parse_number_loose)
    df = df.dropna(subset=["lat", "lon"])
    mapping = {row[df.columns[0]]: (float(row["lat"]), float(row["lon"])) for _, row in df.iterrows()}
    return mapping, df

COUNTRY_CENTER_FULL, _df_countries = load_country_centers()

# =============================================================================
# 3) HEADER
# =============================================================================
st.markdown(
    """
    <div style="background: linear-gradient(90deg,#0f172a,#1e293b,#0f172a);
        padding:1.2rem 1.5rem;border-radius:0.75rem;border:1px solid #334155;
        margin-bottom:1rem;">
        <div style="color:#fff;font-size:1.2rem;font-weight:600;">
            IDEAMAPS Global Metadata Explorer 🌍
        </div>
        <div style="color:#94a3b8;font-size:0.85rem;line-height:1.3;">
            Living catalogue of projects and datasets (spatial / quantitative / qualitative) produced by the IDEAMAPS network and partners.
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# =============================================================================
# 4) LOAD APPROVED PROJECTS
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

# 🔄 FIX: button now AFTER function definition
if st.sidebar.button("🔄 Check updates"):
    try:
        load_approved_projects.clear()
        load_country_centers.clear()
    except Exception:
        pass
    ensure_headers()
    ensure_lat_lon_text_columns()
    ensure_message_headers()
    st.session_state["_last_refresh"] = datetime.utcnow().isoformat()
    st.rerun()

# (continue restante do app normalmente...)
