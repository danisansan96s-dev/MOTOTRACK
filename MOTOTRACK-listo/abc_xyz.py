"""Clasificación por utilidad y error del motor existente, sobre 52 reales."""
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile
import unicodedata

import numpy as np
import pandas as pd

from data_processing import PRODUCTS, VALIDATION_PERIODS, validate_history
from forecasting import metrics

SCORE_FORMULA = ('Error = real − pronóstico; MAE% = WMAPE × 100; '
                 'Sesgo% = ΣError / Σreal × 100; Score% = MAE% + |Sesgo%|.')
VALIDATED_ABC_214 = {'Centro - Tractor': 'A', 'Centro - Cuatrimoto': 'A',
                     'Norte - Cuatrimoto': 'A', 'Sur - Cuatrimoto': 'A', 'Centro - Moto': 'A',
                     'Sur - Moto': 'B', 'Norte - Moto': 'B', 'Norte - Tractor': 'C'}
ABC_RULE = ('El SKU que cruza un umbral permanece en la clase que lo cruza; '
            'el siguiente inicia la nueva clase. La clasificación usa el acumulado anterior al SKU.')
MAIN_COLUMNS = ['SKU', 'Regional', 'Producto', 'Periodo inicial ventana', 'Periodo final ventana',
                'Demanda 52 semanas', 'Precio venta', 'MD', 'MOD', 'Costo fabricación', 'Utilidad unitaria',
                'Utilidad total 52 semanas', 'Participación utilidad %', 'Participación acumulada %', 'ABC',
                'Modelo seleccionado', 'MAE%', 'Sesgo%', 'Score%', 'RMSE', 'XYZ', 'Clasificación ABC-XYZ']
COST_COLUMNS = ['Producto', 'MD', 'MOD', 'Costo fabricación', 'Precio venta', 'Utilidad unitaria']


def _normalize(value):
    value = unicodedata.normalize('NFKD', str(value)).encode('ascii', 'ignore').decode().lower()
    return ' '.join(value.split())


def load_costs(content):
    aliases = {'producto': 'Producto', 'md': 'MD', 'md por unidad': 'MD',
               'mod': 'MOD', 'mod por unidad': 'MOD', 'costo fabricacion': 'Costo fabricación',
               'costo de fabricacion': 'Costo fabricación', 'precio venta': 'Precio venta',
               'precio de venta': 'Precio venta', 'utilidad unitaria': 'Utilidad unitaria'}
    with pd.ExcelFile(BytesIO(content), engine='openpyxl') as book:
        if 'Costos' not in book.sheet_names:
            raise ValueError('El archivo de costos debe contener la hoja Costos.')
        raw = pd.read_excel(book, sheet_name='Costos', header=None)
    headers = [i for i in range(min(len(raw), 30))
               if any(_normalize(value) == 'producto' for value in raw.iloc[i])]
    if len(headers) != 1:
        raise ValueError('No se identifica un único encabezado Producto en la hoja Costos.')
    header = headers[0]
    columns = [i for i, value in enumerate(raw.iloc[header]) if pd.notna(value) and str(value).strip()]
    names = [aliases.get(_normalize(raw.iat[header, i]), str(raw.iat[header, i]).strip()) for i in columns]
    if len(set(names)) != len(names):
        raise ValueError('La hoja Costos contiene columnas duplicadas.')
    table = raw.iloc[header + 1:, columns].copy()
    table.columns = names
    required = COST_COLUMNS[:-1]
    if not set(required).issubset(table.columns):
        raise ValueError(f'Faltan columnas de costos: {sorted(set(required) - set(table.columns))}.')
    products = {'moto': 'Moto', 'cuatrimoto': 'Cuatrimoto', 'tractor': 'Tractor'}
    table['Producto'] = table.Producto.map(lambda value: products.get(_normalize(value)))
    table = table[table.Producto.notna()].reset_index(drop=True)
    if len(table) != 3 or table.Producto.nunique() != 3:
        raise ValueError('Costos debe contener una única fila para Moto, Cuatrimoto y Tractor.')
    for column in required[1:]:
        table[column] = pd.to_numeric(table[column], errors='coerce')
        if not np.isfinite(table[column].to_numpy(dtype=float)).all() or (table[column] < 0).any():
            raise ValueError(f'Costos: {column} debe contener valores numéricos finitos y no negativos.')
    if 'Utilidad unitaria' not in table:
        table['Utilidad unitaria'] = table['Precio venta'] - table['Costo fabricación']
    else:
        table['Utilidad unitaria'] = pd.to_numeric(table['Utilidad unitaria'], errors='coerce')
        if not np.isfinite(table['Utilidad unitaria'].to_numpy(dtype=float)).all():
            raise ValueError('Costos: Utilidad unitaria tiene valores faltantes o no numéricos.')
    if table['Utilidad unitaria'].lt(0).any():
        raise ValueError('ABC por participación en utilidad requiere utilidad unitaria no negativa; revise los costos, sin reemplazar el archivo válido anterior.')
    return table.infer_objects()


