# app.py
import os
import json
import time
import requests
from datetime import datetime

import pandas as pd
import streamlit as st
import folium
from streamlit_folium import st_folium

import gspread
from google.oauth2.service_account import Credentials

# =========================================================
# CONFIG DA PÁGINA
# =========================================================
st.set_page_config(
    page_title="IDEAMAPS Global Metadata Explorer",
    page_icon="🌍",
    layout="wide",
)

# =========================================================
# FUNÇÕES DE GOOGLE SHEETS
# =========================================================
REQUIRED_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

@st.cache_resource(show_spinner=False)
def get_ws():
    """Abre a worksheet (aba) do Google Sheets usando a service account das secrets."""
    sa = st.secrets["gcp_service_account"]
    creds = Credentials.from_service_account_info(sa, scopes=REQUIRED_SCOPES)

    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(st.secrets["SHEETS_SPREADSHEET_ID"])
    ws = spreadsheet.worksheet(st.secrets["SHEETS_WORKSHEET_NAME"])
    return ws

def load_approved_projects() -> pd.DataFrame:
    """Lê todas as linhas aprovadas e retorna DataFrame pronto para o mapa."""
    ws = get_ws()
    values = ws.get_all_records()
    if not values:
        return pd.DataFrame(columns=[
            "country","city","lat","lon","project_name","years","status",
            "data_types","description","contact","access","url","approved","created_at"
        ])

    df = pd.DataFrame(values)
    # Normaliza colunas que podem não existir nas primeiras inserções
    for col in ["approved", "lat", "lon", "url"]:
        if col not in df.columns:
            df[col] = None

    # converte tipos
    def to_float(x):
        try:
            return float(str(x).replace(",", "."))
        except Exception:
            return None

    df["lat"] = df["lat"].apply(to_float)
    df["lon"] = df["lon"].apply(to_float)

    # filtra somente aprovados (TRUE/True/true/1)
    def is_true(x):
        if isinstance(x, bool): return x
        s = str(x).strip().lower()
        return s in ("true","1","yes","y","sim")
    if "approved" in df.columns:
        df = df[df["approved"].apply(is_true)]

    # limpa linhas sem coordenadas
    df = df.dropna(subset=["lat", "lon"], how="any")
    return df

def append_submission_to_sheet(row_dict: dict):
    """Acrescenta uma submissão no final da planilha (approved=FALSE)."""
    ws = get_ws()

    # garante cabeçalho padronizado (ordem das colunas)
    header = [
        "country","city","lat","lon","project_name","years","status",
        "data_types","description","contact","access","url",
        "approved","created_at","submitted_by"
    ]
    # cria header se planilha estiver vazia
    if ws.row_count == 0 or ws.acell("A1").value is None:
        ws.append_row(header, value_input_option="RAW")
        time.sleep(0.3)

    # monta a linha na ordem do header
    payload = {**{h:"" for h in header}, **row_dict}
    payload["approved"] = payload.get("approved", False)
    payload["created_at"] = payload.get("created_at", datetime.utcnow().isoformat())

    row = [payload.get(h, "") for h in header]
    ws.append_row(row, value_input_option="RAW")

# =========================================================
# UI: CABEÇALHO
# =========================================================
st.markdown(
    """
    <div style="
        background: linear-gradient(90deg,#0f172a 0%,#1e293b 50%,#0f172a 100%);
        padding:1.0rem 1.2rem; border-radius:0.75rem; border:1px solid #334155;
        margin-bottom:1rem;">
      <div style="color:#fff; font-size:1.2rem; font-weight:700;">
        IDEAMAPS Global Metadata Explorer 🌍
      </div>
      <div style="color:#94a3b8; font-size:0.85rem;">
        Catálogo vivo de projetos e datasets (spatial / quantitative / qualitative)
        produzidos pela rede IDEAMAPS e parceiros.
      </div>
    </div>
    """,
    unsafe_allow_html=True
)

