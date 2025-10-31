# app.py
import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from datetime import datetime
from pathlib import Path
import re
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
    "approved","created_at"
]

@st.cache_resource(show_spinner=False)
def _gs_worksheet():
    try:
        ss_id = st.secrets.get("SHEETS_SPREADSHEET_ID")
        ws_name = st.secrets.get("SHEETS_WORKSHEET_NAME")
        creds_info = st.secrets.get("gcp_service_account")
        if not (ss_id and ws_name and creds_info):
            return None, "Please configure SHEETS_SPREADSHEET_ID, SHEETS_WORKSHEET_NAME and gcp_service_account in secrets."
        scopes = ["https://www.googleapis.com/auth/spreadsheets",
                  "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
        client = gspread.authorize(creds)
        ws = client.open_by_key(ss_id).worksheet(ws_name)
        return ws, None
    except Exception as e:
        return None, f"Google Sheets connection error: {e}"

def _col_letter(idx0: int) -> str:
    n = idx0 + 1
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(r + 65) + s
    return s

def ensure_lat_lon_text_columns():
    """Force 'lat' and 'lon' columns to Plain text (TEXT) in Sheets."""
    ws, err = _gs_worksheet()
    if err or ws is None:
        return False, err or "Worksheet unavailable."
    try:
        header = ws.row_values(1)
        if not header:
            return False, "Sheet header (row 1) is empty."
        hdr_lower = [h.strip().lower() for h in header]
        if "lat" not in hdr_lower or "lon" not in hdr_lower:
            return False, "Header must contain 'lat' and 'lon'."
        lat_idx = hdr_lower.index("lat")
        lon_idx = hdr_lower.index("lon")
        lat_col = _col_letter(lat_idx)
        lon_col = _col_letter(lon_idx)
        ws.format(f"{lat_col}:{lat_col}", {"numberFormat": {"type": "TEXT"}})
        ws.format(f"{lon_col}:{lon_col}", {"numberFormat": {"type": "TEXT"}})
        return True, "lat/lon columns set to TEXT."
    except Exception as e:
        return False, f"Failed to format columns: {e}"

def ensure_headers(required_headers=REQUIRED_HEADERS):
    """Ensure row 1 contains all required columns, appending missing ones."""
    ws, err = _gs_worksheet()
    if err or ws is None:
        return False, err or "Worksheet unavailable."
    try:
        header = ws.row_values(1)
        header = [h.strip() for h in header] if header else []
        missing = [h for h in required_headers if h not in header]
        if missing:
            new_header = header + missing
            ws.update('A1', [new_header])
        return True, "Header OK."
    except Exception as e:
        return False, f"Failed to adjust header: {e}"

def _parse_number_loose(x):
    """
    Robust number parser:
      - strips quotes/spaces
      - uses last comma/dot as decimal separator
      - removes thousands separators
    """
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

@st.cache_data(show_spinner=False)
def load_approved_projects():
    """Load approved==TRUE, normalize lat/lon (robust to quotes/locale)."""
    ws, err = _gs_worksheet()
    if err or ws is None:
        return pd.DataFrame(), False, err
    try:
        rows = ws.get_all_records()
        df = pd.DataFrame(rows)
        if df.empty:
            return pd.DataFrame(), False, "Sheet is empty."

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
        return pd.DataFrame(), False, f"Error reading sheet: {e}"

def append_submission_to_sheet(payload: dict) -> tuple[bool, str]:
    """Append a row to the sheet (plain text for lat/lon; no leading quotes)."""
    ws, err = _gs_worksheet()
    if err or ws is None:
        return False, err
    try:
        ensure_headers()
        ensure_lat_lon_text_columns()

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

# =============================================================================
# 2) COUNTRIES CSV (LOCAL ONLY)
# =============================================================================
COUNTRY_CSV_PATH = Path(__file__).parent / "country-coord.csv"

@st.cache_data(show_spinner=False)
def load_country_centers():
    """Read country-coord.csv and return {country: (lat, lon)} and DF."""
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
# 3) HEADER + REFRESH
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

if st.sidebar.button("🔄 Check updates"):
    load_approved_projects.clear()
    load_country_centers.clear()
    ensure_headers()
    ensure_lat_lon_text_columns()
    st.session_state["_last_refresh"] = datetime.utcnow().isoformat()
    st.rerun()

# =============================================================================
# 4) LOAD PROJECTS + MAP (AGGREGATED BY COUNTRY)
# =============================================================================
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
    if "lat" in df_projects.columns: df_projects["lat"] = df_projects["lat"].apply(_as_float)
    if "lon" in df_projects.columns: df_projects["lon"] = df_projects["lon"].apply(_as_float)
    df_map = df_projects.dropna(subset=["lat", "lon"]).copy()

    if df_map.empty:
        st.info("No valid points to plot (missing lat/lon).")
    else:
        # group by country + lat/lon
        groups = df_map.groupby(["country","lat","lon"], as_index=False)

        m = folium.Map(
            location=[df_map["lat"].mean(), df_map["lon"].mean()],
            zoom_start=2,
            tiles="CartoDB dark_matter"
        )

        for (country, lat, lon), g in groups:
            # project_name -> {cities, urls}
            proj_dict = {}
            for _, r in g.iterrows():
                pname = str(r.get("project_name","")).strip() or "(unnamed project)"
                city = str(r.get("city","")).strip()
                url  = _clean_url(r.get("url",""))
                if pname not in proj_dict:
                    proj_dict[pname] = {"cities": set(), "urls": set()}
                if city:
                    proj_dict[pname]["cities"].add(city)
                if url:
                    proj_dict[pname]["urls"].add(url)

            # Build detailed HTML (for both tooltip and popup)
            lines = [
                "<div style='font-size:0.9rem;color:#fff;'>",
                f"<b>{country}</b>",
                "<ul style='padding-left:1rem;margin:0;'>"
            ]
            for pname, info in proj_dict.items():
                cities_txt = ", ".join(sorted(info["cities"])) if info["cities"] else "—"
                url_html = ""
                if info["urls"]:
                    u_any = sorted(info["urls"])[0]
                    url_html = f" — <a href='{u_any}' target='_blank' style='color:#38bdf8;'>link</a>"
                lines.append(f"<li><b>{pname}</b> — {cities_txt}{url_html}</li>")
            lines.append("</ul></div>")
            html_block = "".join(lines)

            any_active = any(str(x).lower()=="active" for x in g["status"].tolist())
            color = "#38bdf8" if any_active else "#facc15"

            folium.CircleMarker(
                location=[lat, lon],
                radius=6,
                color=color,
                fill=True,
                fill_opacity=0.9,
                # show full details on hover (tooltip) AND on click (popup)
                tooltip=folium.Tooltip(html_block, sticky=True, direction="top"),
                popup=folium.Popup(html_block, max_width=380),
            ).add_to(m)

        st_folium(m, height=520, width=None)
else:
    st.info("No approved projects found right now.")

# Table + downloads
if not df_projects.empty:
    st.subheader("Data (downloadable)")
    cols_show = [
        "country","city","lat","lon","project_name","years","status","url",
        "submitter_email","is_edit","edit_target","edit_request","created_at"
    ]
    show_cols = [c for c in cols_show if c in df_projects.columns]
    tbl = (df_projects[show_cols]
           .copy()
           .sort_values(["country","project_name","city"], na_position="last")
           .reset_index(drop=True))
    if "lat" in tbl.columns:
        tbl["lat"] = tbl["lat"].apply(lambda x: f"{float(x):.6f}" if pd.notna(x) else "")
    if "lon" in tbl.columns:
        tbl["lon"] = tbl["lon"].apply(lambda x: f"{float(x):.6f}" if pd.notna(x) else "")
    st.dataframe(tbl, use_container_width=True)

    csv_bytes = tbl.to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ Download CSV", data=csv_bytes, file_name="ideamaps_on_map.csv", mime="text/csv")

    html_bytes = tbl.to_html(index=False, escape=False).encode("utf-8")
    st.download_button("⬇️ Download HTML", data=html_bytes, file_name="ideamaps_on_map.html", mime="text/html")

st.markdown("---")

# =============================================================================
# 5) FORM: COUNTRY (outside) → CITY (inside)
# =============================================================================
st.header("Add new project (goes to review queue)")

if "city_list" not in st.session_state:
    st.session_state.city_list = []

def get_country_center(name: str):
    tpl = COUNTRY_CENTER_FULL.get(name)
    if tpl:
        try: return float(tpl[0]), float(tpl[1])
        except Exception: return None, None
    return None, None

def _add_city_entry(country, city):
    if country and city:
        pair = f"{country} — {city}"
        if pair not in st.session_state.city_list:
            st.session_state.city_list.append(pair)

# Countries (outside the form to update options live)
countries_options = sorted(COUNTRY_CENTER_FULL.keys())
st.session_state.countries_sel = st.multiselect(
    "Countries (one or more)",
    options=countries_options,
    default=st.session_state.get("countries_sel", []),
    help="Select all countries covered by this project."
)
options_for_city = st.session_state.get("countries_sel", [])

# Main form
with st.form("add_project_form", clear_on_submit=False):
    new_name = st.text_input("Project name", placeholder="e.g., IDEAMAPS Lagos / Urban Deprivation Mapping")

    # Submitter info & edit mode
    submitter_email = st.text_input("Submitter email (for follow-up / review)", placeholder="name@org.org")

    colc1, colc2, colc3 = st.columns([2, 2, 1])
    with colc1:
        selected_country_for_city = st.selectbox(
            "Select country for this city",
            options=options_for_city,
            index=0 if options_for_city else None,
            disabled=not bool(options_for_city),
            key="country_for_city",
        )
    with colc2:
        city_to_add = st.text_input("City (type name)", key="city_to_add")
    with colc3:
        st.write("")
        add_one = st.form_submit_button("➕ Add city", use_container_width=True, disabled=not bool(options_for_city))

    if add_one and selected_country_for_city and city_to_add.strip():
        _add_city_entry(selected_country_for_city, city_to_add.strip())

    add_all = st.form_submit_button(
        "➕ Add city to all selected countries",
        use_container_width=True,
        disabled=not (options_for_city and city_to_add.strip())
    )
    if add_all:
        for ctry in options_for_city:
            _add_city_entry(ctry, city_to_add.strip())

    if st.session_state.city_list:
        st.caption("Cities added (country — city):")
        for item in st.session_state.city_list:
            st.write(f"- {item}")
        if st.checkbox("Clear all cities"):
            st.session_state.city_list = []

    is_edit = st.checkbox("This is an update/edit to an existing entry", value=False)
    edit_target = ""
    edit_request = ""
    if is_edit:
        existing_projects = sorted(set(df_projects["project_name"].dropna().astype(str))) if not df_projects.empty else []
        edit_target = st.selectbox("Which project is this edit about?", options=[""] + existing_projects)
        edit_request = st.text_area("Describe the changes to apply")

    new_years  = st.text_input("Years (e.g. 2022–2024)")
    new_status = st.selectbox("Status", ["Active", "Legacy", "Completed", "Planning"])
    new_types  = st.text_area("Data types (Spatial? Quantitative? Qualitative?)")
    new_desc   = st.text_area("Short description")
    new_contact= st.text_input("Contact / Responsible institution")
    new_access = st.text_input("Access / License / Ethics")
    new_url    = st.text_input("Project URL (optional)")

    submitted = st.form_submit_button("Submit for review")

if submitted:
    if not new_name.strip():
        st.warning("Please provide a Project name.")
    elif not st.session_state.get("countries_sel"):
        st.warning("Please select at least one country.")
    elif not st.session_state.city_list and not st.session_state.get("countries_sel"):
        st.warning("Please add at least one (country — city) pair or select countries.")
    elif not submitter_email.strip():
        st.warning("Please provide a submitter email.")
    else:
        total_rows, ok_all, msg_any = 0, True, None
        entries_preview = []

        pairs = st.session_state.city_list[:] if st.session_state.city_list else [f"{c} — " for c in st.session_state.get("countries_sel", [])]

        for pair in pairs:
            if "—" not in pair:
                continue
            country, city = [p.strip() for p in pair.split("—", 1)]
            lat, lon = get_country_center(country)

            payload = {
                "country": country,
                "city": city,
                "lat": lat,
                "lon": lon,
                "project_name": new_name,
                "years": new_years,
                "status": new_status,
                "data_types": new_types,
                "description": new_desc,
                "contact": new_contact,
                "access": new_access,
                "url": new_url,
                "submitter_email": submitter_email,
                "is_edit": bool(is_edit),
                "edit_target": edit_target if is_edit else "",
                "edit_request": edit_request if is_edit else "",
            }

            ok_sheet, msg_sheet = append_submission_to_sheet(payload)
            ok_all &= ok_sheet
            msg_any = msg_sheet
            total_rows += 1
            entries_preview.append(payload)

        if ok_all:
            st.success(f"✅ Submission saved ({total_rows} row(s) added).")
            st.session_state.city_list = []
        else:
            st.warning(f"⚠️ Some rows failed: {msg_any}")

        ok_mail, msg_mail = try_send_email_via_emailjs({
            "project_name": new_name,
            "entries": ", ".join([f"{p['country']} — {p['city']}" for p in entries_preview]),
            "status": new_status,
            "years": new_years,
            "url": new_url,
            "submitter_email": submitter_email,
            "is_edit": "yes" if is_edit else "no",
            "edit_target": edit_target,
        })
        if ok_mail:
            st.info("📨 Notification email sent.")
        else:
            st.caption(msg_mail)

        st.markdown("**Submission preview (first rows):**")
        st.code(entries_preview[:3], language="python")
