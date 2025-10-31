# app.py
import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from datetime import datetime
import re

# ===== Integrações opcionais =====
import requests
import gspread
from google.oauth2.service_account import Credentials

# tenta importar pyproj para converter Web Mercator -> WGS84, se necessário
try:
    from pyproj import Transformer
    _HAS_PYPROJ = True
    _WM_TO_WGS84 = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
except Exception:
    _HAS_PYPROJ = False
    _WM_TO_WGS84 = None

# -----------------------------------------------------------------------------
# 0) Configs de página
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="IDEAMAPS Global Metadata Explorer",
    layout="wide",
    page_icon="🌍",
)

# -----------------------------------------------------------------------------
# 1) Fallback local (mostra algo mesmo sem Sheets)
# -----------------------------------------------------------------------------
FALLBACK_DATA = [
    {
        "country": "Nigeria",
        "city": "Lagos",
        "lat": 6.5244,
        "lon": 3.3792,
        "project_name": "IDEAMAPS Lagos / Urban Deprivation Mapping",
        "years": "2021–2024",
        "status": "Active",
        "data_types": "Spatial (building footprints, informal settlement boundaries); Quantitative (deprivation indicators)",
        "description": "High-resolution mapping of deprived / informal areas, combining satellite imagery with community validation and ward-level indicators.",
        "contact": "University of Lagos / Durham University",
        "access": "Partly restricted; request via IDEAMAPS team.",
        "url": "https://ideamapsnetwork.org",
        "approved": "TRUE",
    },
    {
        "country": "Bangladesh",
        "city": "Dhaka",
        "lat": 23.8103,
        "lon": 90.4125,
        "project_name": "CHORUS Bangladesh Informal Providers",
        "years": "2024–2025",
        "status": "Active",
        "data_types": "Spatial (GPS of drug sellers); Qualitative (provider interviews)",
        "description": "Identification of formal and informal drug sellers in Dhaka wards, linked to health system access and vulnerabilities.",
        "contact": "University of York / CHORUS Bangladesh team",
        "access": "Internal / ethics-controlled",
        "url": "https://chorus.york.ac.uk",
        "approved": "TRUE",
    },
    {
        "country": "Kenya",
        "city": "Nairobi",
        "lat": -1.2921,
        "lon": 36.8219,
        "project_name": "IDEAMAPS Nairobi / Participatory Mapping",
        "years": "2019–2022",
        "status": "Legacy",
        "data_types": "Spatial (informal settlement boundaries); Qualitative (community validation)",
        "description": "Co-produced polygons of informal settlements using local knowledge, used to train global informal-settlement detection models.",
        "contact": "University of Nairobi / Durham University",
        "access": "Some layers public; others restricted",
        "url": "https://ideamapsnetwork.org",
        "approved": "TRUE",
    },
]
FALLBACK_DF = pd.DataFrame(FALLBACK_DATA)

# -----------------------------------------------------------------------------
# 2) Country helper (para pré-preencher lat/lon no formulário)
# -----------------------------------------------------------------------------
COUNTRY_CENTER = {
    # África / Ásia (exemplos)
    "Nigeria": (9.0820, 8.6753),
    "Bangladesh": (23.6850, 90.3563),
    "Kenya": (-0.0236, 37.9062),
    "Ghana": (7.9465, -1.0232),
    "Ethiopia": (9.1450, 40.4897),
    "India": (22.9734, 78.6569),
    "Pakistan": (30.3753, 69.3451),
    "Nepal": (28.3949, 84.1240),
    # Américas / Europa
    "Brazil": (-14.2350, -51.9253),
    "United Kingdom": (54.0, -2.0),
    "Spain": (40.4637, -3.7492),
    "Portugal": (39.3999, -8.2245),
}

# -----------------------------------------------------------------------------
# 3) Conectores (Google Sheets + EmailJS)
# -----------------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def _gs_worksheet():
    """Tenta abrir worksheet do Google Sheets. Retorna (ws, msg_erro)."""
    try:
        ss_id = st.secrets.get("SHEETS_SPREADSHEET_ID")
        ws_name = st.secrets.get("SHEETS_WORKSHEET_NAME")
        if not ss_id or not ws_name:
            return None, "Secrets ausentes: defina SHEETS_SPREADSHEET_ID e SHEETS_WORKSHEET_NAME."

        creds_info = st.secrets.get("gcp_service_account")
        if not creds_info:
            return None, "Secrets ausentes: bloco [gcp_service_account] não encontrado."

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
        client = gspread.authorize(creds)
        ws = client.open_by_key(ss_id).worksheet(ws_name)
        return ws, None
    except Exception as e:
        return None, f"Falha ao conectar no Google Sheets: {e}"

