# ──────────────────────────────────────────────────────────────────────────────
# 9) SUBMISSÃO DE OUTPUT (Global permite cidades + botão 🔄 no seletor de país)
# ──────────────────────────────────────────────────────────────────────────────
st.markdown("---")
st.header("Submit Output (goes to review queue)")

# ===== Estado/Init =====
ss = st.session_state
if "city_list_output" not in ss:
    ss.city_list_output = []
if "_clear_city_field_out" not in ss:
    ss._clear_city_field_out = False
if "output_countries" not in ss:
    ss.output_countries = []
# versão do seletor de país das cidades (para forçar refresh das opções)
if "_country_for_city_version" not in ss:
    ss._country_for_city_version = 0

def add_city(country, city_csv):
    """Adiciona 1+ cidades (separadas por vírgula) para um país."""
    if country and country != SELECT_PLACEHOLDER and city_csv.strip():
        for c in [x.strip() for x in city_csv.split(",") if x.strip()]:
            pair = f"{country} — {c}"
            if pair not in ss.city_list_output:
                ss.city_list_output.append(pair)
        return True
    return False

def remove_city(i):
    if 0 <= i < len(ss.city_list_output):
        ss.city_list_output.pop(i)

def clear_form():
    ss.city_list_output = []
    ss._clear_city_field_out = False
    ss.output_countries = []
    ss._country_for_city_version = 0

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
    project_tax_other = st.text_input("Please specify the project (taxonomy)*") if is_other_project else ""

    output_type_sel = st.selectbox("Output Type*", options=OUTPUT_TYPES)

    # >>> Data type travado e salvo em branco quando Output Type = Dataset
    if output_type_sel == "Dataset":
        st.selectbox(
            "Data type (for datasets) — locked for Dataset",
            options=[SELECT_PLACEHOLDER] + DATASET_DTYPES,
            index=0,
            disabled=True,
            key="output_data_type_locked"
        )
        output_data_type = ""  # gravar em branco quando travado
    else:
        output_data_type = st.selectbox(
            "Data type (for datasets)",
            options=[""] + DATASET_DTYPES
        )

    output_type_other = st.text_input("Please specify the output type*") if output_type_sel.startswith("Other") else ""
    output_title = st.text_input("Output Name*")
    output_url   = st.text_input("Output URL (optional)")

    # ── Geographic Coverage (logo após Output URL) ──
    st.subheader("Geographic Coverage")
    st.multiselect(
        "Select countries (select 'Global' for worldwide coverage)*",
        options=_countries_with_global_first(COUNTRY_NAMES) + ["Other: ______"],
        key="output_countries"
    )
    selected_countries = ss.output_countries[:]
    is_global = "Global" in selected_countries

    output_country_other = ""
    if "Other: ______" in selected_countries:
        output_country_other = st.text_input("Please specify other geographic coverage")

    # ── Cidades (sempre disponível; mesmo com Global) ──
    st.write("**Add cities for this output (optional):**")

    # Países disponíveis para vincular cidades:
    # 1) Se houver países reais selecionados → use-os (ignora Global/Other)
    # 2) Se só Global estiver selecionado (ou nenhum) → ofereça TODOS os países
    selected_real = [c for c in selected_countries if c not in ["Global", "Other: ______"]]
    if selected_real:
        city_country_choices = [SELECT_PLACEHOLDER] + selected_real
    else:
        city_country_choices = [SELECT_PLACEHOLDER] + COUNTRY_NAMES

    # Linha com seletor de país + botão de refresh 🔄 ao lado
    col_country, col_refresh = st.columns([5, 1])
    with col_country:
        city_country_select = st.selectbox(
            "Country for the city",
            options=city_country_choices,
            key=f"city_country_select_v{ss._country_for_city_version}"
        )
    with col_refresh:
        st.write("")  # alinhamento
        if st.form_submit_button("🔄", help="Refresh countries for city selector", use_container_width=True, key="refresh_cities_country"):
            # Atualiza a versão do seletor para forçar o Streamlit a reconstruir as opções
            ss._country_for_city_version += 1
            st.rerun()

    # Campo de cidades
    c2, c3 = st.columns([5, 1])
    with c2:
        if ss._clear_city_field_out and "output_city_input" in ss:
            del ss["output_city_input"]
            ss._clear_city_field_out = False
        output_city_input = st.text_input(
            "City name (you can paste multiple, comma-separated)",
            placeholder="e.g., Lagos, Kano",
            key="output_city_input"
        )
    with c3:
        st.write("")
        if st.form_submit_button("➕ Add City", use_container_width=True, key="add_city_btn"):
            if add_city(city_country_select, output_city_input):
                ss._clear_city_field_out = True
                st.rerun()

    # Lista de cidades
    if ss.city_list_output:
        st.write("**Added cities:**")
        for i, pair in enumerate(ss.city_list_output):
            c1, c2 = st.columns([6,1])
            with c1:
                st.write(f"📍 {pair}")
            with c2:
                if st.form_submit_button("🗑️ Remove", key=f"rm_city_{i}"):
                    remove_city(i); st.rerun()

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
# Processamento do envio (Global com cidades permitido)
# ──────────────────────────────────────────────────────────────────────────────
if submitted:
    errors = []
    if not submitter_email.strip():
        errors.append("❌ Submitter email is required")
    if not output_title.strip():
        errors.append("❌ Output name is required")
    if not ss.output_countries:
        errors.append("❌ At least one country must be selected (Global counts)")
    if is_other_project and not project_tax_other.strip():
        errors.append("❌ Project name is required when selecting 'Other'")

    if errors:
        for e in errors: st.error(e)
        st.stop()

    try:
        wsO, errO = ws_outputs()
        if errO or wsO is None:
            st.error(errO or "Worksheet unavailable for outputs."); st.stop()

        # anos
        final_years_sorted_desc = sorted(set(years_selected), reverse=True)
        final_years_str = ",".join(str(y) for y in final_years_sorted_desc) if final_years_sorted_desc else ""

        def save_row(country, city, lat, lon):
            rowO = {
                "project": (project_tax_other.strip() if is_other_project else project_tax_sel),
                "output_title": output_title,
                "output_type": ("" if output_type_sel.startswith("Other") else output_type_sel),
                "output_type_other": (output_type_other if output_type_sel.startswith("Other") else ""),
                "output_data_type": output_data_type,  # "" se Dataset (travado)
                "output_url": output_url,
                "output_country": country,
                "output_country_other": (output_country_other if "Other: ______" in ss.output_countries else ""),
                "output_city": city or "",
                "output_year": final_years_str,
                "output_desc": output_desc,
                "output_contact": output_contact,
                "output_email": "",
                "output_linkedin": output_linkedin,
                "project_url": project_url_for_output or "",
                "submitter_email": submitter_email,
                "is_edit": "FALSE","edit_target":"","edit_request":"New submission",
                "approved": "FALSE",
                "created_at": datetime.utcnow().isoformat(timespec="seconds")+"Z",
                "lat": lat if lat is not None else "",
                "lon": lon if lon is not None else "",
            }
            ok, msg = _append_row(wsO, OUTPUTS_HEADERS, rowO)
            if not ok:
                st.error(f"⚠️ {msg}"); st.stop()

        # Lógica de gravação:
        selected_countries = ss.output_countries[:]
        has_cities = len(ss.city_list_output) > 0

        if "Global" in selected_countries:
            if has_cities:
                # Global + cidades → grava por cidade (sem linha extra Global)
                for pair in ss.city_list_output:
                    if "—" not in pair: continue
                    ctry, cty = [p.strip() for p in pair.split("—", 1)]
                    if ctry == "Other: ______": continue
                    lat_o, lon_o = COUNTRY_CENTER_FULL.get(ctry, (None, None))
                    save_row(ctry, cty, lat_o, lon_o)
            else:
                # Só Global, sem cidades → uma linha Global
                save_row("Global", "", None, None)
        else:
            # Sem Global → se houver cidades, grava por cidade; senão, por país
            if has_cities:
                for pair in ss.city_list_output:
                    if "—" not in pair: continue
                    ctry, cty = [p.strip() for p in pair.split("—", 1)]
                    if ctry in ["Global", "Other: ______"]: continue
                    lat_o, lon_o = COUNTRY_CENTER_FULL.get(ctry, (None, None))
                    save_row(ctry, cty, lat_o, lon_o)
            else:
                for ctry in [c for c in selected_countries if c not in ["Global","Other: ______"]]:
                    lat_o, lon_o = COUNTRY_CENTER_FULL.get(ctry, (None, None))
                    save_row(ctry, "", lat_o, lon_o)

        st.success("✅ Output submission queued for review!")
        clear_form()
        st.rerun()

    except Exception as e:
        st.error(f"An error occurred: {e}")
