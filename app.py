# app.py (apenas a seção 9 - SUBMISSÃO DE OUTPUT com as correções)
# ... (código anterior permanece igual)

# ──────────────────────────────────────────────────────────────────────────────
# 9) SUBMISSÃO DE OUTPUT
# ──────────────────────────────────────────────────────────────────────────────

_FORM_KEYS = {
    "submitter_email","project_tax_sel","project_tax_other",
    "new_project_url","new_project_contact","new_project_countries",
    "selected_country_city","city_add_proj","_clear_cities_flag",
    "output_type_sel","output_type_other","output_data_type",
    "output_title","output_url",
    "output_country","output_country_other",
    "output_city_dummy",
    "years_selected",
    "output_desc","output_contact","output_linkedin","project_url_for_output",
    "country_for_city", "output_countries", "city_list_output",
    "selected_city_newproj", "selected_city_output"
}

def _really_clear_output_form_state():
    for k in list(_FORM_KEYS):
        st.session_state.pop(k, None)
    st.session_state.pop("map_center", None)
    st.session_state["map_zoom"] = 2
    st.session_state["_clear_city_field_out"] = False
    st.session_state["_clear_city_field_newproj"] = False
    if "city_coordinates" in st.session_state:
        st.session_state.city_coordinates = {}

if "_pending_form_reset" not in st.session_state:
    st.session_state._pending_form_reset = False

if st.session_state._pending_form_reset:
    _really_clear_output_form_state()
    st.session_state._pending_form_reset = False

# Estado inicial
if "output_countries" not in st.session_state:
    st.session_state.output_countries = []
if "city_list_output" not in st.session_state:
    st.session_state.city_list_output = []
if "city_coordinates" not in st.session_state:
    st.session_state.city_coordinates = {}
if "map_center" not in st.session_state:
    st.session_state.map_center = None
if "map_zoom" not in st.session_state:
    st.session_state.map_zoom = 2
if "_clear_city_field_out" not in st.session_state:
    st.session_state._clear_city_field_out = False
if "_clear_city_field_newproj" not in st.session_state:
    st.session_state._clear_city_field_newproj = False

st.markdown("---")
st.header("Submit Output (goes to review queue)")

# Verifica se as cidades foram carregadas
if WORLD_CITIES.empty:
    st.warning("⚠️ City database is loading... Please wait a moment and refresh the page.")
else:
    st.success(f"✅ City database loaded with {len(WORLD_CITIES)} cities")

