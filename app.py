# app.py
import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from datetime import datetime
from io import StringIO

# Extras (integrações opcionais e resilientes)
import requests
import gspread
from google.oauth2.service_account import Credentials

# =============================================================================
# 0) Configs básicos da página
# =============================================================================
st.set_page_config(
    page_title="IDEAMAPS Global Metadata Explorer",
    layout="wide",
    page_icon="🌍",
)

# =============================================================================
# 1) Dados base (fallback local) — usados se Sheets não estiver disponível
# =============================================================================
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

# =============================================================================
# 2) Países → (lat, lon): URL (secrets) → CSV local → fallback interno
#     CSV esperado (nomes flexíveis): 
#     Country, Latitude (average), Longitude (average)
# =============================================================================
FALLBACK_COUNTRY_CENTER = {
    "Nigeria": (9.0820, 8.6753),
    "Bangladesh": (23.6850, 90.3563),
    "Kenya": (-0.0236, 37.9062),
    "Brazil": (-14.2350, -51.9253),
    "United Kingdom": (54.0, -2.0),
}

@st.cache_data(show_spinner=False)
def load_country_centers() -> dict[str, tuple[float, float]]:
    """
    Tenta na ordem:
      1) URL em st.secrets["COUNTRIES_CSV_URL"] (CSV com cols Country, Latitude..., Longitude...)
      2) Arquivo local data/countries_latlon.csv
      3) FALLBACK_COUNTRY_CENTER
    Retorna dict: {Country: (lat, lon)}
    """
    def _norm(df: pd.DataFrame) -> dict[str, tuple[float, float]]:
        cols = {c.strip().lower(): c for c in df.columns}
        c_country = next((cols[k] for k in cols if k in {"country", "name"}), None)
        c_lat = next((cols[k] for k in cols if "lat" in k), None)
        c_lon = next((cols[k] for k in cols if "lon" in k or "lng" in k), None)
        if not (c_country and c_lat and c_lon):
            return {}
        tmp = df[[c_country, c_lat, c_lon]].copy()
        tmp.columns = ["country", "lat", "lon"]
        tmp["country"] = tmp["country"].astype(str).str.strip()
        tmp["lat"] = pd.to_numeric(tmp["lat"], errors="coerce")
        tmp["lon"] = pd.to_numeric(tmp["lon"], errors="coerce")
        tmp = tmp.dropna(subset=["lat", "lon"])
        out = {}
        for _, r in tmp.iterrows():
            if r["country"]:
                out[r["country"]] = (float(r["lat"]), float(r["lon"]))
        return out

    # 1) URL nas secrets
    url = st.secrets.get("COUNTRIES_CSV_URL")
    if url:
        try:
            r = requests.get(url, timeout=12)
            if r.ok:
                df_url = pd.read_csv(StringIO(r.text))
                d = _norm(df_url)
                if d:
                    return d
        except Exception:
            pass

    # 2) Arquivo local
    try:
        df_local = pd.read_csv("data/countries_latlon.csv")
        d = _norm(df_local)
        if d:
            return d
    except Exception:
        pass

    # 3) fallback
    return FALLBACK_COUNTRY_CENTER

COUNTRY_CENTER = load_country_centers()

# =============================================================================
# 3) Conector Google Sheets + EmailJS (resiliente)
# =============================================================================
@st.cache_resource(show_spinner=False)
def _gs_worksheet():
    """Abre worksheet (aba) do Google Sheets definido nas secrets."""
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
    """
    Lê a planilha e retorna apenas approved == TRUE.
    (df, from_sheets: bool, debug_msg: str|None)
    """
    ws, err = _gs_worksheet()
    if err or ws is None:
        return FALLBACK_DF.copy(), False, err or "Worksheet indisponível."

    try:
        rows = ws.get_all_records()
        df = pd.DataFrame(rows)

        # garantir colunas
        for col in ["country", "city", "lat", "lon", "project_name", "years", "status",
                    "data_types", "description", "contact", "access", "url", "approved"]:
            if col not in df.columns:
                df[col] = ""

        # filtrar aprovados
        df = df[df["approved"].astype(str).str.upper().eq("TRUE")].copy()

        # coerção lat/lon
        for c in ("lat", "lon"):
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")

        # se vazio, usa fallback
        if df.empty:
            return FALLBACK_DF.copy(), False, "Planilha sem aprovados; usando fallback local."

        return df, True, None
    except Exception as e:
        return FALLBACK_DF.copy(), False, f"Erro lendo a planilha: {e}"

