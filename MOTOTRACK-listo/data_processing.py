"""Lectura estricta de los bloques reales de MotoTrak; nunca imputa demanda."""
from io import BytesIO
from pathlib import Path
import numpy as np
import pandas as pd

PRODUCTS = {'Norte': ['Moto', 'Cuatrimoto', 'Tractor'], 'Centro': ['Moto', 'Cuatrimoto', 'Tractor'], 'Sur': ['Moto', 'Cuatrimoto']}
MIN_HISTORY = 208
FINAL_PERIOD = 226
VALIDATION_PERIODS = 52


def default_workbook_path(directory):
    """Fuente actual explícita; nunca sustituye silenciosamente por el histórico 209."""
    return Path(directory) / 'moto-track (6).xlsx'



def validate_history(data):
    """Comprueba series completas y alineadas; devuelve el último periodo real."""
    expected = {(region, product) for region, products in PRODUCTS.items() for product in products}
    actual = set(data[['Regional', 'Producto']].itertuples(index=False, name=None))
    if actual != expected:
        raise ValueError('Se requieren las 8 series Regional–Producto: Norte y Centro con Moto, '
                         'Cuatrimoto y Tractor; Sur únicamente con Moto y Cuatrimoto.')
    numeric = data[['Periodo', 'Demanda']].apply(pd.to_numeric, errors='coerce')
    if not np.isfinite(numeric.to_numpy(dtype=float)).all():
        raise ValueError('Hay periodos o demandas faltantes o no numéricos. No se imputan datos.')
    if (numeric.Demanda < 0).any() or (numeric.Periodo % 1 != 0).any():
        raise ValueError('Los periodos deben ser enteros y la demanda no puede ser negativa.')
    regional_periods = {region: sorted(data.loc[data.Regional == region, 'Periodo'].unique())
                        for region in PRODUCTS}
    if any(periods != regional_periods['Norte'] for periods in regional_periods.values()):
        counts = '; '.join(f'{region}: {len(periods)} periodos (último {max(periods)})'
                           for region, periods in regional_periods.items())
        raise ValueError(f'Regionales desalineadas. {counts}. Norte, Centro y Sur deben tener '
                         'los mismos periodos reales. Complete el archivo; no se eliminan ni imputan datos.')
    for (region, product), group in data.groupby(['Regional', 'Producto']):
        periods = sorted(group.Periodo.tolist())
        if periods != list(range(1, len(periods) + 1)):
            raise ValueError(f'{region}–{product}: los periodos deben ser consecutivos desde 1, '
                             'sin duplicados ni faltantes, y coincidir con los demás productos.')
        if len(periods) < MIN_HISTORY:
            raise ValueError(f'{region}–{product}: se encontraron {len(periods)} periodos; '
                             f'se requiere un mínimo de {MIN_HISTORY}.')
    return int(data.Periodo.max())


def load_data(content: bytes):
    book = pd.ExcelFile(BytesIO(content), engine='openpyxl')
    if not {'Demand', 'Summary'}.issubset(book.sheet_names):
        raise ValueError('El archivo debe contener las hojas Demand y Summary.')
    summary = pd.read_excel(book, sheet_name='Summary', header=None)
    settings = dict(zip(summary.iloc[:, 0], summary.iloc[:, 1]))
    if settings.get('Periods per Year') != 52 or settings.get('Playable Periods') != 18:
        raise ValueError('Se esperaba Periods per Year = 52 y Playable Periods = 18.')
    raw = pd.read_excel(book, sheet_name='Demand', header=None)
    records = []
    for region, products in PRODUCTS.items():
        matches = [(r, c) for r in range(min(10, len(raw))) for c in range(raw.shape[1])
                   if isinstance(raw.iat[r, c], str) and region.lower() in raw.iat[r, c].lower() and 'mototrack' in raw.iat[r, c].lower()]
        if len(matches) != 1:
            raise ValueError(f'No se identifica un único bloque para {region}.')
        row, col = matches[0]
        headers = [str(raw.iat[row + 1, col + k]).strip().upper() for k in range(len(products) + 1)]
        if headers != ['TURN'] + [p.upper() for p in products]:
            raise ValueError(f'Encabezados inválidos en {region}: {headers}')
        # Revisar el siguiente encabezado también impide aceptar un Tractor Sur.
        if col + len(products) + 1 < raw.shape[1] and str(raw.iat[row + 1, col + len(products) + 1]).upper() == 'TRACTOR':
            raise ValueError('Sur no debe contener Tractor.')
        block = raw.iloc[row + 2:, col:col + len(products) + 1].copy()
        block = block.dropna(how='all')
        numeric = block.apply(pd.to_numeric, errors='coerce')
        if numeric.isna().any().any() or not np.isfinite(numeric.to_numpy()).all():
            raise ValueError(f'{region}: hay valores faltantes o no numéricos; corrija el Excel. No se imputan datos.')
        numeric = numeric.sort_values(numeric.columns[0])
        if numeric.iloc[:, 0].tolist() != list(range(1, len(numeric) + 1)):
            raise ValueError(f'{region}: los periodos deben ser consecutivos desde 1, sin duplicados ni faltantes.')
        if (numeric.iloc[:, 1:] < 0).any().any():
            raise ValueError(f'{region}: hay demanda negativa.')
        for k, product in enumerate(products, 1):
            for period, demand in zip(numeric.iloc[:, 0], numeric.iloc[:, k]):
                records.append((int(period), region, product, float(demand)))
    data = pd.DataFrame(records, columns=['Periodo', 'Regional', 'Producto', 'Demanda'])
    validate_history(data)
    return data.sort_values(['Periodo', 'Regional', 'Producto']).reset_index(drop=True), settings


def analyze(data):
    rows = []
    for (region, product), g in data.groupby(['Regional', 'Producto']):
        history = g.sort_values('Periodo')
        y = history.Demanda.to_numpy()
        periods = history.Periodo.to_numpy()
        slope, intercept = np.polyfit(periods, y, 1)
        residual = y - (slope * periods + intercept)
        acf = float(np.corrcoef(residual[:-52], residual[52:])[0, 1]) if residual.std() > 1e-10 else np.nan
        rows.append({'Regional': region, 'Producto': product, 'Observaciones': len(y), 'Media': y.mean(), 'Mediana': np.median(y),
                     'Desviación estándar': y.std(ddof=1), 'Mínimo': y.min(), 'Máximo': y.max(),
                     'CV': y.std(ddof=1) / y.mean() if y.mean() else np.nan, 'Periodos cero': int((y == 0).sum()),
                     'Tendencia (unidades/periodo)': slope, 'Correlación rezago 52 sin tendencia': acf,
                     'Posible estacionalidad 52': 'Indicio positivo' if acf > .3 else 'Sin indicio fuerte'})
    return pd.DataFrame(rows)
