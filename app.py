import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium

# =========================
# DADOS INICIAIS (agora com URL)
# =========================
data = [
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
        "url": "https://ideamapsnetwork.org"  # ajuste este link
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
        "url": "https://chorus.york.ac.uk"  # ajuste este link
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
        "url": "https://ideamapsnetwork.org"  # ajuste este link
    },
]

df_all = pd.DataFrame(data)

# =========================
# CONFIGURAÇÃO DA PÁGINA
# =========================
st.set_page_config(
    page_title="IDEAMAPS Global Metadata Explorer",
    layout="wide",
    page_icon="🌍",
)

# =========================
# HEADER
# =========================
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

# =========================
# SIDEBAR (FILTROS)
# =========================
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

all_countries = ["All"] + sorted(df_all["country"].unique().tolist())
selected_country = st.sidebar.selectbox("Filter by country", all_countries)

if selected_country != "All":
    df = df_all[df_all["country"] == selected_country].copy()
else:
    df = df_all.copy()

# =========================
# MAPA (DARK) + LINK NO POPUP
# =========================

# agrupar por cidade para não repetir o mesmo ponto
grouped = (
    df.groupby(["country", "city", "lat", "lon"], as_index=False)
    .agg({"project_name": list})
)

# mapa base escuro
m = folium.Map(
    location=[15, 0],
    zoom_start=2,
    tiles="CartoDB dark_matter"
)

# adicionar marcadores
for _, row in grouped.iterrows():
    country = row["country"]
    city = row["city"]
    lat = row["lat"]
    lon = row["lon"]

    # todos os projetos daquela cidade (incluindo URL)
    project_list = df_all[
        (df_all["country"] == country) & (df_all["city"] == city)
    ][["project_name", "years", "status", "url"]].to_dict(orient="records")

    # popup HTML com link clicável (🔗 Open project)
    popup_lines = [f"<b>{city}, {country}</b><br/>"]
    popup_lines.append("<ul style='padding-left:1rem;margin:0;'>")
    for proj in project_list:
        link_html = (
            f"<br/><a href='{proj['url']}' target='_blank' "
            f"style='color:#38bdf8; text-decoration:none;'>🔗 Open project</a>"
            if proj.get("url") else ""
        )
        popup_lines.append(
            f"<li style='font-size:0.8rem; line-height:1.2;'>"
            f"<b>{proj['project_name']}</b>"
            f"{link_html}"
            f"<br/><span style='color:#888'>Years: {proj['years']} — Status: {proj['status']}</span>"
            f"</li>"
        )
    popup_lines.append("</ul>")

    html_popup = "<div style='font-size:0.8rem; color:#fff;'>" + "".join(popup_lines) + "</div>"

    any_active = any(p["status"].lower() == "active" for p in project_list)
    color = "#38bdf8" if any_active else "#facc15"

    # tooltip não aceita link clicável; mostra nome do 1º projeto como texto
    tooltip_text = f"{city}, {country}"
    if project_list:
        tooltip_text += f" — {project_list[0]['project_name']}"

    folium.CircleMarker(
        location=[lat, lon],
        radius=6,
        color=color,
        fill=True,
        fill_opacity=0.85,
        popup=folium.Popup(html_popup, max_width=320),
        tooltip=tooltip_text,
    ).add_to(m)

# desenhar mapa
st_folium(m, height=500, width=None)

st.markdown("---")

# =========================
# LISTA DE PROJETOS (CARDS COM LINK)
# =========================
st.markdown(
    "<h3 style='color:#fff; margin-bottom:0.5rem;'>Projects</h3>"
    "<p style='color:#94a3b8; font-size:0.8rem; margin-top:0;'>Resumo dos projetos conhecidos no país selecionado.</p>",
    unsafe_allow_html=True
)

for _, row in df.iterrows():
    url_html = (
        f"<a href='{row['url']}' target='_blank' "
        f"style='color:#38bdf8; text-decoration:none;'>🔗 Open project</a>"
        if pd.notna(row.get("url")) and str(row.get("url")).strip() != ""
        else ""
    )

    st.markdown(
        f"""
        <div style="
            font-size:0.8rem;
            line-height:1.4;
            border:1px solid #334155;
            background-color:#1e293b;
            border-radius:12px;
            padding:12px 16px;
            margin-bottom:12px;
        ">
          <div style="font-size:0.9rem; font-weight:600; color:#fff;">
            {row['project_name']}
          </div>

          <div style="font-size:0.75rem; color:#38bdf8;">
            {row['city']}, {row['country']} — {row['years']} — {row['status']}
          </div>

          <div style="margin-top:6px; font-size:0.8rem; color:#cbd5e1;">
            <b>Data types:</b> {row['data_types']}
          </div>

          <div style="margin-top:6px; font-size:0.8rem; color:#cbd5e1;">
            {row['description']}
          </div>

          <div style="margin-top:10px; font-size:0.7rem; color:#94a3b8;">
            <b>Contact:</b> {row['contact']}<br/>
            <b>Access:</b> {row['access']}
          </div>

          <div style="margin-top:8px;">
            {url_html}
          </div>
        </div>
        """,
        unsafe_allow_html=True
    )

st.markdown("---")

# =========================
# FORMULÁRIO (gera bloco para copiar)
# =========================
st.header("Add new project (manual capture, not saved)")

with st.form("add_project_form"):
    new_country = st.text_input("Country")
    new_city = st.text_input("City")
    new_lat = st.number_input("Latitude", value=0.0, format="%.6f")
    new_lon = st.number_input("Longitude", value=0.0, format="%.6f")
    new_name = st.text_input("Project name")
    new_years = st.text_input("Years (e.g. 2022–2024)")
    new_status = st.selectbox("Status", ["Active", "Legacy", "Completed", "Planning"])
    new_types = st.text_area("Data types (Spatial? Quantitative? Qualitative?)")
    new_desc = st.text_area("Short description")
    new_contact = st.text_input("Contact / Responsible institution")
    new_access = st.text_input("Access / License / Ethics")
    new_url = st.text_input("Project URL (optional)")

    submitted = st.form_submit_button("Generate row for copy-paste")

    import requests

# ==============================================================
# QUANDO O FORMULÁRIO É ENVIADO
# ==============================================================
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

    st.success("✅ Submission received. An email notification has been sent to the IDEAMAPS team.")

    # Mostra também o bloco pra referência
    st.code(new_row, language="python")

    # ===== Enviar e-mail via EmailJS =====
    EMAILJS_SERVICE_ID = "service_ygft3w1"     # <- substitua pelo seu
    EMAILJS_TEMPLATE_ID = "template_7spl6yj"   # <- substitua pelo seu
    EMAILJS_PUBLIC_KEY = "wbC-hdEG56dF4ebls"             # <- substitua pelo seu


    # Monta payload
    payload = {
        "service_id": EMAILJS_SERVICE_ID,
        "template_id": EMAILJS_TEMPLATE_ID,
        "user_id": EMAILJS_PUBLIC_KEY,
        "template_params": new_row,
    }

    # Envia requisição POST
    try:
        response = requests.post(
            "https://api.emailjs.com/api/v1.0/email/send",
            json=payload,
            timeout=10
        )
        if response.status_code == 200:
            st.info("📨 Email sent successfully!")
        else:
            st.warning(f"⚠️ Email not sent. Status: {response.status_code}")
    except Exception as e:
        st.error(f"Error sending email: {e}")