# =========================================================
# PAINEL DE DEBUG (opcional, útil para diagnóstico)
# =========================================================
with st.expander("🔧 Debug: Google Sheets connection", expanded=False):
    st.write("Spreadsheet ID:", st.secrets.get("SHEETS_SPREADSHEET_ID", "")[:12] + "…")
    st.write("Worksheet name:", st.secrets.get("SHEETS_WORKSHEET_NAME", ""))
    if st.button("Force refresh connection (clear cache)"):
        try:
            st.cache_resource.clear()
            st.success("Cache limpo. Use ▸ Rerun no menu do app.")
        except Exception as e:
            st.error(f"Erro ao limpar cache: {e}")

    try:
        ws_dbg = get_ws()
        st.success("Consegui abrir a worksheet ✅")
        vals = ws_dbg.get_all_values()
        if vals:
            st.write("Cabeçalho:", vals[0])
            st.write("Últimas linhas:", vals[-3:])
        else:
            st.info("Planilha vazia por enquanto.")
    except Exception as e:
        st.error(f"Falha ao conectar/ler: {e}")

    if st.button("Append TEST row now"):
        try:
            append_submission_to_sheet({
                "country":"TEST","city":"Ping","lat":0.0,"lon":0.0,
                "project_name":"Connectivity Check","years":"0000–0000",
                "status":"Planning","data_types":"None","description":"Debug row",
                "contact":"System","access":"N/A","url":"","submitted_by":"debug",
            })
            st.success("Linha de teste enviada. Verifique a planilha.")
        except Exception as e:
            st.error(f"Não consegui escrever: {e}")

# =========================================================
# CARREGAR DADOS APROVADOS
# =========================================================
with st.spinner("Loading approved projects…"):
    df_all = load_approved_projects()

# =========================================================
# SIDEBAR: FILTROS + TILESET
# =========================================================
st.sidebar.markdown("### 🌍 IDEAMAPS Explorer")
all_countries = ["All"] + sorted(df_all["country"].dropna().unique().tolist())
selected_country = st.sidebar.selectbox("Filter by country", all_countries)

TILESETS = {
    "CartoDB Dark Matter": "CartoDB dark_matter",
    "OpenStreetMap": "OpenStreetMap",
    "Stamen Terrain": "Stamen Terrain",
    "Stamen Toner": "Stamen Toner",
    "CartoDB Positron": "CartoDB positron",
}
tiles_choice = st.sidebar.selectbox("Map style", list(TILESETS.keys()))

if selected_country != "All":
    df = df_all[df_all["country"] == selected_country].copy()
else:
    df = df_all.copy()

# =========================================================
# MAPA
# =========================================================
m = folium.Map(location=[15, 0], zoom_start=2, tiles=TILESETS[tiles_choice])

# Agrupa por cidade para um único marcador por par (city,country)
if not df.empty:
    grouped = (
        df.groupby(["country", "city", "lat", "lon"], as_index=False)
        .agg({"project_name": list})
    )

    for _, row in grouped.iterrows():
        country, city, lat, lon = row["country"], row["city"], row["lat"], row["lon"]
        projects = df_all[(df_all["country"] == country) & (df_all["city"] == city)][
            ["project_name", "years", "status", "url"]
        ].to_dict(orient="records")

        # popup com links
        lines = [f"<b>{city}, {country}</b><br/>", "<ul style='padding-left:1rem;margin:0;'>"]
        for p in projects:
            link = f"<br/><a href='{p.get('url','')}' target='_blank' style='color:#38bdf8;'>🔗 Open project</a>" if p.get("url") else ""
            lines.append(
                f"<li style='font-size:0.8rem;line-height:1.2;'><b>{p['project_name']}</b>{link}"
                f"<br/><span style='color:#888'>Years: {p['years']} — Status: {p['status']}</span></li>"
            )
        lines.append("</ul>")
        html_popup = "<div style='font-size:0.8rem;'>" + "".join(lines) + "</div>"

        any_active = any(str(p["status"]).lower() == "active" for p in projects)
        color = "#38bdf8" if any_active else "#facc15"
        tooltip_text = f"{city}, {country} — {projects[0]['project_name'] if projects else ''}"

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

# =========================================================
# LISTA DE PROJETOS (CARDS)
# =========================================================
st.markdown("### Projects")
if df.empty:
    st.info("No approved projects for this filter yet.")