with st.form("OUTPUT_FORM", clear_on_submit=False):
    submitter_email = st.text_input(
        "Submitter email (required for review)",
        key="submitter_email",
        placeholder="name@org.org"
    )

    project_tax_sel = st.selectbox(
        "Project Name (taxonomy)",
        options=PROJECT_TAXONOMY,
        key="project_tax_sel"
    )
    is_other_project = project_tax_sel.startswith("Other")
    project_tax_other = ""
    if is_other_project:
        project_tax_other = st.text_input("Please specify the project (taxonomy)", key="project_tax_other")

    new_project_url = ""
    new_project_contact = ""
    if is_other_project:
        st.markdown("**New project details (required if not in taxonomy)**")
        countries_sel = st.multiselect(
            "Implementation countries (one or more)",
            COUNTRY_NAMES,
            key="new_project_countries"
        )
        colc1, colc2, colc3 = st.columns([2,2,1])
        with colc1:
            selected_country_city = st.selectbox(
                "Select implementation country for the city",
                options=[SELECT_PLACEHOLDER] + countries_sel if countries_sel else [SELECT_PLACEHOLDER],
                index=0, disabled=not bool(countries_sel),
                key="selected_country_city"
            )
        with colc2:
            # AUTOBUSCA DE CIDADES PARA NOVO PROJETO
            city_options = [SELECT_PLACEHOLDER]
            if selected_country_city and selected_country_city != SELECT_PLACEHOLDER:
                cities_data = get_cities_by_country(selected_country_city)
                if cities_data:
                    city_options.extend([city['city'] for city in cities_data])
                else:
                    st.info(f"No cities found for {selected_country_city}")
            
            selected_city_newproj = st.selectbox(
                "Select city",
                options=city_options,
                key="selected_city_newproj"
            )
        with colc3:
            st.write("")
            if st.form_submit_button("➕ Add city to NEW project"):
                if (selected_country_city and selected_country_city != SELECT_PLACEHOLDER and 
                    selected_city_newproj and selected_city_newproj != SELECT_PLACEHOLDER):
                    pair = f"{selected_country_city} — {selected_city_newproj}"
                    if pair not in st.session_state.city_list_output:
                        st.session_state.city_list_output.append(pair)
                        # Salva coordenadas da cidade
                        lat, lon = find_city_coordinates(selected_country_city, selected_city_newproj)
                        if lat and lon:
                            st.session_state.city_coordinates[pair] = (lat, lon)
                            st.success(f"📍 Coordinates found: {lat:.4f}, {lon:.4f}")
                        else:
                            st.warning("⚠️ City coordinates not found - using country center")
                    st.rerun()
                else:
                    st.warning("Select a valid country and city.")

        if st.session_state.get("city_list_output"):
            st.caption("Cities added to NEW project:")
            for i, it in enumerate(st.session_state.city_list_output):
                c1, c2 = st.columns([6,1])
                with c1: 
                    coords = st.session_state.city_coordinates.get(it, (None, None))
                    if coords[0] and coords[1]:
                        st.write(f"- {it} (📍 {coords[0]:.4f}, {coords[1]:.4f})")
                    else:
                        st.write(f"- {it} (⚠️ no coordinates)")
                with c2:
                    if st.form_submit_button("Remove", key=f"rm_city_newproj_{i}"):
                        st.session_state.city_list_output.pop(i)
                        if it in st.session_state.city_coordinates:
                            del st.session_state.city_coordinates[it]
                        st.rerun()

        new_project_url = st.text_input("Project URL (optional)", key="new_project_url")
        new_project_contact = st.text_input("Project contact / institution (optional)", key="new_project_contact")

    # CORREÇÃO: Output Type com Data Type travado quando não é Dataset
    output_type_sel = st.selectbox("Output Type", options=OUTPUT_TYPES, key="output_type_sel")
    
    # CORREÇÃO: Data Type só aparece e é habilitado quando Output Type = Dataset
    output_data_type = ""
    if output_type_sel == "Dataset":
        output_data_type = st.selectbox(
            "Data type (for datasets) *", 
            options=[SELECT_PLACEHOLDER] + DATASET_DTYPES,
            key="output_data_type"
        )
    else:
        # Quando não é Dataset, o campo fica vazio e não é exibido
        output_data_type = ""

    output_type_other = ""
    if output_type_sel.startswith("Other"):
        output_type_other = st.text_input("Please specify the output type", key="output_type_other")

    output_title = st.text_input("Output Name *", key="output_title")
    output_url   = st.text_input("Output URL (optional)", key="output_url")

    st.markdown("**Geographic coverage of output**")
    countries_fixed = _countries_with_global_first(COUNTRY_NAMES) + ["Other: ______"]
    output_countries = st.multiselect(
        "Select one or more countries (select 'Global' for worldwide coverage) *",
        options=countries_fixed,
        key="output_countries"
    )
    
    # CORREÇÃO: Lógica simplificada para Global
    is_global = "Global" in output_countries
    if is_global:
        st.info("Global coverage selected - city fields will be disabled")

    output_country_other = ""
    if "Other: ______" in output_countries:
        output_country_other = st.text_input("Please specify other geographic coverage", key="output_country_other")

    # AUTOBUSCA DE CIDADES PARA OUTPUT
    st.markdown("**Cities covered**")
    
    # CORREÇÃO: Prepara países disponíveis para seleção de cidades
    available_countries_for_cities = []
    if output_countries:
        available_countries_for_cities = [c for c in output_countries if c not in ["Global", "Other: ______"]]
    
    colx1, colx2, colx3 = st.columns([2,2,1])
    with colx1:
        # CORREÇÃO: Campo SEMPRE habilitado quando não é Global
        country_for_city = st.selectbox(
            "Country for the city",
            options=[SELECT_PLACEHOLDER] + available_countries_for_cities,
            index=0,
            key="country_for_city",
            disabled=is_global  # Só desabilita se for Global
        )
    with colx2:
        # Carrega cidades do país selecionado
        city_options_output = [SELECT_PLACEHOLDER]
        if country_for_city and country_for_city != SELECT_PLACEHOLDER:
            cities_data = get_cities_by_country(country_for_city)
            if cities_data:
                city_options_output.extend([city['city'] for city in cities_data])
            else:
                st.info(f"No cities found for {country_for_city}")
        
        selected_city_output = st.selectbox(
            "Select city",
            options=city_options_output,
            key="selected_city_output",
            disabled=is_global  # Só desabilita se for Global
        )
    with colx3:
        st.write("")
        # CORREÇÃO: Só desabilita o botão se for Global ou se não tiver seleção válida
        add_city_disabled = is_global or not country_for_city or country_for_city == SELECT_PLACEHOLDER or not selected_city_output or selected_city_output == SELECT_PLACEHOLDER
        if st.form_submit_button("➕ Add city to OUTPUT", disabled=add_city_disabled):
            if not is_global and country_for_city and country_for_city != SELECT_PLACEHOLDER and selected_city_output and selected_city_output != SELECT_PLACEHOLDER:
                pair = f"{country_for_city} — {selected_city_output}"
                if pair not in st.session_state.city_list_output:
                    st.session_state.city_list_output.append(pair)
                    # Salva coordenadas da cidade
                    lat, lon = find_city_coordinates(country_for_city, selected_city_output)
                    if lat and lon:
                        st.session_state.city_coordinates[pair] = (lat, lon)
                        st.success(f"📍 Coordinates found: {lat:.4f}, {lon:.4f}")
                    else:
                        st.warning("⚠️ City coordinates not found - using country center")
                st.rerun()
            elif is_global:
                st.warning("Cannot add cities for global coverage")
            else:
                st.warning("Choose a valid country and city.")

    if st.session_state.get("city_list_output") and not is_global:
        st.caption("Cities added to OUTPUT:")
        for i, it in enumerate(st.session_state.city_list_output):
            c1, c2 = st.columns([6,1])
            with c1: 
                coords = st.session_state.city_coordinates.get(it, (None, None))
                if coords[0] and coords[1]:
                    st.write(f"- {it} (📍 {coords[0]:.4f}, {coords[1]:.4f})")
                else:
                    st.write(f"- {it} (⚠️ no coordinates)")
            with c2:
                if st.form_submit_button("Remove", key=f"rm_city_out_{i}"):
                    st.session_state.city_list_output.pop(i)
                    if it in st.session_state.city_coordinates:
                        del st.session_state.city_coordinates[it]
                    st.rerun()

    # Mapa com cidades
    if not is_global and available_countries_for_cities:
        if available_countries_for_cities:
            first_country = available_countries_for_cities[0]
            if first_country in COUNTRY_CENTER_FULL:
                st.session_state.map_center = COUNTRY_CENTER_FULL[first_country]
                st.session_state.map_zoom = 3
                
            if st.session_state.get("map_center"):
                m = folium.Map(
                    location=st.session_state.map_center,
                    zoom_start=st.session_state.map_zoom,
                    tiles="CartoDB positron"
                )
                
                # Marca países
                for country in available_countries_for_cities:
                    if country in COUNTRY_CENTER_FULL:
                        latlon = COUNTRY_CENTER_FULL[country]
                        folium.CircleMarker(
                            location=latlon, radius=8, color="#2563eb",
                            fill=True, fill_opacity=0.9, tooltip=f"{country}"
                        ).add_to(m)
                
                # Marca cidades com coordenadas específicas
                for pair in st.session_state.get("city_list_output", []):
                    if "—" in pair:
                        ctry, cty = [p.strip() for p in pair.split("—",1)]
                        coords = st.session_state.city_coordinates.get(pair, None)
                        if coords and coords[0] is not None and coords[1] is not None:
                            # Usa coordenadas específicas da cidade
                            folium.Marker(
                                location=coords, 
                                tooltip=f"{cty} ({ctry})",
                                icon=folium.Icon(color="green", icon="info-sign")
                            ).add_to(m)
                        else:
                            # Fallback para centro do país
                            latlon = COUNTRY_CENTER_FULL.get(ctry)
                            if latlon:
                                folium.Marker(
                                    location=latlon, 
                                    tooltip=f"{cty} ({ctry})",
                                    icon=folium.Icon(color="orange", icon="info-sign")
                                ).add_to(m)
                st_folium(m, height=320, width=None)
    elif is_global:
        st.info("Map preview not available for global coverage")

    current_year = datetime.utcnow().year
    base_years_desc = list(range(current_year, 1999, -1))
    years_selected = st.multiselect("Year of output release", base_years_desc, key="years_selected")
    final_years_sorted_desc = sorted(set(years_selected), reverse=True)
    final_years_str = ",".join(str(y) for y in final_years_sorted_desc) if final_years_sorted_desc else ""

    output_desc = st.text_area("Short description of output", key="output_desc")
    output_contact = st.text_input("Name & institution of person responsible", key="output_contact")
    output_linkedin = st.text_input("LinkedIn address of contact", key="output_linkedin")
    project_url_for_output = st.text_input("Project URL (optional, if different)", key="project_url_for_output")

    submitted = st.form_submit_button("Submit for review (Output)")

    if submitted:
        if not submitter_email.strip():
            st.warning("Please provide the submitter email."); st.stop()
        if not output_title.strip():
            st.warning("Please provide the Output Name."); st.stop()
        if not output_countries:
            st.warning("Please select at least one country for geographic coverage."); st.stop()
        # CORREÇÃO: Validação do Data Type apenas quando for Dataset
        if output_type_sel == "Dataset" and (not output_data_type or output_data_type == SELECT_PLACEHOLDER):
            st.warning("Please select a Data type for Dataset outputs."); st.stop()
        if is_other_project and not (st.session_state.get("city_list_output") or st.session_state.get("new_project_countries")):
            st.warning("For a new project (Other), please add at least one country/city."); st.stop()

        # CORREÇÃO: Garante que Data Type fica vazio se não for Dataset
        if output_type_sel != "Dataset":
            output_data_type = ""

        # 1) Registrar projeto se for "Other"
        if is_other_project:
            wsP, errP = ws_projects()
            if errP or wsP is None:
                st.error(errP or "Worksheet unavailable for projects."); st.stop()
            
            countries_to_process = st.session_state.get("new_project_countries", [])
            cities_to_process = st.session_state.get("city_list_output", [])
            
            ok_allP, msg_anyP = True, None
            
            # Países sem cidades específicas
            for country in countries_to_process:
                latp, lonp = COUNTRY_CENTER_FULL.get(country, (None, None))
                rowP = {
                    "country": country, "city": "", "lat": latp, "lon": lonp,
                    "project_name": project_tax_other.strip(), "years": "",
                    "status": "", "data_types": "", "description": "",
                    "contact": new_project_contact, "access": "", "url": new_project_url,
                    "submitter_email": submitter_email,
                    "is_edit": "FALSE", "edit_target": "", "edit_request": "New project via output submission",
                    "approved": "FALSE",
                    "created_at": datetime.utcnow().isoformat(timespec="seconds")+"Z",
                }
                okP2, msgP2 = _append_row(wsP, PROJECTS_HEADERS, rowP)
                ok_allP &= okP2; msg_anyP = msgP2
            
            # Cidades com coordenadas específicas
            for pair in cities_to_process:
                if "—" not in pair: continue
                country, city = [p.strip() for p in pair.split("—",1)]
                # Usa coordenadas da cidade se disponível
                coords = st.session_state.city_coordinates.get(pair, (None, None))
                if coords[0] and coords[1]:
                    latp, lonp = coords
                else:
                    latp, lonp = COUNTRY_CENTER_FULL.get(country, (None, None))
                
                rowP = {
                    "country": country, "city": city, "lat": latp, "lon": lonp,
                    "project_name": project_tax_other.strip(), "years": "",
                    "status": "", "data_types": "", "description": "",
                    "contact": new_project_contact, "access": "", "url": new_project_url,
                    "submitter_email": submitter_email,
                    "is_edit": "FALSE", "edit_target": "", "edit_request": "New project via output submission",
                    "approved": "FALSE",
                    "created_at": datetime.utcnow().isoformat(timespec="seconds")+"Z",
                }
                okP2, msgP2 = _append_row(wsP, PROJECTS_HEADERS, rowP)
                ok_allP &= okP2; msg_anyP = msgP2
                
            if not ok_allP:
                st.error(f"⚠️ Project staging write error: {msg_anyP}"); st.stop()

        # 2) Gravar output
        wsO, errO = ws_outputs()
        if errO or wsO is None:
            st.error(errO or "Worksheet unavailable for outputs."); st.stop()

        # Define coordenadas - prioriza cidades
        lat_o, lon_o = (None, None)
        if not is_global and available_countries_for_cities:
            # Tenta usar coordenadas da primeira cidade
            if st.session_state.get("city_list_output"):
                first_city = st.session_state.city_list_output[0]
                coords = st.session_state.city_coordinates.get(first_city, (None, None))
                if coords[0] and coords[1]:
                    lat_o, lon_o = coords
            
            # Fallback para primeiro país
            if lat_o is None and available_countries_for_cities:
                first_country = available_countries_for_cities[0]
                if first_country in COUNTRY_CENTER_FULL:
                    lat_o, lon_o = COUNTRY_CENTER_FULL[first_country]

        output_cities_str = ", ".join(st.session_state.get("city_list_output", [])) if st.session_state.get("city_list_output") else ""
        output_countries_str = ", ".join(output_countries)

        rowO = {
            "project": (project_tax_other.strip() if is_other_project else project_tax_sel),
            "output_title": output_title,
            "output_type": ("" if output_type_sel.startswith("Other") else output_type_sel),
            "output_type_other": (output_type_other if output_type_sel.startswith("Other") else ""),
            "output_data_type": output_data_type,
            "output_url": output_url,
            "output_country": output_countries_str,
            "output_country_other": (output_country_other if "Other: ______" in output_countries else ""),
            "output_city": output_cities_str,
            "output_year": final_years_str,
            "output_desc": output_desc,
            "output_contact": output_contact,
            "output_email": "",
            "output_linkedin": output_linkedin,
            "project_url": (project_url_for_output or (st.session_state.get("new_project_url","") if is_other_project else "")),
            "submitter_email": submitter_email,
            "is_edit": "FALSE","edit_target":"","edit_request":"New submission",
            "approved": "FALSE",
            "created_at": datetime.utcnow().isoformat(timespec="seconds")+"Z",
            "lat": lat_o if lat_o is not None else "",
            "lon": lon_o if lon_o is not None else "",
        }
        okO2, msgO2 = _append_row(wsO, OUTPUTS_HEADERS, rowO)
        if okO2:
            st.success("✅ Output submission queued for review")
            _really_clear_output_form_state()
            st.session_state._pending_form_reset = True
            st.rerun()
        else:
            st.error(f"⚠️ {msgO2}")
