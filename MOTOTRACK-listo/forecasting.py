"""Validación contra los últimos 52 reales y reentrenamiento con todo el histórico."""
import warnings
import numpy as np
import pandas as pd
from statsmodels.tsa.holtwinters import ExponentialSmoothing
from data_processing import validate_history, FINAL_PERIOD, VALIDATION_PERIODS

MODELS = ['Naive', 'Naive estacional 52', 'Promedio móvil 4', 'Promedio móvil 8', 'Promedio móvil 13', 'SES', 'Holt', 'Holt amortiguado', 'Holt-Winters aditivo 52', 'Holt-Winters aditivo amortiguado 52']
TIE_WMAPE = .005  # 0.5 puntos porcentuales


def predict(y, model, horizon):
    y = np.asarray(y, dtype=float)
    if model not in MODELS:
        raise ValueError(f'Modelo desconocido: {model}')
    if not isinstance(horizon, (int, np.integer)) or horizon < 1:
        raise ValueError('El horizonte debe ser un entero positivo.')
    if y.ndim != 1 or len(y) == 0 or not np.isfinite(y).all():
        raise ValueError('La serie debe contener observaciones finitas.')
    if ('52' in model) and len(y) < (104 if model.startswith('Holt-Winters') else 52):
        raise ValueError('La serie no tiene suficientes periodos para la estacionalidad 52.')
    notes = []
    if model == 'Naive':
        result = np.repeat(y[-1], horizon)
    elif model == 'Naive estacional 52':
        result = np.resize(y[-52:], horizon)
    elif model.startswith('Promedio móvil'):
        window = int(model.split()[-1]); history = list(y); result = []
        for _ in range(horizon):
            value = np.mean(history[-window:]); result.append(value); history.append(value)
    else:
        seasonal = model.startswith('Holt-Winters')
        trend = None if model == 'SES' else 'add'
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            fitted = ExponentialSmoothing(y, trend=trend, damped_trend='amortiguado' in model,
                        seasonal='add' if seasonal else None, seasonal_periods=52 if seasonal else None,
                        initialization_method='estimated').fit(optimized=True)
            result = fitted.forecast(horizon)
        notes = list(dict.fromkeys(str(w.message) for w in caught))
        if hasattr(fitted, 'mle_retvals') and not fitted.mle_retvals.get('success', True):
            raise ValueError('La optimización no convergió; modelo excluido de selección.')
    result = np.asarray(result, dtype=float)
    if not np.isfinite(result).all():
        raise ValueError('Pronóstico no finito.')
    return np.maximum(result, 0), '; '.join(notes)


def metrics(actual, prediction):
    actual, prediction = np.asarray(actual, float), np.asarray(prediction, float)
    error = prediction - actual
    return {'MAE': float(np.abs(error).mean()), 'RMSE': float(np.sqrt(np.mean(error ** 2))),
            'WMAPE': float(np.abs(error).sum() / actual.sum()) if actual.sum() else np.nan,
            'Bias': float(error.mean()),
            'MAPE': float(np.mean(np.abs(error) / actual)) if np.all(actual > 0) else np.nan}


def choose(table):
    good = table[table.Estado == 'OK'].copy()
    if good.empty:
        raise ValueError('Ningún modelo produjo una validación válida.')
    if good.WMAPE.notna().any():
        good = good[good.WMAPE <= good.WMAPE.min() + TIE_WMAPE]
    good['Sesgo absoluto'] = good.Bias.abs()
    return good.sort_values(['RMSE', 'Sesgo absoluto', 'Modelo'], kind='stable').iloc[0]


def evaluate(data):
    validate_history(data)
    rows, validations = [], []
    for (region, product), g in data.groupby(['Regional', 'Producto']):
        history = g.sort_values('Periodo')
        y = history.Demanda.to_numpy()
        train, actual = y[:-VALIDATION_PERIODS], y[-VALIDATION_PERIODS:]
        validation_periods = history.Periodo.to_numpy()[-VALIDATION_PERIODS:]
        for model in MODELS:
            row = {'Regional': region, 'Producto': product, 'Modelo': model}
            try:
                predicted, notes = predict(train, model, VALIDATION_PERIODS)
                row.update(metrics(actual, predicted), Estado='OK', Aviso=notes)
                validations.extend((region, product, model, p, a, f) for p, a, f in zip(validation_periods, actual, predicted))
            except Exception as exc:
                row.update({m: np.nan for m in ['MAE', 'RMSE', 'WMAPE', 'Bias', 'MAPE']}, Estado='Error', Aviso=str(exc))
            rows.append(row)
    comparison = pd.DataFrame(rows)
    selected = pd.DataFrame([choose(g).drop(labels=['Sesgo absoluto'], errors='ignore') for _, g in comparison.groupby(['Regional','Producto'])]).reset_index(drop=True)
    validation = pd.DataFrame(validations, columns=['Regional','Producto','Modelo','Periodo','Real','Pronóstico'])
    return comparison, selected, validation


def forecast(data, selected, horizon=None, common_model=False):
    last_period = validate_history(data)
    remaining = FINAL_PERIOD - last_period
    if remaining <= 0:
        raise ValueError(f'La simulación finalizó en el periodo {FINAL_PERIOD}; no quedan periodos futuros.')
    if horizon is None:
        horizon = remaining
    if not isinstance(horizon, (int, np.integer)) or not 1 <= horizon <= remaining:
        raise ValueError(f'El horizonte debe estar entre 1 y {remaining}; '
                         f'no puede superar el periodo {FINAL_PERIOD}.')
    rows, fit_notes = [], []
    for _, row in selected.iterrows():
        series = data[(data.Regional == row.Regional) & (data.Producto == row.Producto)].sort_values('Periodo')
        model = MODELS[8] if common_model else row.Modelo
        prediction, notes = predict(series.Demanda.to_numpy(), model, horizon)
        fit_notes.append({'Regional':row.Regional, 'Producto':row.Producto,'Modelo':model,'Aviso':notes})
        units = np.floor(prediction + .5).astype(int)
        rows.extend((last_period + 1 + k, row.Regional, row.Producto, int(v), model) for k, v in enumerate(units))
    detail = pd.DataFrame(rows, columns=['Periodo','Regional','Producto','Pronóstico','Modelo'])
    consolidated = detail.pivot_table(index='Periodo', columns='Producto', values='Pronóstico', aggfunc='sum').reindex(columns=['Moto','Cuatrimoto','Tractor']).astype(int)
    consolidated['Total'] = consolidated.sum(axis=1)
    return detail, consolidated.reset_index(), pd.DataFrame(fit_notes)