@st.cache_data(show_spinner=False)
def load_approved_projects():
    """Lê aprovados (approved==TRUE) do Sheets. Se falhar, retorna FALLBACK_DF."""
    ws, err = _gs_worksheet()
    if err or ws is None:
        return FALLBACK_DF.copy(), False, err or "Worksheet indisponível."

    try:
        rows = ws.get_all_records()
        df = pd.DataFrame(rows)
        if df.empty:
            return FALLBACK_DF.copy(), False, "Planilha vazia; usando fallback local."

        # Colunas esperadas
        cols = ["country", "city", "lat", "lon", "project_name", "years", "status",
                "data_types", "description", "contact", "access", "url", "approved"]
        for c in cols:
            if c not in df.columns:
                df[c] = ""

        # Apenas aprovados
        df = df[df["approved"].astype(str).str.upper().eq("TRUE")].copy()
        return df, True, None
    except Exception as e:
        return FALLBACK_DF.copy(), False, f"Erro lendo a planilha: {e}"

def append_submission_to_sheet(payload: dict) -> tuple[bool, str]:
    """Anexa submissão na aba 'submissions' (fila de aprovação)."""
    ws, err = _gs_worksheet()
    if err or ws is None:
        return False, err or "Worksheet indisponível."
    try:
        row = {
            "country": payload.get("country", ""),
            "city": payload.get("city", ""),
            "lat": payload.get("lat", ""),
            "lon": payload.get("lon", ""),
            "project_name": payload.get("project_name", ""),
            "years": payload.get("years", ""),
            "status": payload.get("status", ""),
            "data_types": payload.get("data_types", ""),
            "description": payload.get("description", ""),
            "contact": payload.get("contact", ""),
            "access": payload.get("access", ""),
            "url": payload.get("url", ""),
            "approved": "FALSE",
            "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
        header = ws.row_values(1)
        values = [row.get(col, "") for col in header] if header else list(row.values())
        ws.append_row(values)
        return True, "Salvo no Google Sheets."
    except Exception as e:
        return False, f"Não consegui escrever no Sheets: {e}"

def try_send_email_via_emailjs(template_params: dict) -> tuple[bool, str]:
    """Envia notificação via EmailJS, se configurado nas secrets."""
    svc = st.secrets.get("EMAILJS_SERVICE_ID")
    tpl = st.secrets.get("EMAILJS_TEMPLATE_ID")
    key = st.secrets.get("EMAILJS_PUBLIC_KEY")
    if not (svc and tpl and key):
        return False, "EmailJS não configurado nas secrets; pulando envio."
    try:
        resp = requests.post(
            "https://api.emailjs.com/api/v1.0/email/send",
            json={
                "service_id": svc,
                "template_id": tpl,
                "user_id": key,
                "template_params": template_params,
            },
            timeout=12,
        )
        if resp.status_code == 200:
            return True, "Email enviado com sucesso."
        return False, f"EmailJS retornou status {resp.status_code}."
    except Exception as e:
        return False, f"Falha no envio de e-mail: {e}"

# -----------------------------------------------------------------------------
# 4) Header
# -----------------------------------------------------------------------------
st.markdown(
    """
    <div style="
        background: linear-gradient(90deg, #0f172a 0%, #1e293b 50%, #0f172a 100%);
        padding: 1.2rem 1.5rem;
        border-radius: 0.75rem;
        border: 1px solid #334155;
        margin-bottom: 1rem;">
        <div style="color:#fff; font-size:1.2rem; font-weight:600;">
            IDEAMAPS Global Metadata Explorer 🌍
        </div>
        <div style="color:#94a3b8; font-size:0.85rem; line-height:1.3;">
            Catálogo vivo de projetos e datasets (spatial / quantitative / qualitative)
            produzidos pela rede IDEAMAPS e parceiros.
        </div>
    </div>
    """,
    unsafe_allow_html=True
)

# -----------------------------------------------------------------------------
# 5) Carrega aprovados + concatena com fallback
# -----------------------------------------------------------------------------
df_sheets, from_sheets, debug_msg = load_approved_projects()
if not from_sheets:
    st.info("ℹ️ Usando dados locais temporários (o Google Sheets não está disponível).")
    if debug_msg:
        st.caption(f"Debug: {debug_msg}")

df_all = pd.concat([FALLBACK_DF, df_sheets], ignore_index=True)
df_all = df_all.drop_duplicates(subset=["city", "project_name"], keep="first")

# -----------------------------------------------------------------------------
# 6) Normalização de coordenadas
# -----------------------------------------------------------------------------
DMS_RE = re.compile(
    r"""^\s*
        (?P<deg>-?\d+(?:[.,]\d+)?)
        (?:[°\s]+(?P<min>\d+(?:[.,]\d+)?))?
        (?:['\s]+(?P<sec>\d+(?:[.,]\d+)?))?
        ["\s]*?(?P<hem>[NnSsEeWw])?
        \s*$""",
    re.VERBOSE
)

def _to_float(s):
    if pd.isna(s):
        return None
    t = str(s).strip().replace(",", ".")
    m = re.search(r"-?\d+(?:\.\d+)?", t)
    return float(m.group()) if m else None

def _dms_to_decimal(val):
    if pd.isna(val):
        return None
    text = str(val).strip().replace(",", ".")
    m = DMS_RE.match(text)
    if not m:
        return None
    deg = float(m.group("deg"))
    minutes = float(m.group("min")) if m.group("min") else 0.0
    seconds = float(m.group("sec")) if m.group("sec") else 0.0
    hem = (m.group("hem") or "").upper()
    dec = abs(deg) + minutes/60.0 + seconds/3600.0
    if deg < 0:
        dec = -dec
    if hem in ("S", "W"):
        dec = -abs(dec)
    if hem in ("N", "E"):
        dec = abs(dec)
    return dec

def _coerce_coord(val, kind):
    dec = _dms_to_decimal(val)
    if dec is None:
        dec = _to_float(val)
    if dec is None:
        return None
    if kind == "lat" and not (-90.0 <= dec <= 90.0):
        return None
    if kind == "lon" and not (-180.0 <= dec <= 180.0):
        return None
    return dec

def _looks_like_webmercator(x, y):
    return (
        isinstance(x, (int, float)) and isinstance(y, (int, float)) and
        1000 < abs(x) <= 3e7 and 1000 < abs(y) <= 3e7
    )

def normalize_latlon(df, lat_cols=("lat","latitude"), lon_cols=("lon","lng","longitude")):
    lat_src = next((c for c in lat_cols if c in df.columns), None)
    lon_src = next((c for c in lon_cols if c in df.columns), None)
    if not lat_src or not lon_src:
        return df

    lat_vals, lon_vals = [], []
    for a, b in zip(df[lon_src].tolist(), df[lat_src].tolist()):
        lat = _coerce_coord(b, "lat")
        lon = _coerce_coord(a, "lon")

        if lat is None or lon is None:
            lat2 = _coerce_coord(a, "lat")
            lon2 = _coerce_coord(b, "lon")
            if lat2 is not None and lon2 is not None:
                lat, lon = lat2, lon2

        if (lat is None or lon is None) and _HAS_PYPROJ:
            xf = _to_float(a)
            yf = _to_float(b)
            if xf is not None and yf is not None and _looks_like_webmercator(xf, yf):
                try:
                    lon_wgs, lat_wgs = _WM_TO_WGS84.transform(xf, yf)
                    if -90 <= lat_wgs <= 90 and -180 <= lon_wgs <= 180:
                        lat, lon = lat_wgs, lon_wgs
                except Exception:
                    pass

        lat_vals.append(lat)
        lon_vals.append(lon)

    out = df.copy()
    out["lat"] = pd.Series(lat_vals, index=out.index)
    out["lon"] = pd.Series(lon_vals, index=out.index)
    return out.dropna(subset=["lat", "lon"])

# -----------------------------------------------------------------------------
# 7) Sidebar — filtros
# -----------------------------------------------------------------------------
st.sidebar.markdown(
    """
    <div style="font-weight:600; font-size:1rem; color:#fff;">
        🌍 IDEAMAPS Explorer
    </div>
    <div style="color:#94a3b8; font-size:0.8rem; line-height:1.4;">
        Filtre por país para ver projetos e dados disponíveis.
    </div>
    <hr style="border:1px solid #334155; margin:0.75rem 0;" />
    """,
    unsafe_allow_html=True
)
all_countries = ["All"] + sorted(df_all["country"].dropna().unique().tolist())
selected_country = st.sidebar.selectbox("Filter by country", all_countries)
df_filtered = df_all if selected_country == "All" else df_all[df_all["country"] == selected_country].copy()

# Normaliza coordenadas para os dados visíveis
df_norm = normalize_latlon(df_filtered)

# Se tudo ficou vazio (coords ruins), tenta normalizar o conjunto todo como diagnóstico
if df_norm.empty:
    df_norm = normalize_latlon(df_all)

# Centro do mapa
if not df_norm.empty:
    lat0, lon0 = df_norm["lat"].mean(), df_norm["lon"].mean()
else:
    lat0, lon0 = 15, 0

# -----------------------------------------------------------------------------
# 8) Mapa
# -----------------------------------------------------------------------------
m = folium.Map(location=[lat0, lon0], zoom_start=2, tiles="CartoDB dark_matter")

grouped = (
    df_norm.groupby(["country", "city", "lat", "lon"], as_index=False)
    .agg({"project_name": list})
)

for _, row in grouped.iterrows():
    country, city, lat, lon = row["country"], row["city"], float(row["lat"]), float(row["lon"])
    project_list = df_all[
        (df_all["country"] == country) & (df_all["city"] == city)
    ][["project_name", "years", "status", "url"]].to_dict(orient="records")

    popup_lines = [f"<b>{city}, {country}</b><br/>", "<ul style='padding-left:1rem;margin:0;'>"]
    for proj in project_list:
        link_html = (
            f"<br/><a href='{proj.get('url','')}' target='_blank' "
            f"style='color:#38bdf8; text-decoration:none;'>🔗 Open project</a>"
            if proj.get("url") else ""
        )
        popup_lines.append(
            f"<li style='font-size:0.8rem; line-height:1.2;'>"
            f"<b>{proj.get('project_name','')}</b>{link_html}"
            f"<br/><span style='color:#888'>Years: {proj.get('years','')} — Status: {proj.get('status','')}</span>"
            f"</li>"
        )
    popup_lines.append("</ul>")
    html_popup = "<div style='font-size:0.8rem; color:#fff;'>" + "".join(popup_lines) + "</div>"

    any_active = any((p.get("status","").lower() == "active") for p in project_list)
    color = "#38bdf8" if any_active else "#facc15"

    tooltip_text = f"{city}, {country}"
    if project_list:
        tooltip_text += f" — {project_list[0].get('project_name','')}"

    folium.CircleMarker(
        location=[lat, lon],
        radius=6,
        color=color,
        fill=True,
        fill_opacity=0.85,
        popup=folium.Popup(html_popup, max_width=320),
        tooltip=tooltip_text,
    ).add_to(m)

st_folium(m, height=500, width=None)

# Diagnóstico (opcional)
with st.expander("🧭 Coord diagnostics", expanded=False):
    st.write("No filtro:", len(df_filtered))
    st.write("Plotadas (lat/lon válidos):", len(df_norm))
    if len(df_filtered) and len(df_norm) < len(df_filtered):
        bad = df_filtered.copy()
        good_keys = df_norm[["country","city","lat","lon"]].drop_duplicates()
        bad = bad.merge(good_keys, on=["country","city","lat","lon"], how="left", indicator=True)
        bad = bad[bad["_merge"]=="left_only"].drop(columns=["_merge"])
        st.write("Linhas sem coordenadas válidas:", len(bad))
        st.dataframe(bad.head(15), use_container_width=True)

st.markdown("---")

# -----------------------------------------------------------------------------
# 9) Cards de projetos
# -----------------------------------------------------------------------------
st.markdown(
    "<h3 style='color:#fff; margin-bottom:0.5rem;'>Projects</h3>"
    "<p style='color:#94a3b8; font-size:0.8rem; margin-top:0;'>Resumo dos projetos conhecidos no país selecionado.</p>",
    unsafe_allow_html=True
)
for _, row in df_filtered.iterrows():
    url_html = (
        f"<a href='{row.get('url','')}' target='_blank' style='color:#38bdf8; text-decoration:none;'>🔗 Open project</a>"
        if pd.notna(row.get("url")) and str(row.get("url")).strip() != "" else ""
    )
    st.markdown(
        f"""
        <div style="font-size:0.8rem; line-height:1.4;
            border:1px solid #334155; background-color:#1e293b;
            border-radius:12px; padding:12px 16px; margin-bottom:12px;">
          <div style="font-size:0.9rem; font-weight:600; color:#fff;">
            {row.get('project_name','')}
          </div>
          <div style="font-size:0.75rem; color:#38bdf8;">
            {row.get('city','')}, {row.get('country','')} — {row.get('years','')} — {row.get('status','')}
          </div>
          <div style="margin-top:6px; font-size:0.8rem; color:#cbd5e1;">
            <b>Data types:</b> {row.get('data_types','')}
          </div>
          <div style="margin-top:6px; font-size:0.8rem; color:#cbd5e1;">
            {row.get('description','')}
          </div>
          <div style="margin-top:10px; font-size:0.7rem; color:#94a3b8;">
            <b>Contact:</b> {row.get('contact','')}<br/>
            <b>Access:</b> {row.get('access','')}
          </div>
          <div style="margin-top:8px;">{url_html}</div>
        </div>
        """,
        unsafe_allow_html=True
    )

st.markdown("---")

# -----------------------------------------------------------------------------
# 10) Tabela dos pontos do mapa + Download CSV/HTML
# -----------------------------------------------------------------------------
st.subheader("Data on map (downloadable)")
if df_norm.empty:
    st.info("Nenhum ponto com coordenadas válidas para o filtro atual.")
else:
    cols_show = ["country","city","lat","lon","project_name","years","status","url"]
    tbl = (df_norm[cols_show]
           .copy()
           .sort_values(["country","city","project_name"])
           .reset_index(drop=True))
    st.dataframe(tbl, use_container_width=True)

    csv_bytes = tbl.to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ Download CSV", data=csv_bytes, file_name="ideamaps_on_map.csv", mime="text/csv")

    html_bytes = tbl.to_html(index=False).encode("utf-8")
    st.download_button("⬇️ Download HTML", data=html_bytes, file_name="ideamaps_on_map.html", mime="text/html")

st.markdown("---")

# -----------------------------------------------------------------------------
# 11) Helper de país (fora do form) para pré-preencher lat/lon
#     — evita callback dentro do st.form (que causava erro)
# -----------------------------------------------------------------------------
st.subheader("Country helper (prefill)")
colh1, colh2, colh3 = st.columns([2,1,1])
with colh1:
    helper_choice = st.selectbox(
        "Choose a country to prefill (optional)",
        [""] + sorted(COUNTRY_CENTER.keys()),
        index=0
    )
with colh2:
    if "lat_input" not in st.session_state:
        st.session_state.lat_input = 0.0
    if "lon_input" not in st.session_state:
        st.session_state.lon_input = 0.0
    if "form_country_text" not in st.session_state:
        st.session_state.form_country_text = ""

    if helper_choice:
        cc = COUNTRY_CENTER.get(helper_choice)
        if cc:
            st.session_state.lat_input = float(cc[0])
            st.session_state.lon_input = float(cc[1])
            st.session_state.form_country_text = helper_choice

with colh3:
    st.caption(f"Prefilled lat/lon: {st.session_state.lat_input}, {st.session_state.lon_input}")

st.markdown("---")

# -----------------------------------------------------------------------------
# 12) Formulário — submissão para fila (Sheets) + EmailJS
# -----------------------------------------------------------------------------
st.header("Add new project (goes to review queue)")

with st.form("add_project_form"):
    new_country = st.text_input("Country", value=st.session_state.form_country_text)
    new_city = st.text_input("City")

    new_lat = st.number_input("Latitude", value=float(st.session_state.lat_input), format="%.6f", key="lat_input")
    new_lon = st.number_input("Longitude", value=float(st.session_state.lon_input), format="%.6f", key="lon_input")

    new_name = st.text_input("Project name")
    new_years = st.text_input("Years (e.g. 2022–2024)")
    new_status = st.selectbox("Status", ["Active", "Legacy", "Completed", "Planning"])
    new_types = st.text_area("Data types (Spatial? Quantitative? Qualitative?)")
    new_desc = st.text_area("Short description")
    new_contact = st.text_input("Contact / Responsible institution")
    new_access = st.text_input("Access / License / Ethics")
    new_url = st.text_input("Project URL (optional)")

    submitted = st.form_submit_button("Submit for review")

if submitted:
    new_row = {
        "country": new_country,
        "city": new_city,
        "lat": st.session_state.lat_input,
        "lon": st.session_state.lon_input,
        "project_name": new_name,
        "years": new_years,
        "status": new_status,
        "data_types": new_types,
        "description": new_desc,
        "contact": new_contact,
        "access": new_access,
        "url": new_url,
    }

    ok_sheet, msg_sheet = append_submission_to_sheet(new_row)
    if ok_sheet:
        st.success("✅ Submission saved to review queue (Google Sheets).")
    else:
        st.warning(f"⚠️ Submission NOT saved to Sheets. {msg_sheet}")

    ok_mail, msg_mail = try_send_email_via_emailjs(new_row)
    if ok_mail:
        st.info("📨 Notification email sent.")
    else:
        st.caption(msg_mail)

    st.markdown("**Submission payload (for your records):**")
    st.code(new_row, language="python")