def append_submission_to_sheet(payload: dict) -> tuple[bool, str]:
    """Anexa submissão na aba configurada (fila de aprovação)."""
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
    """Envia e-mail via EmailJS (opcional)."""
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
        else:
            return False, f"EmailJS retornou status {resp.status_code}."
    except Exception as e:
        return False, f"Falha no envio de e-mail: {e}"

# =============================================================================
# 4) Header
# =============================================================================
st.markdown(
    """
    <div style="
        background: linear-gradient(90deg, #0f172a 0%, #1e293b 50%, #0f172a 100%);
        padding: 1.2rem 1.5rem;
        border-radius: 0.75rem;
        border: 1px solid #334155;
        margin-bottom: 1rem;
    ">
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

# =============================================================================
# 5) Carrega dados (Sheets se disponível; senão fallback)
# =============================================================================
df_all, from_sheets, debug_msg = load_approved_projects()
if not from_sheets:
    st.info("ℹ️ Usando dados locais temporários (o Google Sheets não está disponível).")
    if debug_msg:
        st.caption(f"Debug: {debug_msg}")

# segurança extra: coerção lat/lon
for c in ("lat", "lon"):
    if c in df_all.columns:
        df_all[c] = pd.to_numeric(df_all[c], errors="coerce")

# =============================================================================
# 6) Sidebar — Filtros
# =============================================================================
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
df = df_all.copy() if selected_country == "All" else df_all[df_all["country"] == selected_country].copy()

# =============================================================================
# 7) Mapa (dark) + Popups com links
# =============================================================================
# Define centro do mapa (se usuário filtrou por país e temos centro conhecido)
if selected_country != "All" and selected_country in COUNTRY_CENTER:
    map_center = COUNTRY_CENTER[selected_country]
else:
    map_center = (15, 0)

m = folium.Map(location=map_center, zoom_start=2 if selected_country == "All" else 4, tiles="CartoDB dark_matter")

# Agrupar por (country, city) para não duplicar marcadores
grouped = (
    df.groupby(["country", "city", "lat", "lon"], as_index=False)
      .agg({"project_name": list})
)

for _, row in grouped.iterrows():
    country, city, lat, lon = row["country"], row["city"], row["lat"], row["lon"]
    if pd.isna(lat) or pd.isna(lon):
        continue

    project_list = df_all[(df_all["country"] == country) & (df_all["city"] == city)][
        ["project_name", "years", "status", "url"]
    ].to_dict(orient="records")

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

st_folium(m, height=520, width=None)
st.markdown("---")

# =============================================================================
# 8) Cards de projetos
# =============================================================================
st.markdown(
    "<h3 style='color:#fff; margin-bottom:0.5rem;'>Projects</h3>"
    "<p style='color:#94a3b8; font-size:0.8rem; margin-top:0;'>Resumo dos projetos conhecidos no país selecionado.</p>",
    unsafe_allow_html=True
)
for _, row in df.iterrows():
    url_html = (
        f"<a href='{row.get('url','')}' target='_blank' style='color:#38bdf8; text-decoration:none;'>🔗 Open project</a>"
        if pd.notna(row.get("url")) and str(row.get("url")).strip() != ""
        else ""
    )
    st.markdown(
        f"""
        <div style="
            font-size:0.8rem; line-height:1.4;
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
          <div style="margin-top:8px;">
            {url_html}
          </div>
        </div>
        """,
        unsafe_allow_html=True
    )

st.markdown("---")

# =============================================================================
# 9) Formulário — submissão para fila de aprovação (com auto lat/lon)
# =============================================================================
st.header("Add new project (goes to review queue)")

# opções: países vindos do CSV + países já existentes no dataset
form_country_options = sorted(set(list(COUNTRY_CENTER.keys()) + df_all["country"].dropna().tolist()))

def _on_country_change():
    c = st.session_state.form_country
    if c in COUNTRY_CENTER:
        st.session_state.lat_input, st.session_state.lon_input = COUNTRY_CENTER[c]

# estado inicial
if "form_country" not in st.session_state:
    st.session_state.form_country = form_country_options[0] if form_country_options else ""
if "lat_input" not in st.session_state:
    st.session_state.lat_input, st.session_state.lon_input = (0.0, 0.0)

with st.form("add_project_form"):
    new_country = st.selectbox("Country", form_country_options, key="form_country", on_change=_on_country_change)
    col1, col2 = st.columns(2)
    with col1:
        new_city = st.text_input("City")
        new_lat = st.number_input("Latitude", value=st.session_state.lat_input, format="%.6f", key="lat_input")
    with col2:
        new_name = st.text_input("Project name")
        new_lon = st.number_input("Longitude", value=st.session_state.lon_input, format="%.6f", key="lon_input")

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
        "lat": new_lat,
        "lon": new_lon,
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