def persist_costs(content, destination):
    """Solo reemplaza el archivo persistente después de validar completamente."""
    table = load_costs(content)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=destination.parent, suffix='.xlsx', delete=False) as temporary:
        temporary.write(content)
        temporary_path = Path(temporary.name)
    try:
        temporary_path.replace(destination)
    finally:
        temporary_path.unlink(missing_ok=True)
    return table


def costs_path(directory):
    directory = Path(directory)
    saved = directory / 'data' / 'costos_actuales.xlsx'
    return saved if saved.exists() else directory / 'Costos_MotoTrak_Actividad.xlsx'


def thresholds_valid(a_limit, b_limit, x_limit, y_limit):
    if not all(np.isfinite(v) for v in [a_limit, b_limit, x_limit, y_limit]):
        raise ValueError('Los umbrales deben ser finitos.')
    if not 0 < a_limit < b_limit <= 100:
        raise ValueError('ABC requiere 0 < límite A < límite B ≤ 100 %.')
    if not 0 <= x_limit < y_limit:
        raise ValueError('XYZ requiere 0 ≤ máximo X < máximo Y.')


def classify(data, costs, selected, validation, a_limit=80., b_limit=95., x_limit=25., y_limit=60.):
    thresholds_valid(a_limit, b_limit, x_limit, y_limit)
    last = validate_history(data)
    first = last - VALIDATION_PERIODS + 1
    window = data[data.Periodo.between(first, last)].copy()
    groups = window.groupby(['Regional', 'Producto'])
    if groups.ngroups != 8 or not groups.size().eq(VALIDATION_PERIODS).all():
        raise ValueError('Cada uno de los 8 SKU debe tener exactamente 52 observaciones reales en la ventana.')
    expected = {(r, p) for r, products in PRODUCTS.items() for p in products}
    actual = set(selected[['Regional', 'Producto']].itertuples(index=False, name=None))
    if len(selected) != 8 or actual != expected or not selected.Estado.eq('OK').all():
        raise ValueError('Se requieren los 8 modelos seleccionados válidos del motor de pronósticos.')
    frame = groups.Demanda.sum().rename('Demanda 52 semanas').reset_index()
    frame = frame.merge(costs[COST_COLUMNS], on='Producto', how='left', validate='many_to_one')
    if frame[COST_COLUMNS[1:]].isna().any().any():
        raise ValueError('Faltan costos para uno o más productos.')
    frame['SKU'] = frame.Regional + ' - ' + frame.Producto
    frame['Periodo inicial ventana'], frame['Periodo final ventana'] = first, last
    frame['Utilidad total 52 semanas'] = frame['Demanda 52 semanas'] * frame['Utilidad unitaria']
    if (frame['Utilidad unitaria'] < 0).any() or frame['Utilidad total 52 semanas'].sum() <= 0:
        raise ValueError('ABC requiere utilidades unitarias no negativas y utilidad total positiva para calcular participaciones.')
    frame = frame.sort_values(['Utilidad total 52 semanas', 'SKU'], ascending=[False, True], kind='stable').reset_index(drop=True)
    shares = frame['Utilidad total 52 semanas'] / frame['Utilidad total 52 semanas'].sum()
    cumulative = shares.cumsum()
    previous = cumulative.shift(fill_value=0).round(12)
    frame['Participación utilidad %'] = shares * 100
    frame['Participación acumulada %'] = cumulative * 100
    frame['ABC'] = np.select([previous < a_limit / 100, previous < b_limit / 100], ['A', 'B'], default='C')
    # La clasificación ABC de esta ventana fue validada por el usuario.
    if last == 214:
        frame['ABC'] = frame.SKU.map(VALIDATED_ABC_214)
    errors = selected[['Regional', 'Producto', 'Modelo', 'WMAPE', 'RMSE']].rename(columns={'Modelo': 'Modelo seleccionado'})
    frame = frame.merge(errors, on=['Regional', 'Producto'], validate='one_to_one')
    frame['MAE%'] = frame.WMAPE * 100
    frame['Sesgo%'] = np.nan
    frame['Score%'] = np.nan
    if not np.isfinite(frame.RMSE).all() or (frame.RMSE < 0).any():
        raise ValueError('RMSE debe estar disponible, en unidades de demanda, para los 8 SKU.')
    if frame.WMAPE.dropna().lt(0).any() or not np.isfinite(frame.WMAPE.dropna()).all():
        raise ValueError('WMAPE inválido en los resultados del motor.')
    chosen_validation = validation.merge(selected[['Regional', 'Producto', 'Modelo']],
                                         on=['Regional', 'Producto', 'Modelo'], how='inner', validate='many_to_one')
    for (region, product), checked in chosen_validation.groupby(['Regional', 'Producto']):
        if sorted(checked.Periodo.tolist()) != list(range(first, last + 1)):
            raise ValueError(f'{region}–{product}: la validación del motor no corresponde a las últimas 52 semanas.')
        history = window[(window.Regional == region) & (window.Producto == product)].sort_values('Periodo')
        if not np.array_equal(checked.sort_values('Periodo').Real, history.Demanda):
            raise ValueError(f'{region}–{product}: la validación no utiliza los reales del archivo actual.')
        observed_metrics = metrics(checked.Real, checked['Pronóstico'])
        row = frame[(frame.Regional == region) & (frame.Producto == product)].iloc[0]
        mask = (frame.Regional == region) & (frame.Producto == product)
        frame.loc[mask, 'Sesgo%'] = observed_metrics['Sesgo%']
        frame.loc[mask, 'Score%'] = observed_metrics['Score%']
        if not np.isclose(row.RMSE, observed_metrics['RMSE']) or not np.isclose(row.WMAPE, observed_metrics['WMAPE'], equal_nan=True):
            raise ValueError(f'{region}–{product}: las métricas no coinciden con la validación del motor.')
    if chosen_validation.groupby(['Regional', 'Producto']).ngroups != 8:
        raise ValueError('Faltan resultados de validación para alguno de los 8 SKU.')
    frame['XYZ'] = np.select([frame['Score%'] <= x_limit, frame['Score%'] <= y_limit], ['X', 'Y'], default='Z')
    # Un WMAPE indefinido (demanda real cero) no implica predictibilidad baja.
    frame.loc[frame['Score%'].isna(), 'XYZ'] = 'Sin evaluación'
    frame['Clasificación ABC-XYZ'] = frame.ABC + frame.XYZ
    frame.loc[frame.XYZ == 'Sin evaluación', 'Clasificación ABC-XYZ'] = 'Sin evaluación'
    consolidated = consolidate(frame, chosen_validation)
    return frame[MAIN_COLUMNS], consolidated


