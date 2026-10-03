import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
from plotly.subplots import make_subplots


def format_number(value, decimals=0):
    """Formato de presentación español; no modifica los valores numéricos."""
    return f'{value:,.{decimals}f}'.translate(str.maketrans({',': '.', '.': ','}))


def format_percentage(value):
    return '—' if pd.isna(value) else f'{format_number(value * 100, 2)} %'


def model_table(frame):
    return frame.style.format({'WMAPE': format_percentage, 'MAPE': format_percentage}, na_rep='—')


def horizon_summary(consolidated):
    """Totales de todas las regionales para el horizonte que se muestra."""
    totals = consolidated[['Moto', 'Cuatrimoto', 'Tractor', 'Total']].sum()
    return pd.DataFrame({
        'Producto': ['Moto', 'Cuatrimoto', 'Tractor', 'Total general'],
        'Total pronosticado': totals.to_numpy(),
        'Promedio por periodo': (totals / len(consolidated)).to_numpy(),
    })


def regional_summary(detail):
    """Agrupa únicamente las combinaciones presentes en el pronóstico."""
    return detail.groupby(['Regional', 'Producto'], as_index=False).agg(**{
        'Total pronosticado': ('Pronóstico', 'sum'),
        'Promedio por periodo': ('Pronóstico', 'mean'),
    })


def summary_table(frame):
    return frame.style.format({
        'Total pronosticado': format_number,
        'Promedio por periodo': lambda value: format_number(value, 2),
    })


def history_forecast(history, future):
    fig = go.Figure()
    fig.add_scatter(x=history.Periodo,y=history.Demanda,name='Histórico',line={'color':'#2563eb'})
    if future is not None:
        fig.add_scatter(x=future.Periodo,y=future['Pronóstico'],name='Pronóstico',mode='lines+markers',line={'color':'#14b8a6','dash':'dash'})
        if not future.empty:
            fig.add_vline(x=history.Periodo.max() + .5,line_dash='dot',annotation_text='Inicio del pronóstico')
    fig.update_layout(xaxis_title='Periodo semanal',yaxis_title='Unidades',legend={'orientation':'h'},margin={'t':35})
    return fig


def abc_table(frame):
    money = ['MD', 'MOD', 'Precio venta', 'Costo fabricación', 'Utilidad unitaria', 'Utilidad total 52 semanas']
    formats = {column: lambda value: f'$ {format_number(value, 2)}' for column in money if column in frame}
    formats.update({column: lambda value: f'{format_number(value, 2)} %' for column in frame if '%' in column})
    formats.update({column: lambda value: format_number(value, 2) for column in frame if 'RMSE' in column})
    return frame.style.format(formats, na_rep='Sin evaluación')


def pareto_abc(frame, a_limit, b_limit):
    figure = make_subplots(specs=[[{'secondary_y': True}]])
    figure.add_trace(go.Bar(x=frame.SKU, y=frame['Utilidad total 52 semanas'], name='Utilidad',
                           marker_color=frame.ABC.map({'A': '#2563eb', 'B': '#f59e0b', 'C': '#94a3b8'})), secondary_y=False)
    figure.add_trace(go.Scatter(x=frame.SKU, y=frame['Participación acumulada %'], mode='lines+markers',
                               name='Utilidad acumulada', line={'color': '#14b8a6', 'width': 3}), secondary_y=True)
    for limit, name in [(a_limit, 'Límite A'), (b_limit, 'Límite B')]:
        figure.add_hline(y=limit, line_dash='dot', line_color='#64748b', secondary_y=True,
                        annotation_text=f'{name}: {limit:g} %')
    figure.update_yaxes(title_text='Utilidad total ($)', secondary_y=False)
    figure.update_yaxes(title_text='Acumulado (%)', range=[0, 105], ticksuffix=' %', secondary_y=True)
    figure.update_layout(title='Pareto ABC · utilidad de las últimas 52 semanas', template='plotly_white',
                         legend={'orientation': 'h', 'y': 1.15}, margin={'b': 120}, separators=',.')
    return figure


def abc_xyz_matrix(frame):
    cells, counts = [], []
    for abc in ['A', 'B', 'C']:
        labels, numbers = [], []
        for xyz in ['X', 'Y', 'Z']:
            members = frame[(frame.ABC == abc) & (frame.XYZ == xyz)].SKU.tolist()
            labels.append('<br>'.join(members) if members else '—')
            numbers.append(len(members))
        cells.append(labels)
        counts.append(numbers)
    figure = go.Figure(go.Heatmap(x=['X · alta', 'Y · media', 'Z · baja'], y=['A', 'B', 'C'],
                                 z=counts, text=cells, texttemplate='%{text}', colorscale='Blues',
                                 showscale=False, hovertemplate='ABC: %{y}<br>Predictibilidad: %{x}<br>%{text}<extra></extra>'))
    figure.update_layout(title='Matriz ABC-XYZ · ubicación de cada SKU', height=460, template='plotly_white',
                         xaxis_title='Predictibilidad por error del pronóstico', yaxis_title='Importancia económica',
                         yaxis={'autorange': 'reversed'}, margin={'t': 70})
    return figure


def profit_chart(frame):
    return px.bar(frame, x='Producto', y='Utilidad total 52 semanas', color='Regional', barmode='group',
                  title='Utilidad por producto y regional · últimas 52 semanas', template='plotly_white',
                  labels={'Utilidad total 52 semanas': 'Utilidad total ($)'})


def regional_chart(detail, product):
    return px.line(detail[detail.Producto == product],x='Periodo',y='Pronóstico',color='Regional',markers=True,title=f'{product}: pronóstico por regional')


def consolidated_chart(consolidated):
    return px.line(consolidated.melt(id_vars='Periodo',value_vars=['Moto','Cuatrimoto','Tractor'],var_name='Producto',value_name='Unidades'),x='Periodo',y='Unidades',color='Producto',markers=True,title='Pronóstico consolidado por producto')


def comparison_chart(comparison):
    fig = px.bar(comparison[comparison.Estado == 'OK'].sort_values('WMAPE'),x='WMAPE',y='Modelo',orientation='h',color='Modelo')
    fig.update_traces(hovertemplate='%{y}<br>WMAPE: %{x:.2%}<extra></extra>')
    fig.update_layout(showlegend=False,xaxis_tickformat='.2%',yaxis={'autorange':'reversed'},xaxis_title='WMAPE (menor es mejor)',separators=',.')
    return fig
