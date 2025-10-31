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
    """Force 'lat' and 'lon' as Plain text (TEXT) in the PROJECTS sheet."""
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
    """Ensure the PROJECTS header has all required columns."""
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
    """Ensure the MESSAGES sheet header exists."""
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
    """Robust number parser for lat/lon."""
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
    ensure_message_headers()
    st.session_state["_last_refresh"] = datetime.utcnow().isoformat()
    st.rerun()

# =============================================================================
# 4) LOAD APPROVED PROJECTS + MAP
# =============================================================================
@st.cache_data(show_spinner=False)
def load_approved_projects():
    """Load approved==TRUE, normalize lat/lon."""
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
        groups = df_map.groupby(["country","lat","lon"], as_index=False)

        m = folium.Map(
            location=[df_map["lat"].mean(), df_map["lon"].mean()],
            zoom_start=2,
            tiles="CartoDB dark_matter"
        )

        for (country, lat, lon), g in groups:
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

            # ---------- FIXED: readable (dark) text inside white tooltip/popup ----------
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

            any_active = any(str(x).lower()=="active" for x in g["status"].tolist())
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
# 5) DATA (DOWNLOADABLE & EDITABLE) — DIFF → SUBMIT FOR REVIEW
# =============================================================================
st.subheader("Data (downloadable & editable)")

EDIT_KEY_COLS = ["country", "city", "project_name"]

def _mk_key(row: pd.Series) -> str:
    vals = [str(row.get(c, "")).strip() for c in EDIT_KEY_COLS]
    return "||".join(vals)

def _coerce_lat_lon(row: pd.Series, country_center_map: dict):
    lat = row.get("lat", None)
    lon = row.get("lon", None)
    try:
        lat = float(lat) if pd.notna(lat) and str(lat).strip() != "" else None
    except Exception:
        lat = None
    try:
        lon = float(lon) if pd.notna(lon) and str(lon).strip() != "" else None
    except Exception:
        lon = None
    if (lat is None or lon is None) and country_center_map:
        cc = country_center_map.get(str(row.get("country","")).strip())
        if cc:
            lat, lon = float(cc[0]), float(cc[1])
    return lat, lon

def _summarize_changes(before: pd.Series, after: pd.Series) -> str:
    changes = []
    for col in ["country","city","lat","lon","project_name","years","status","data_types",
                "description","contact","access","url"]:
        bv = before.get(col, "")
        av = after.get(col, "")
        if (pd.isna(bv) and pd.notna(av)) or (pd.notna(bv) and pd.isna(av)) or (str(bv)!=str(av)):
            bv_s = "" if (pd.isna(bv) or bv is None) else str(bv)
            av_s = "" if (pd.isna(av) or av is None) else str(av)
            if len(bv_s) > 120: bv_s = bv_s[:117]+"…"
            if len(av_s) > 120: av_s = av_s[:117]+"…"
            changes.append(f"{col}: '{bv_s}' → '{av_s}'")
    return "; ".join(changes) if changes else "No visible change"

if df_projects.empty:
    st.info("No data to display yet.")
