import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium

# =========================
# DADOS INICIAIS
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
        "access": "Partly restricted; request via IDEAMAPS team."
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
        "access": "Internal / ethics-controlled"
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
        "access": "Some layers public; others restricted"
    },
]

df = pd.DataFrame(data)

# =========================
# CONFIGURAÇÃO DA PÁGINA
# =========================
st.set_page_config(page_title="IDEAMAPS Metadata Map", layout="wide", page_icon="🌍")

st.sidebar.title("IDEAMAPS Metadata Explorer 🌍")
selected_country = st.sidebar.selectbox("Filter by country", ["All"] + sorted(df["country"].unique()))

if selected_country != "All":
    df = df[df["country"] == selected_country]

# =========================
# MAPA
# =========================
m = folium.Map(location=[15, 0], zoom_start=2)
for _, row in df.iterrows():
    popup_html = f"<b>{row['project_name']}</b><br>{row['city']}, {row['country']}<br>{row['years']}<br>{row['data_types']}"
    folium.CircleMarker(
        location=[row['lat'], row['lon']],
        radius=6,
        color="blue" if row["status"] == "Active" else "orange",
        fill=True,
        fill_opacity=0.8,
        popup=folium.Popup(popup_html, max_width=300),
        tooltip=row['city'],
    ).add_to(m)

st_folium(m, height=500, width=None)

st.markdown("---")
st.write("### Projects list")
st.dataframe(df[["country", "city", "project_name", "years", "status", "access"]])