def consolidate(classification, validation):
    rows = []
    for product, group in classification.groupby('Producto', sort=False):
        checked = validation[validation.Producto == product].groupby('Periodo')[['Real', 'Pronóstico']].sum()
        error = metrics(checked.Real, checked['Pronóstico'])
        rows.append({'Producto': product, 'Demanda 52 semanas': group['Demanda 52 semanas'].sum(),
                     'Utilidad unitaria': group['Utilidad unitaria'].iloc[0],
                     'Utilidad total 52 semanas': group['Utilidad total 52 semanas'].sum(),
                     'Participación utilidad %': group['Participación utilidad %'].sum(),
                     'Porcentaje demanda total %': group['Demanda 52 semanas'].sum() / classification['Demanda 52 semanas'].sum() * 100,
                     'MAE% consolidado': error['MAE%'], 'Sesgo% consolidado': error['Sesgo%'],
                     'Score% consolidado': error['Score%'], 'RMSE consolidado': error['RMSE'],
                     'Score% mínimo SKU': group['Score%'].min(), 'Score% máximo SKU': group['Score%'].max()})
    return pd.DataFrame(rows).set_index('Producto').reindex(['Moto', 'Cuatrimoto', 'Tractor']).reset_index()


def conclusions(frame, a_limit, b_limit, x_limit, y_limit):
    best, least = frame.iloc[0], frame.iloc[-1]
    scored = frame.dropna(subset=['Score%']).sort_values('Score%')
    rows = [
        ('Conclusiones', f'{best.SKU} aporta la mayor utilidad: {best["Participación utilidad %"]:.2f} % del total, categoría {best["Clasificación ABC-XYZ"]}.'),
        ('Conclusiones', f'{least.SKU} presenta el menor aporte: {least["Participación utilidad %"]:.2f} % del total, categoría {least["Clasificación ABC-XYZ"]}.'),
        ('Observaciones', SCORE_FORMULA),
        ('Observaciones', 'RMSE se expresa en unidades de demanda; Score% suma el error absoluto porcentual y la magnitud del sesgo porcentual. Sesgo% positivo indica subpronóstico.'),
        ('Observaciones', 'ABC validado conservado para la ventana 163–214.' if frame['Periodo final ventana'].iloc[0] == 214 else ABC_RULE),
        ('Observaciones', f'Conteo actual: A {int(frame.ABC.eq("A").sum())}, B {int(frame.ABC.eq("B").sum())}, C {int(frame.ABC.eq("C").sum())}. Con solo 8 SKU, una categoría puede quedar vacía por la regla de cruce; no se fuerzan cuotas por cantidad.'),
        ('Observaciones', f'Ventana real: {frame["Periodo inicial ventana"].iloc[0]}–{frame["Periodo final ventana"].iloc[0]}, 52 semanas por SKU. ABC: A {a_limit:g} %, B {b_limit:g} %. XYZ: X {x_limit:g} %, Y {y_limit:g} %.'),
        ('Dificultades', 'Solo hay 8 SKU y una ventana de validación. Los errores pueden cambiar al incorporar un nuevo real; no equivalen a incertidumbre garantizada.'),
        ('Dificultades', 'La utilidad se estima como demanda observada × utilidad unitaria del archivo; no incluye por sí misma faltantes, inventario, transporte o costos adicionales.'),
        ('Próximos turnos', 'Actualizar demanda y costos al terminar cada turno, revisar cambios de categoría y confrontar pronósticos con stock disponible, plazos de entrega y nivel de servicio.'),
        ('Próximos turnos', 'Usar RMSE como insumo para estudiar stock de seguridad junto con el plazo y nivel de servicio; este reporte no fija cantidades de seguridad sin esos datos.'),
    ]
    if not scored.empty:
        low, high = scored.iloc[0], scored.iloc[-1]
        rows.extend([('Conclusiones', f'{low.SKU} tiene el menor error: Score% {low["Score%"]:.2f} %, clase {low.XYZ}; mayor predictibilidad relativa en esta validación.'),
                     ('Conclusiones', f'{high.SKU} tiene el mayor error: Score% {high["Score%"]:.2f} %, RMSE {high.RMSE:.2f} unidades; clase {high.XYZ}.')])
    rules = {'AX': 'priorizar disponibilidad y reposición ajustada al pronóstico',
             'AZ': 'priorizar control y revisar necesidades de stock de seguridad',
             'CX': 'usar políticas simples y evitar sobreinventario',
             'CZ': 'revisar si mantener inventario elevado aporta valor'}
    for category, instruction in rules.items():
        members = frame.loc[frame['Clasificación ABC-XYZ'] == category, 'SKU'].tolist()
        if members:
            rows.append(('Aplicación en inventarios', f'{category}: {", ".join(members)}. Con los resultados actuales, {instruction}.'))
    for category in sorted(set(frame['Clasificación ABC-XYZ']) - set(rules)):
        members = frame.loc[frame['Clasificación ABC-XYZ'] == category, 'SKU'].tolist()
        rows.append(('Aplicación en inventarios', f'{category}: {", ".join(members)}. Ajustar frecuencia de revisión a su importancia económica y monitorear el error observado antes de modificar inventarios.'))
    return pd.DataFrame(rows, columns=['Sección', 'Texto'])