else:
    for _, r in df.sort_values(["country","city","project_name"]).iterrows():
        url_html = f"<a href='{r.get('url','')}' target='_blank' style='color:#38bdf8;'>🔗 Open project</a>" if pd.notna(r.get("url")) and str(r.get("url")).strip() else ""
        st.markdown(
            f"""
            <div style="font-size:0.85rem;border:1px solid #334155;background:#1e293b;
                        border-radius:12px;padding:12px 16px;margin-bottom:12px;">
              <div style="font-weight:700;color:#fff;">{r.get('project_name','')}</div>
              <div style="color:#38bdf8;">{r.get('city','')}, {r.get('country','')} — {r.get('years','')} — {r.get('status','')}</div>
              <div style="margin-top:6px;color:#cbd5e1;"><b>Data types:</b> {r.get('data_types','')}</div>
              <div style="margin-top:6px;color:#cbd5e1;">{r.get('description','')}</div>
              <div style="margin-top:8px;color:#94a3b8;"><b>Contact:</b> {r.get('contact','')}<br/><b>Access:</b> {r.get('access','')}</div>
              <div style="margin-top:8px;">{url_html}</div>
            </div>
            """,
            unsafe_allow_html=True
        )

st.markdown("---")

# =========================================================
# FORMULÁRIO: SUBMISSÃO
# =========================================================
st.header("Submit a new project (goes to review queue)")

with st.form("add_project_form", clear_on_submit=True):
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        new_country = st.text_input("Country*")
    with c2:
        new_city = st.text_input("City*")
    with c3:
        new_lat = st.number_input("Latitude*", value=0.0, format="%.6f")
    with c4:
        new_lon = st.number_input("Longitude*", value=0.0, format="%.6f")

    new_name = st.text_input("Project name*")
    c5, c6 = st.columns(2)
    with c5:
        new_years = st.text_input("Years (e.g. 2022–2024)")
    with c6:
        new_status = st.selectbox("Status", ["Active", "Legacy", "Completed", "Planning"])

    new_types = st.text_area("Data types (Spatial? Quantitative? Qualitative?)")
    new_desc  = st.text_area("Short description")
    c7, c8 = st.columns(2)
    with c7:
        new_contact = st.text_input("Contact / Responsible institution")
    with c8:
        new_access  = st.text_input("Access / License / Ethics")

    new_url = st.text_input("Project URL (optional)")
    submitted_by = st.text_input("Your email (for follow-up)")

    submitted = st.form_submit_button("Submit for review")

    if submitted:
        if not all([new_country, new_city, new_name]):
            st.error("Please fill at least Country, City and Project name.")
        else:
            row = {
                "country": new_country.strip(),
                "city": new_city.strip(),
                "lat": float(new_lat),
                "lon": float(new_lon),
                "project_name": new_name.strip(),
                "years": new_years.strip(),
                "status": new_status,
                "data_types": new_types.strip(),
                "description": new_desc.strip(),
                "contact": new_contact.strip(),
                "access": new_access.strip(),
                "url": new_url.strip(),
                "approved": False,
                "created_at": datetime.utcnow().isoformat(),
                "submitted_by": submitted_by.strip(),
            }
            try:
                append_submission_to_sheet(row)
                st.success("Submission received ✅. It will appear on the map after approval.")
            except Exception as e:
                st.error(f"Could not save to Google Sheets: {e}")

            # EmailJS (opcional)
            try:
                sj = {
                    "service_id": st.secrets.get("EMAILJS_SERVICE_ID", ""),
                    "template_id": st.secrets.get("EMAILJS_TEMPLATE_ID", ""),
                    "user_id": st.secrets.get("EMAILJS_PUBLIC_KEY", ""),
                    "template_params": {
                        "country": row["country"],
                        "city": row["city"],
                        "project_name": row["project_name"],
                        "years": row["years"],
                        "status": row["status"],
                        "contact": row["contact"],
                        "access": row["access"],
                        "url": row["url"],
                        "submitted_by": row["submitted_by"],
                        "created_at": row["created_at"],
                    },
                }
                if all([sj["service_id"], sj["template_id"], sj["user_id"]]):
                    r = requests.post(
                        "https://api.emailjs.com/api/v1.0/email/send",
                        headers={"Content-Type": "application/json"},
                        data=json.dumps(sj),
                        timeout=10,
                    )
                    if r.status_code == 200:
                        st.info("A notification email was sent to the IDEAMAPS team.")
                    else:
                        st.warning(f"Email not sent (status {r.status_code}).")
                else:
                    st.caption("EmailJS not configured in secrets; skipping email.")
            except Exception as e:
                st.warning(f"Email notify failed: {e}")
