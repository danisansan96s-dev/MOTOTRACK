"""Pestañas ABC-XYZ integradas en el pronosticador existente."""
from pathlib import Path

import streamlit as st

from abc_xyz import (load_costs, persist_costs, costs_path, classify, conclusions, SCORE_FORMULA, ABC_RULE)
from export import export_abc_excel
from visualizations import abc_table, pareto_abc, abc_xyz_matrix, profit_chart, format_number


def render_classification(data, selected, validation, abc_tab, consolidated_tab, conclusions_tab,
                          uploaded_costs=None, save_costs=False):
    root = Path(__file__).parent
    st.sidebar.divider()
    st.sidebar.subheader('Clasificación ABC-XYZ')
    uploaded = uploaded_costs
    a_limit = st.sidebar.number_input('Límite A (%)', min_value=0.1, max_value=99.9, value=80., step=1., key='abc_limit_a')
    b_limit = st.sidebar.number_input('Límite B (%)', min_value=0.2, max_value=100., value=95., step=1., key='abc_limit_b')
    x_limit = st.sidebar.number_input('Máximo Score% para X', min_value=0., value=15., step=1., key='xyz_limit_x')
    y_limit = st.sidebar.number_input('Máximo Score% para Y', min_value=0., value=30., step=1., key='xyz_limit_y')
    parameters = {'Límite A (%)': a_limit, 'Límite B (%)': b_limit,
                  'Máximo Score% X': x_limit, 'Máximo Score% Y': y_limit}
    destination = root / 'data' / 'costos_actuales.xlsx'
    try:
        if uploaded is not None:
            content = uploaded.getvalue()
            costs = load_costs(content)
            st.session_state['validated_costs_content'] = content
            source_name = 'Archivo de costos cargado'
        elif 'validated_costs_content' in st.session_state:
            content = st.session_state['validated_costs_content']
            costs = load_costs(content)
            source_name = 'Últimos costos válidos de esta sesión'
        else:
            source = costs_path(root)
            if not source.is_file():
                with abc_tab:
                    st.info('Carga un Excel con la hoja Costos en la barra lateral para habilitar ABC-XYZ.')
                with consolidated_tab:
                    st.info('Carga los costos para obtener el consolidado ABC-XYZ.')
                with conclusions_tab:
                    st.info('Carga los costos para generar las conclusiones.')
                return
            content = source.read_bytes()
            costs = load_costs(content)
            source_name = source.name
        if save_costs:
            try:
                if not destination.exists() or destination.read_bytes() != content:
                    persist_costs(content, destination)
            except OSError:
                st.sidebar.caption('Los costos válidos se usan en esta sesión; no se pudo guardar una copia local.')
        table, consolidated = classify(data, costs, selected, validation, a_limit, b_limit, x_limit, y_limit)
        draft = conclusions(table, a_limit, b_limit, x_limit, y_limit)
    except Exception as exc:
        with abc_tab:
            st.error(f'Clasificación ABC-XYZ: {exc}')
            st.caption('Corrija los costos o parámetros para completar la clasificación. El pronosticador sigue disponible.')
        with consolidated_tab:
            st.info('El consolidado ABC-XYZ estará disponible cuando los costos y parámetros sean válidos.')
        with conclusions_tab:
            st.info('Las conclusiones necesitan una clasificación ABC-XYZ válida.')
        return
    with abc_tab:
        st.subheader('Clasificación ABC-XYZ')
        first, last = table['Periodo inicial ventana'].iloc[0], table['Periodo final ventana'].iloc[0]
        st.caption(f'8 SKU · 52 semanas reales por SKU · ventana {first}–{last} · costos: {source_name}')
        counts = st.columns(4)
        counts[0].metric('Utilidad total · 52 semanas', '$ ' + format_number(table['Utilidad total 52 semanas'].sum()))
        for column, label in zip(counts[1:], ['A', 'B', 'C']):
            column.metric(f'SKU {label}', int(table.ABC.eq(label).sum()))
        for column, label in zip(st.columns(3), ['X', 'Y', 'Z']):
            column.metric(f'SKU {label}', int(table.XYZ.eq(label).sum()))
        st.write(SCORE_FORMULA)
        st.caption(f'XYZ reutiliza la validación del motor sobre los periodos {first}–{last}; '
                   f'los modelos se entrenan con los anteriores (1–{first - 1}). '
                   'RMSE se conserva en unidades de demanda. No se utiliza CV ni CV² para XYZ.')
        st.caption(ABC_RULE)
        if table['Score%'].isna().any():
            st.warning('Algunos SKU tienen demanda real cero: WMAPE no está definido y XYZ se muestra como Sin evaluación.')
        st.download_button('Descargar reporte ABC-XYZ en Excel',
                           export_abc_excel(costs, table, consolidated, draft, parameters),
                           file_name='MotoTrak_ABC_XYZ.xlsx',
                           mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', key='abc_download')
        st.dataframe(abc_table(table), hide_index=True, key='abc_classification')
        st.plotly_chart(pareto_abc(table, a_limit, b_limit), width='stretch', key='abc_pareto')
        st.plotly_chart(abc_xyz_matrix(table), width='stretch', key='abc_matrix')
        with st.expander('Costos utilizados'):
            st.dataframe(abc_table(costs), hide_index=True)
            st.caption('Los valores presentes en el Excel se conservan. Solo si falta Utilidad unitaria se utiliza precio − costo.')
    with consolidated_tab:
        st.subheader('Consolidado por producto · últimas 52 semanas')
        st.caption('Moto y Cuatrimoto: Norte + Centro + Sur. Tractor: Norte + Centro. '
                   'Este análisis es informativo; la clasificación sigue correspondiendo a los 8 SKU.')
        st.dataframe(abc_table(consolidated), hide_index=True, key='abc_product_consolidated')
        st.plotly_chart(profit_chart(table), width='stretch', key='abc_profit')
        st.caption('Score% y RMSE consolidados se calculan comparando la suma de los pronósticos seleccionados '
                   'con la suma de los reales de las mismas 52 semanas. No son promedios de RMSE. '
                   'El rango de Score% por SKU permite observar diferencias entre regionales.')
    with conclusions_tab:
        st.subheader('Borrador de conclusiones')
        st.caption('Interpretación de los datos y parámetros actuales; revisar antes de entregar la actividad.')
        for section, group in draft.groupby('Sección', sort=False):
            st.markdown(f'**{section}**')
            for value in group.Texto:
                st.write(value)