else:
    editable_cols = [
        "country","city","lat","lon","project_name","years","status",
        "data_types","description","contact","access","url"
    ]
    present_cols = [c for c in editable_cols if c in df_projects.columns]
    view_df = df_projects[present_cols].copy()

    edited_df = st.data_editor(
        view_df,
        num_rows="dynamic",
        use_container_width=True,
        key="projects_editor",
        column_config={
            "country": st.column_config.SelectboxColumn(
                "country", options=sorted(COUNTRY_CENTER_FULL.keys()),
                help="Country name (from master list)"
            ),
            "status": st.column_config.SelectboxColumn(
                "status", options=["Active","Legacy","Completed","Planning"]
            ),
            "url": st.column_config.LinkColumn("url"),
            "lat": st.column_config.NumberColumn("lat", step=0.000001, format="%.6f"),
            "lon": st.column_config.NumberColumn("lon", step=0.000001, format="%.6f"),
        }
    )

    col_dl1, col_dl2, col_opts = st.columns([1,1,2])
    with col_dl1:
        csv_bytes = edited_df.to_csv(index=False).encode("utf-8")
        st.download_button("⬇️ Download CSV", data=csv_bytes, file_name="ideamaps_data.csv", mime="text/csv")
    with col_dl2:
        html_bytes = edited_df.to_html(index=False, escape=False).encode("utf-8")
        st.download_button("⬇️ Download HTML", data=html_bytes, file_name="ideamaps_data.html", mime="text/html")
    with col_opts:
        consider_deletions = st.checkbox("Treat removed rows as delete requests (send for review)", value=False)

    st.markdown("")

    orig = view_df.copy()
    orig["_key"] = orig.apply(_mk_key, axis=1)
    edited = edited_df.copy()
    edited["_key"] = edited.apply(_mk_key, axis=1)

    orig_by_key = orig.set_index("_key", drop=False)
    edt_by_key  = edited.set_index("_key", drop=False)

    new_keys = [k for k in edt_by_key.index if k not in orig_by_key.index]
    maybe_changed_keys = [k for k in edt_by_key.index if k in orig_by_key.index]
    deleted_keys = [k for k in orig_by_key.index if k not in edt_by_key.index]

    editor_email = st.text_input("Your email for review (required to submit changes)", key="editor_email")

    if st.button("Submit edits for review"):
        if not editor_email.strip():
            st.warning("Please provide your email to submit changes.")
        else:
            total_rows, ok_all, last_msg = 0, True, None

            # NEW rows → submission
            for k in new_keys:
                row = edt_by_key.loc[k, present_cols].copy()
                lat, lon = _coerce_lat_lon(row, COUNTRY_CENTER_FULL)
                payload = {
                    **{col: row.get(col, "") for col in present_cols},
                    "lat": lat, "lon": lon,
                    "submitter_email": editor_email,
                    "is_edit": False,
                    "edit_target": row.get("project_name",""),
                    "edit_request": "New submission via data editor"
                }
                ok, msg = append_submission_to_sheet(payload)
                ok_all &= ok; last_msg = msg; total_rows += 1

            # CHANGED rows → edit
            for k in maybe_changed_keys:
                before = orig_by_key.loc[k, present_cols]
                after  = edt_by_key.loc[k, present_cols]
                if not before.equals(after):
                    lat, lon = _coerce_lat_lon(after, COUNTRY_CENTER_FULL)
                    payload = {
                        **{col: after.get(col, "") for col in present_cols},
                        "lat": lat, "lon": lon,
                        "submitter_email": editor_email,
                        "is_edit": True,
                        "edit_target": after.get("project_name",""),
                        "edit_request": _summarize_changes(before, after)
                    }
                    ok, msg = append_submission_to_sheet(payload)
                    ok_all &= ok; last_msg = msg; total_rows += 1

            # DELETED rows → delete request (optional)
            if consider_deletions:
                for k in deleted_keys:
                    before = orig_by_key.loc[k, present_cols]
                    payload = {
                        **{col: before.get(col, "") for col in present_cols},
                        "submitter_email": editor_email,
                        "is_edit": True,
                        "edit_target": before.get("project_name",""),
                        "edit_request": "DELETE REQUEST for this (country, city, project) row"
                    }
                    ok, msg = append_submission_to_sheet(payload)
                    ok_all &= ok; last_msg = msg; total_rows += 1

            if total_rows == 0:
                st.info("No changes detected.")
            elif ok_all:
                st.success(f"✅ Changes sent for review ({total_rows} row(s)).")
            else:
                st.warning(f"⚠️ Some rows failed: {last_msg}")

st.markdown("---")

# =============================================================================
# 6) ADD NEW PROJECT (GOES TO REVIEW QUEUE)
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

countries_options = sorted(COUNTRY_CENTER_FULL.keys())
st.session_state.countries_sel = st.multiselect(
    "Countries (one or more)",
    options=countries_options,
    default=st.session_state.get("countries_sel", []),
    help="Select all countries covered by this project."
)
options_for_city = st.session_state.get("countries_sel", [])

with st.form("add_project_form", clear_on_submit=False):
    new_name = st.text_input("Project name", placeholder="e.g., IDEAMAPS Lagos / Urban Deprivation Mapping")
    submitter_email = st.text_input("Submitter email (required for review)", placeholder="name@org.org")

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

def append_submission_to_sheet(payload: dict) -> tuple[bool, str]:
    """Append a row to the PROJECTS sheet (plain text for lat/lon)."""
    ws, err = _ws_projects()
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
            "email": email.strip(),
            "message": message.strip(),
            "approved": "FALSE",
            "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
        header = ws.row_values(1)
        values = [row.get(col, "") for col in header] if header else list(row.values())
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
        ok, msg = append_message_to_sheet(msg_name, msg_email or "", msg_text)
        if ok:
            st.success("✅ Message sent for approval.")
        else:
            st.warning(f"⚠️ {msg}")

@st.cache_data(show_spinner=False)
def load_approved_messages():
    ws, err = _ws_messages()
    if err or ws is None:
        return pd.DataFrame(), False, err
    try:
        rows = ws.get_all_records()
        df = pd.DataFrame(rows)
        if df.empty:
            return pd.DataFrame(), True, None
        for c in MESSAGE_HEADERS:
            if c not in df.columns:
                df[c] = ""
        df = df[df["approved"].astype(str).str.upper().eq("TRUE")].copy()
        return df.sort_values("created_at", ascending=False), True, None
    except Exception as e:
        return pd.DataFrame(), False, f"Error reading messages: {e}"

st.subheader("Recent public messages")
df_msgs, ok_msgs, err_msgs = load_approved_messages()
if not ok_msgs:
    st.caption(f"⚠️ {err_msgs}")
elif df_msgs.empty:
    st.info("No public messages yet.")
else:
    for _, r in df_msgs.head(12).iterrows():
        name = r.get("name","").strip() or "Anonymous"
        email = r.get("email","").strip()
        created = r.get("created_at","")
        body = r.get("message","").strip()
        st.markdown(
            f"""
            <div style="border:1px solid #334155;background:#0b1220;border-radius:12px;padding:12px;margin-bottom:10px;">
              <div style="color:#e5e7eb;font-weight:600;">{name}
                <span style="color:#64748b;font-weight:400;font-size:0.85rem;">{(' • '+email) if email else ''}</span>
              </div>
              <div style="color:#94a3b8;font-size:0.75rem;margin-top:2px;">{created}</div>
              <div style="color:#cbd5e1;margin-top:8px;">{body}</div>
            </div>
            """,
            unsafe_allow_html=True
        )
