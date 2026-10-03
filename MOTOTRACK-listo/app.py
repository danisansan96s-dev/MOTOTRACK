"""Pronosticador MotoTrak basado en los ocho históricos del archivo real."""
from pathlib import Path

import streamlit as st

from data_processing import load_data, analyze, FINAL_PERIOD, VALIDATION_PERIODS, default_workbook_path
from forecasting import evaluate, forecast
from visualizations import (history_forecast, comparison_chart, consolidated_chart, regional_chart,
                            horizon_summary, regional_summary, summary_table, model_table, format_number)
from export import export_excel
from abc_ui import render_classification

st.set_page_config(page_title='MotoTrak | Pronosticador', page_icon='🏍️', layout='wide')


@st.cache_data(show_spinner=False)
def read_history(content):
    return load_data(content)


@st.cache_data(show_spinner=False)
def prepare(content):
    data, settings = read_history(content)
    comparison, selected, validation = evaluate(data)
    return data, settings, analyze(data), comparison, selected, validation


def main():
    st.title('MotoTrak · Pronosticador de demanda')
    uploaded = st.sidebar.file_uploader('Archivo MotoTrak', type=['xlsx'])
    root = Path(__file__).parent
    uploaded_costs = st.sidebar.file_uploader('Archivo de costos · hoja Costos', type=['xlsx'], key='costs_upload')
    save_costs = st.sidebar.checkbox('Recordar costos entre sesiones locales',
                                     value=False, key='persist_local_costs')
    default_path = default_workbook_path(root)
    if default_path.is_file():
        st.sidebar.caption(f'Archivo local: {default_path.name}. Puede cargar una exportación más reciente.')
    if uploaded is None and not default_path.is_file():
        st.info('Carga un archivo de demanda MotoTrak en la barra lateral para comenzar. '
                'También puedes cargar el archivo de costos para la clasificación ABC-XYZ.')
        return
    try:
        content = uploaded.getvalue() if uploaded else default_path.read_bytes()
        data, settings = read_history(content)
    except Exception as exc:
        st.error(f'No se pudo procesar el archivo: {exc}')
        st.stop()
    last_period = int(data.Periodo.max())
    history_count = data.Periodo.nunique()
    remaining = max(0, FINAL_PERIOD - last_period)
    series_count = data.groupby(['Regional', 'Producto']).ngroups
    next_text = f'próximo periodo a pronosticar: {last_period + 1}' if remaining else 'simulación finalizada'
    st.caption(f'{history_count} periodos históricos · {series_count} series · {next_text}')
    if remaining == 0:
        st.metric('Periodos históricos', history_count)
        st.metric('Series', series_count)
        st.info(f'La simulación finalizó en el periodo {FINAL_PERIOD}. No quedan periodos por pronosticar.')
        st.dataframe(analyze(data), hide_index=True)
        st.dataframe(data, hide_index=True)
        _, _, _, _, selected, validation = prepare(content)
        abc_tab, product_tab, conclusions_tab = st.tabs(['ABC-XYZ', 'Consolidado', 'Conclusiones'])
        render_classification(data, selected, validation, abc_tab, product_tab, conclusions_tab,
                              uploaded_costs=uploaded_costs, save_costs=save_costs)
        return
    if remaining == 1:
        # Streamlit no admite sliders con mínimo y máximo iguales.
        horizon = 1
        st.sidebar.slider('Periodos futuros', 0, 1, 1, disabled=True)
        st.sidebar.caption('Queda 1 periodo: 226.')
    else:
        horizon = st.sidebar.slider('Periodos futuros', 1, remaining, remaining, key=f'horizon_{last_period}')
    try:
        with st.spinner('Leyendo los históricos y comparando modelos…'):
            data, settings, summary, comparison, selected, validation = prepare(content)
            detail, consolidated, notes = forecast(data, selected, horizon)
    except Exception as exc:
        st.error(f'No se pudo procesar el archivo: {exc}')
        st.stop()
    columns = st.columns(3)
    columns[0].metric('Periodos históricos', data.Periodo.nunique())
    columns[1].metric('Series', len(selected))
    columns[2].metric('Unidades futuras', f'{int(consolidated.Total.sum()):,}')
    st.download_button('Descargar pronóstico en Excel', export_excel(detail, consolidated, selected, comparison),
                       file_name='MotoTrak_pronostico.xlsx',
                       mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    history_tab, models_tab, future_tab, abc_tab, product_tab, conclusions_tab = st.tabs(
        ['Histórico', 'Validación de modelos', 'Pronóstico', 'ABC-XYZ', 'Consolidado', 'Conclusiones'])
    region = st.sidebar.selectbox('Regional', sorted(data.Regional.unique()))
    product = st.sidebar.selectbox('Producto', sorted(data.loc[data.Regional == region, 'Producto'].unique()))
    mask = (data.Regional == region) & (data.Producto == product)
    future_mask = (detail.Regional == region) & (detail.Producto == product)
    model_mask = (comparison.Regional == region) & (comparison.Producto == product)
    with history_tab:
        st.subheader(f'{region} · {product}')
        st.plotly_chart(history_forecast(data[mask], detail[future_mask]), width='stretch')
        st.dataframe(summary, hide_index=True)
        st.dataframe(data[mask], hide_index=True)
    with models_tab:
        train_end = last_period - VALIDATION_PERIODS
        st.write(f'Cada modelo se entrena con los periodos 1–{train_end} y predice los periodos '
                 f'{train_end + 1}–{last_period} sin acceder a esos valores. '
                 'Se elige el menor WMAPE; entre modelos a menos de 0,5 puntos porcentuales del mínimo, '
                 f'se prioriza RMSE y luego el sesgo absoluto. Finalmente se reentrena con los {history_count} periodos reales.')
        st.dataframe(model_table(selected), hide_index=True, key='selected_models')
        st.plotly_chart(comparison_chart(comparison[model_mask]), width='stretch')
        st.dataframe(model_table(comparison[model_mask]), hide_index=True, key='model_comparison')
        winner = selected.loc[(selected.Regional == region) & (selected.Producto == product), 'Modelo'].iloc[0]
        check = validation[(validation.Regional == region) & (validation.Producto == product) & (validation.Modelo == winner)]
        st.subheader(f'Validación temporal · {winner}')
        st.line_chart(check.set_index('Periodo')[['Real', 'Pronóstico']])
        errors = comparison[comparison.Estado != 'OK']
        if not errors.empty:
            st.warning('Los modelos con errores de ajuste se excluyen de la selección.')
            st.dataframe(model_table(errors), hide_index=True)
    with future_tab:
        st.caption('Predicciones limitadas a cero y redondeadas a unidades enteras. Sur tiene Moto y Cuatrimoto.')
        totals = horizon_summary(consolidated)
        st.subheader('Resumen del horizonte de pronóstico')
        st.caption(f'{horizon} periodos seleccionados · {int(consolidated.Periodo.min())}–{int(consolidated.Periodo.max())} '
                   '· Totales de todas las regionales y productos.')
        total_columns = st.columns(4)
        labels = ['Total de Motos', 'Total de Cuatrimotos', 'Total de Tractores', 'Total general']
        for column, label, total in zip(total_columns, labels, totals['Total pronosticado']):
            column.metric(label, format_number(total))
        average_columns = st.columns(3)
        for column, row in zip(average_columns, totals.iloc[:3].itertuples(index=False, name=None)):
            column.metric(f'{row[0]} · Promedio por periodo', format_number(row[2], 2))
        st.dataframe(summary_table(totals), hide_index=True, key='horizon_summary')
        st.subheader('Resumen por Regional y Producto')
        st.caption('Unidades acumuladas durante todo el horizonte seleccionado.')
        st.dataframe(summary_table(regional_summary(detail)), hide_index=True, key='regional_summary')
        st.plotly_chart(consolidated_chart(consolidated), width='stretch')
        st.plotly_chart(regional_chart(detail, product), width='stretch')
        st.subheader('Consolidado por periodo · todas las regionales')
        st.dataframe(consolidated, hide_index=True)
        st.subheader(f'Pronóstico detallado · {region}–{product}')
        st.dataframe(detail[future_mask], hide_index=True, key='forecast_detail_filtered')
        with st.expander('Pronóstico detallado de todas las series'):
            st.dataframe(detail, hide_index=True, key='forecast_detail_all')
        if notes.Aviso.str.len().gt(0).any():
            with st.expander('Avisos de ajuste'):
                st.dataframe(notes[notes.Aviso.str.len().gt(0)], hide_index=True)
    render_classification(data, selected, validation, abc_tab, product_tab, conclusions_tab,
                          uploaded_costs=uploaded_costs, save_costs=save_costs)


if __name__ == '__main__':
    main()
