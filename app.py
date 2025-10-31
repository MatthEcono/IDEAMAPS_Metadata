# app.py
import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from datetime import datetime

# Integrações opcionais (Sheets + Email)
import requests
import gspread
from google.oauth2.service_account import Credentials
from io import StringIO

# -----------------------------------------------------------------------------
# 0) Configs de página
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="IDEAMAPS Global Metadata Explorer",
    layout="wide",
    page_icon="🌍",
)

# Barra superior: título + refresh
colA, colB = st.columns([1, 0.25])
with colA:
    st.markdown(
        """
        <div style="
            background: linear-gradient(90deg, #0f172a 0%, #1e293b 50%, #0f172a 100%);
            padding: 1.0rem 1.2rem;
            border-radius: 0.75rem;
            border: 1px solid #334155;
            margin-bottom: 0.5rem;
        ">
            <div style="color:#fff; font-size:1.1rem; font-weight:600;">
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
with colB:
    if st.button("🔄 Refresh data (clear cache)", use_container_width=True):
        st.cache_data.clear()
        st.cache_resource.clear()
        st.success("Cache limpo. Dados serão recarregados agora.")

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
        "country": "BRASIL",
        "city": "Lagos",
        "lat": 14,235,
        "lon": 51,9253,
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
# 2) Country → (lat, lon) para auto-preencher o formulário
# -----------------------------------------------------------------------------
COUNTRY_CENTER = {
    "Nigeria": (9.0820, 8.6753),
    "Bangladesh": (23.6850, 90.3563),
    "Kenya": (-0.0236, 37.9062),
    "Ghana": (7.9465, -1.0232),
    "Ethiopia": (9.1450, 40.4897),
    "India": (22.9734, 78.6569),
    "Pakistan": (30.3753, 69.3451),
    "Nepal": (28.3949, 84.1240),
    "Brazil": (-14.2350, -51.9253),
    "United Kingdom": (54.0, -2.0),
    "Spain": (40.4637, -3.7492),
    "Portugal": (39.3999, -8.2245),
}

# -----------------------------------------------------------------------------
# 3) Conectores (Sheets + EmailJS) com cache
# -----------------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def _gs_worksheet():
    """Tenta abrir worksheet do Google Sheets. Retorna (ws, msg_erro)"""
    try:
        ss_id = st.secrets.get("SHEETS_SPREADSHEET_ID")
        ws_name = st.secrets.get("SHEETS_WORKSHEET_NAME")  # ex.: "submissions"
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

@st.cache_data(show_spinner=False, ttl=60)
def load_approved_projects():
    """
    Lê aprovados (approved==TRUE) do Sheets.
    Se falhar, retorna FALLBACK_DF e flag from_sheets=False.
    """
    ws, err = _gs_worksheet()
    if err or ws is None:
        return FALLBACK_DF.copy(), False, err or "Worksheet indisponível."

    try:
        rows = ws.get_all_records()
        df = pd.DataFrame(rows)
        if df.empty:
            return FALLBACK_DF.copy(), False, "Planilha vazia; usando fallback local."

        # Normaliza esperadas
        needed = ["country", "city", "lat", "lon", "project_name", "years", "status",
                  "data_types", "description", "contact", "access", "url", "approved"]
        for col in needed:
            if col not in df.columns:
                df[col] = ""

        # Filtra aprovados
        df = df[df["approved"].astype(str).str.upper().eq("TRUE")].copy()

        # Converte lat/lon
        for c in ("lat", "lon"):
            df[c] = pd.to_numeric(df[c], errors="coerce")

        return df, True, None
    except Exception as e:
        return FALLBACK_DF.copy(), False, f"Erro lendo a planilha: {e}"

def append_submission_to_sheet(payload: dict) -> tuple[bool, str]:
    """Anexa submissão na aba definida em SHEETS_WORKSHEET_NAME (fila de aprovação)."""
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
# 4) Carrega dados aprovados + concatena com fallback
# -----------------------------------------------------------------------------
df_sheets, from_sheets, debug_msg = load_approved_projects()
if not from_sheets:
    st.info("ℹ️ Usando dados locais temporários (o Google Sheets não está disponível).")
    if debug_msg:
        st.caption(f"Debug: {debug_msg}")

df_all = pd.concat([FALLBACK_DF, df_sheets], ignore_index=True)
df_all = df_all.drop_duplicates(subset=["city", "project_name"], keep="first")

for c in ("lat", "lon"):
    if c in df_all.columns:
        df_all[c] = pd.to_numeric(df_all[c], errors="coerce")

# -----------------------------------------------------------------------------
# 5) Sidebar — filtros
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
df = df_all if selected_country == "All" else df_all[df_all["country"] == selected_country].copy()

# -----------------------------------------------------------------------------
# 6) Mapa (apenas linhas com lat/lon válidos)
# -----------------------------------------------------------------------------
df_map = df.dropna(subset=["lat", "lon"]).copy()

if not df_map.empty:
    lat0, lon0 = df_map["lat"].mean(), df_map["lon"].mean()
else:
    lat0, lon0 = 15, 0

m = folium.Map(location=[lat0, lon0], zoom_start=2, tiles="CartoDB dark_matter")

grouped = (
    df_map.groupby(["country", "city", "lat", "lon"], as_index=False)
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
st.markdown("---")

# -----------------------------------------------------------------------------
# 7) Tabela dos pontos visíveis + Download CSV/HTML
# -----------------------------------------------------------------------------
st.markdown("### Projects on the map (current filter)")
if df_map.empty:
    st.caption("Nenhum ponto com latitude/longitude válidos para o filtro atual.")
else:
    # organiza colunas amigáveis
    cols = ["country", "city", "lat", "lon", "project_name", "years", "status", "data_types", "description", "contact", "access", "url"]
    cols = [c for c in cols if c in df_map.columns]
    df_map_view = df_map[cols].sort_values(["country", "city", "project_name"], na_position="last")
    st.dataframe(df_map_view, use_container_width=True, hide_index=True)

    # CSV
    csv_buf = StringIO()
    df_map_view.to_csv(csv_buf, index=False)
    st.download_button(
        "⬇️ Download CSV",
        data=csv_buf.getvalue().encode("utf-8"),
        file_name="ideamaps_projects_on_map.csv",
        mime="text/csv",
    )

    # HTML (tabela)
    html_table = df_map_view.to_html(index=False, escape=False)
    st.download_button(
        "⬇️ Download HTML",
        data=html_table.encode("utf-8"),
        file_name="ideamaps_projects_on_map.html",
        mime="text/html",
    )

st.markdown("---")

# -----------------------------------------------------------------------------
# 8) Lista (cards)
# -----------------------------------------------------------------------------
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

# -----------------------------------------------------------------------------
# 9) Formulário — país com auto-preenchimento de lat/lon (callback fora do form)
# -----------------------------------------------------------------------------
st.header("Add new project (goes to review queue)")

# session_state
if "form_country" not in st.session_state:
    st.session_state.form_country = ""
if "lat_input" not in st.session_state:
    st.session_state.lat_input = 0.0
if "lon_input" not in st.session_state:
    st.session_state.lon_input = 0.0

def _apply_country_center(country: str):
    if country in COUNTRY_CENTER:
        st.session_state.lat_input, st.session_state.lon_input = COUNTRY_CENTER[country]

form_country_options = sorted(set(list(COUNTRY_CENTER.keys()) + df_all["country"].dropna().tolist()))

st.subheader("Select country")
st.caption("Ao escolher o país, latitude/longitude serão preenchidos automaticamente (você pode editar depois).")
st.session_state.form_country = st.selectbox(
    "Country (for auto lat/lon)",
    options=[""] + form_country_options,
    index=([""] + form_country_options).index(st.session_state.form_country) if st.session_state.form_country in ([""] + form_country_options) else 0,
    key="country_selector_outside_form",
    on_change=lambda: _apply_country_center(st.session_state.country_selector_outside_form),
)

with st.form("add_project_form"):
    col1, col2 = st.columns(2)
    with col1:
        new_city = st.text_input("City")
        new_lat = st.number_input("Latitude", value=float(st.session_state.lat_input), format="%.6f", key="lat_input")
    with col2:
        new_name = st.text_input("Project name")
        new_lon = st.number_input("Longitude", value=float(st.session_state.lon_input), format="%.6f", key="lon_input")

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
        "country": st.session_state.form_country,
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
