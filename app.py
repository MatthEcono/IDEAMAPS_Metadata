# ──────────────────────────────────────────────────────────────────────────────
# 9) SUBMISSÃO DE OUTPUT (ordem correta + keys estáveis + múltiplas linhas)
# ──────────────────────────────────────────────────────────────────────────────
st.markdown("---")
st.header("Submit Output (goes to review queue)")

# ===== Estado/Init =====
if "city_list_output" not in st.session_state:
    st.session_state.city_list_output = []
if "_clear_city_field_out" not in st.session_state:
    st.session_state._clear_city_field_out = False
if "_clear_city_field_newproj" not in st.session_state:
    st.session_state._clear_city_field_newproj = False
if "map_center" not in st.session_state:
    st.session_state.map_center = None
if "map_zoom" not in st.session_state:
    st.session_state.map_zoom = 2
if "output_countries" not in st.session_state:
    st.session_state.output_countries = []   # <- chave fixa dos países

def add_city(country, city_csv):
    """Adiciona 1+ cidades para um país (separadas por vírgula)"""
    if country and country != SELECT_PLACEHOLDER and city_csv.strip():
        for c in [x.strip() for x in city_csv.split(",") if x.strip()]:
            pair = f"{country} — {c}"
            if pair not in st.session_state.city_list_output:
                st.session_state.city_list_output.append(pair)
        return True
    return False

def remove_city(i):
    if 0 <= i < len(st.session_state.city_list_output):
        st.session_state.city_list_output.pop(i)

def clear_form():
    st.session_state.city_list_output = []
    st.session_state._clear_city_field_out = False
    st.session_state._clear_city_field_newproj = False
    st.session_state.map_center = None
    st.session_state.map_zoom = 2
    st.session_state.output_countries = []

with st.form("output_form", clear_on_submit=False):
    st.subheader("Basic Information")

    submitter_email = st.text_input(
        "Submitter email (required for review)*",
        placeholder="name@org.org"
    )

    project_tax_sel = st.selectbox(
        "Project Name (taxonomy)*",
        options=PROJECT_TAXONOMY
    )
    is_other_project = project_tax_sel.startswith("Other")
    project_tax_other = ""
    if is_other_project:
        project_tax_other = st.text_input("Please specify the project (taxonomy)*")

    output_type_sel = st.selectbox("Output Type*", options=OUTPUT_TYPES)

    # Se quiser manter o 'Data type' opcional: deixe SELECT_PLACEHOLDER por padrão.
    output_data_type = SELECT_PLACEHOLDER
    if output_type_sel == "Dataset":
        # Se quiser travar quando Dataset, troque 'disabled=False' por 'disabled=True' e não valide.
        output_data_type = st.selectbox(
            "Data type (for datasets)*",
            options=[SELECT_PLACEHOLDER] + DATASET_DTYPES,
            disabled=False
        )
    else:
        output_data_type = ""

    output_type_other = ""
    if output_type_sel.startswith("Other"):
        output_type_other = st.text_input("Please specify the output type*")

    output_title = st.text_input("Output Name*")
    output_url   = st.text_input("Output URL (optional)")

    # ── Geographic Coverage (vem logo após Output URL) ──
    st.subheader("Geographic Coverage")
    st.multiselect(
        "Select countries (select 'Global' for worldwide coverage)*",
        options=_countries_with_global_first(COUNTRY_NAMES) + ["Other: ______"],
        key="output_countries"  # <- chave fixa
    )
    selected_countries = st.session_state.output_countries[:]  # fonte da verdade
    is_global = "Global" in selected_countries

    output_country_other = ""
    if "Other: ______" in selected_countries:
        output_country_other = st.text_input("Please specify other geographic coverage")

    # ── Cidades (aparece quando há país e não é Global) ──
    if selected_countries and not is_global:
        available_countries = [c for c in selected_countries if c not in ["Global", "Other: ______"]]
        if available_countries:
            st.write("**Add cities for this output:**")
            c1, c2, c3 = st.columns([2, 2, 1])
            with c1:
                output_country_select = st.selectbox(
                    "Select country",
                    options=[SELECT_PLACEHOLDER] + available_countries,
                    key="output_country_select"
                )
            with c2:
                if st.session_state._clear_city_field_out and "output_city_input" in st.session_state:
                    del st.session_state["output_city_input"]
                    st.session_state._clear_city_field_out = False
                output_city_input = st.text_input(
                    "City name (accepts multiple, separated by commas)",
                    placeholder="Enter city name",
                    key="output_city_input"
                )
            with c3:
                st.write(""); st.write("")
                if st.form_submit_button("➕ Add City", use_container_width=True, key="add_city_output"):
                    if add_city(output_country_select, output_city_input):
                        st.session_state._clear_city_field_out = True
                        st.rerun()
    else:
        if is_global:
            st.info("🌍 Global coverage selected — city selection is disabled")

    # Lista de cidades adicionadas
    if st.session_state.city_list_output:
        st.write("**Added cities:**")
        for i, pair in enumerate(st.session_state.city_list_output):
            c1, c2 = st.columns([4,1])
            with c1:
                st.write(f"📍 {pair}")
            with c2:
                if st.form_submit_button("🗑️ Remove", key=f"rm_city_{i}"):
                    remove_city(i); st.rerun()

    # Preview mapa (centra no 1º país útil)
    if st.session_state.city_list_output and not is_global:
        avail = [c for c in selected_countries if c not in ["Global","Other: ______"]]
        center = COUNTRY_CENTER_FULL.get(avail[0], (0,0)) if avail else (0,0)
        m = folium.Map(location=center, zoom_start=3, tiles="CartoDB positron")
        for ctry in avail:
            if ctry in COUNTRY_CENTER_FULL:
                folium.CircleMarker(
                    location=COUNTRY_CENTER_FULL[ctry], radius=10, tooltip=ctry,
                    color="#2563eb", fill=True, fill_opacity=0.6
                ).add_to(m)
        for pair in st.session_state.city_list_output:
            if "—" in pair:
                ctry, cty = [p.strip() for p in pair.split("—",1)]
                if ctry in COUNTRY_CENTER_FULL:
                    folium.Marker(
                        location=COUNTRY_CENTER_FULL[ctry],
                        tooltip=f"{cty} ({ctry})",
                        icon=folium.Icon(color="red", icon="info-sign")
                    ).add_to(m)
        st_folium(m, height=300, width=None)

    # ── Additional info ──
    st.subheader("Additional Information")
    current_year = datetime.utcnow().year
    base_years_desc = list(range(current_year, 1999, -1))
    years_selected = st.multiselect("Year of output release", base_years_desc)

    output_desc = st.text_area("Short description of output")
    output_contact = st.text_input("Name & institution of person responsible")
    output_linkedin = st.text_input("LinkedIn address of contact")
    project_url_for_output = st.text_input("Project URL (optional, if different)")

    c1, c2 = st.columns([1,1])
    with c1:
        submitted = st.form_submit_button("✅ Submit for Review", use_container_width=True, type="primary")
    with c2:
        if st.form_submit_button("🗑️ Clear Form", use_container_width=True, type="secondary"):
            clear_form(); st.rerun()

# ──────────────────────────────────────────────────────────────────────────────
# Processamento do envio (inclui múltiplas linhas por país/cidade)
# ──────────────────────────────────────────────────────────────────────────────
if submitted:
    errors = []
    if not submitter_email.strip():
        errors.append("❌ Submitter email is required")
    if not output_title.strip():
        errors.append("❌ Output name is required")
    if not st.session_state.output_countries:
        errors.append("❌ At least one country must be selected")
    if output_type_sel == "Dataset" and output_data_type == SELECT_PLACEHOLDER:
        errors.append("❌ Data type is required for datasets")
    if is_other_project and not project_tax_other.strip():
        errors.append("❌ Project name is required when selecting 'Other'")

    if errors:
        for e in errors: st.error(e)
        st.stop()

    try:
        # 1) Se "Other": opcionalmente registrar projeto (uma linha por país/cidade)
        if is_other_project:
            wsP, errP = ws_projects()
            if errP or wsP is None:
                st.error(errP or "Worksheet unavailable for projects."); st.stop()

            # Países sem cidade
            new_project_countries = st.session_state.get("new_project_countries", []) if "new_project_countries" in st.session_state else []
            for country in new_project_countries:
                latp, lonp = COUNTRY_CENTER_FULL.get(country, (None, None))
                rowP = {
                    "country": country, "city": "", "lat": latp, "lon": lonp,
                    "project_name": project_tax_other.strip(), "years": "",
                    "status": "", "data_types": "", "description": "",
                    "contact": "", "access": "", "url": "",
                    "submitter_email": submitter_email,
                    "is_edit": "FALSE", "edit_target": "", "edit_request": "New project via output submission",
                    "approved": "FALSE",
                    "created_at": datetime.utcnow().isoformat(timespec="seconds")+"Z",
                }
                _append_row(wsP, PROJECTS_HEADERS, rowP)

            # Cidades (se tiver)
            for pair in st.session_state.city_list_output:
                if "—" in pair:
                    ctry, cty = [p.strip() for p in pair.split("—",1)]
                    latp, lonp = COUNTRY_CENTER_FULL.get(ctry, (None, None))
                    rowP = {
                        "country": ctry, "city": cty, "lat": latp, "lon": lonp,
                        "project_name": project_tax_other.strip(), "years": "",
                        "status": "", "data_types": "", "description": "",
                        "contact": "", "access": "", "url": "",
                        "submitter_email": submitter_email,
                        "is_edit": "FALSE", "edit_target": "", "edit_request": "New project via output submission",
                        "approved": "FALSE",
                        "created_at": datetime.utcnow().isoformat(timespec="seconds")+"Z",
                    }
                    _append_row(wsP, PROJECTS_HEADERS, rowP)

        # 2) Gravar OUTPUT no sheet — múltiplas linhas
        wsO, errO = ws_outputs()
        if errO or wsO is None:
            st.error(errO or "Worksheet unavailable for outputs."); st.stop()

        # anos formatados
        final_years_sorted_desc = sorted(set(years_selected), reverse=True)
        final_years_str = ",".join(str(y) for y in final_years_sorted_desc) if final_years_sorted_desc else ""

        # Se não for Dataset, força em branco
        if output_type_sel != "Dataset":
            output_data_type = ""

        # Se GLOBAL → 1 única linha
        if is_global:
            rowO = {
                "project": (project_tax_other.strip() if is_other_project else project_tax_sel),
                "output_title": output_title,
                "output_type": ("" if output_type_sel.startswith("Other") else output_type_sel),
                "output_type_other": (output_type_other if output_type_sel.startswith("Other") else ""),
                "output_data_type": output_data_type,
                "output_url": output_url,
                "output_country": "Global",
                "output_country_other": (output_country_other if "Other: ______" in selected_countries else ""),
                "output_city": "",
                "output_year": final_years_str,
                "output_desc": output_desc,
                "output_contact": output_contact,
                "output_email": "",
                "output_linkedin": output_linkedin,
                "project_url": (project_url_for_output or ("" if not is_other_project else "")),
                "submitter_email": submitter_email,
                "is_edit": "FALSE","edit_target":"","edit_request":"New submission",
                "approved": "FALSE",
                "created_at": datetime.utcnow().isoformat(timespec="seconds")+"Z",
                "lat": "", "lon": "",
            }
            ok, msg = _append_row(wsO, OUTPUTS_HEADERS, rowO)
            if not ok:
                st.error(f"⚠️ {msg}"); st.stop()
        else:
            # Se houver cidades → 1 linha por cidade; senão → 1 linha por país
            if st.session_state.city_list_output:
                for pair in st.session_state.city_list_output:
                    if "—" not in pair:
                        continue
                    ctry, cty = [p.strip() for p in pair.split("—",1)]
                    if ctry in ["Global", "Other: ______"]:
                        continue
                    lat_o, lon_o = COUNTRY_CENTER_FULL.get(ctry, (None, None))
                    rowO = {
                        "project": (project_tax_other.strip() if is_other_project else project_tax_sel),
                        "output_title": output_title,
                        "output_type": ("" if output_type_sel.startswith("Other") else output_type_sel),
                        "output_type_other": (output_type_other if output_type_sel.startswith("Other") else ""),
                        "output_data_type": output_data_type,
                        "output_url": output_url,
                        "output_country": ctry,
                        "output_country_other": (output_country_other if "Other: ______" in selected_countries else ""),
                        "output_city": cty,
                        "output_year": final_years_str,
                        "output_desc": output_desc,
                        "output_contact": output_contact,
                        "output_email": "",
                        "output_linkedin": output_linkedin,
                        "project_url": (project_url_for_output or ("" if not is_other_project else "")),
                        "submitter_email": submitter_email,
                        "is_edit": "FALSE","edit_target":"","edit_request":"New submission",
                        "approved": "FALSE",
                        "created_at": datetime.utcnow().isoformat(timespec="seconds")+"Z",
                        "lat": lat_o if lat_o is not None else "",
                        "lon": lon_o if lon_o is not None else "",
                    }
                    ok, msg = _append_row(wsO, OUTPUTS_HEADERS, rowO)
                    if not ok:
                        st.error(f"⚠️ {msg}"); st.stop()
            else:
                # Sem cidades → uma linha por país selecionado
                for ctry in [c for c in selected_countries if c not in ["Global","Other: ______"]]:
                    lat_o, lon_o = COUNTRY_CENTER_FULL.get(ctry, (None, None))
                    rowO = {
                        "project": (project_tax_other.strip() if is_other_project else project_tax_sel),
                        "output_title": output_title,
                        "output_type": ("" if output_type_sel.startswith("Other") else output_type_sel),
                        "output_type_other": (output_type_other if output_type_sel.startswith("Other") else ""),
                        "output_data_type": output_data_type,
                        "output_url": output_url,
                        "output_country": ctry,
                        "output_country_other": (output_country_other if "Other: ______" in selected_countries else ""),
                        "output_city": "",
                        "output_year": final_years_str,
                        "output_desc": output_desc,
                        "output_contact": output_contact,
                        "output_email": "",
                        "output_linkedin": output_linkedin,
                        "project_url": (project_url_for_output or ("" if not is_other_project else "")),
                        "submitter_email": submitter_email,
                        "is_edit": "FALSE","edit_target":"","edit_request":"New submission",
                        "approved": "FALSE",
                        "created_at": datetime.utcnow().isoformat(timespec="seconds")+"Z",
                        "lat": lat_o if lat_o is not None else "",
                        "lon": lon_o if lon_o is not None else "",
                    }
                    ok, msg = _append_row(wsO, OUTPUTS_HEADERS, rowO)
                    if not ok:
                        st.error(f"⚠️ {msg}"); st.stop()

        st.success("✅ Output submission queued for review!")
        clear_form()
        st.rerun()

    except Exception as e:
        st.error(f"An error occurred: {e}")
