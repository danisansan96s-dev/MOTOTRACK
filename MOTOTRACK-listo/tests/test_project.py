from io import BytesIO
from pathlib import Path
import unittest
from unittest.mock import patch, Mock

import numpy as np
import pandas as pd
from openpyxl import load_workbook
from streamlit.testing.v1 import AppTest
from streamlit.dataframe_util import convert_arrow_bytes_to_pandas_df

from data_processing import load_data, PRODUCTS, default_workbook_path
from forecasting import MODELS, evaluate, forecast, predict, choose
from export import export_excel

ROOT = Path(__file__).resolve().parents[1]


class ProjectTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data, cls.settings = load_data(default_workbook_path(ROOT).read_bytes())
        cls.last_period = int(cls.data.Periodo.max())
        cls.horizon = 226 - cls.last_period
        cls.comparison, cls.selected, cls.validation = evaluate(cls.data)
        cls.detail, cls.consolidated, cls.notes = forecast(cls.data, cls.selected)

    def test_real_history(self):
        self.assertEqual(len(self.data), self.last_period * 8)
        actual = set(self.data[['Regional', 'Producto']].itertuples(index=False, name=None))
        self.assertEqual(actual, {(r, p) for r, products in PRODUCTS.items() for p in products})
        for _, group in self.data.groupby(['Regional', 'Producto']):
            self.assertEqual(group.Periodo.tolist(), list(range(1, self.last_period + 1)))
        self.assertNotIn(('Sur', 'Tractor'), actual)

    def test_invalid_workbooks(self):
        for cell, value in [('N2', 'TRACTOR'), ('A4', 1), ('B3', -1), ('B3', None)]:
            with self.subTest(cell=cell, value=value):
                book = load_workbook(ROOT / 'moto-track.xlsx')
                book['Demand'][cell] = value
                content = BytesIO()
                book.save(content)
                with self.assertRaises(ValueError):
                    load_data(content.getvalue())

    def test_models_and_selection(self):
        self.assertEqual(len(self.comparison), 8 * len(MODELS))
        self.assertEqual(len(self.selected), 8)
        seasonal = self.comparison[self.comparison.Modelo.str.startswith('Holt-Winters')]
        self.assertTrue((seasonal.Estado == 'OK').all(), seasonal.to_string())
        for (region, product), group in self.comparison.groupby(['Regional', 'Producto']):
            chosen = self.selected[(self.selected.Regional == region) & (self.selected.Producto == product)].iloc[0]
            self.assertEqual(chosen.Modelo, choose(group).Modelo)
            history = self.data[(self.data.Regional == region) & (self.data.Producto == product)].Demanda.to_numpy()
            prediction, _ = predict(history[:-52], chosen.Modelo, 52)
            checked = self.validation[(self.validation.Regional == region) & (self.validation.Producto == product) & (self.validation.Modelo == chosen.Modelo)]
            np.testing.assert_allclose(checked['Pronóstico'], prediction)
            np.testing.assert_allclose(checked.Real, history[-52:])

    def test_no_future_leakage(self):
        import forecasting
        original = forecasting.predict
        calls = []
        def tracked(y, model, horizon):
            calls.append((len(y), horizon))
            return original(y, model, horizon)
        changed = self.data.copy()
        changed.loc[changed.Periodo > self.last_period - 52, 'Demanda'] += 100000
        with patch('forecasting.predict', side_effect=tracked):
            _, _, validation = evaluate(self.data)
            _, _, altered = evaluate(changed)
        self.assertTrue(all(c == (self.last_period - 52, 52) for c in calls))
        np.testing.assert_allclose(validation['Pronóstico'], altered['Pronóstico'])

    def test_horizons_and_nonnegative(self):
        for horizon in [1, self.horizon]:
            detail, total, _ = forecast(self.data, self.selected, horizon)
            self.assertEqual(len(detail), horizon * 8)
            self.assertEqual(total.Periodo.tolist(), list(range(self.last_period + 1, self.last_period + 1 + horizon)))
            self.assertTrue((detail['Pronóstico'] >= 0).all())
            self.assertFalse(((detail.Regional == 'Sur') & (detail.Producto == 'Tractor')).any())
            self.assertEqual(total.Total.sum(), detail['Pronóstico'].sum())
        for horizon in [0, self.horizon + 1]:
            with self.assertRaises(ValueError):
                forecast(self.data, self.selected, horizon)
        self.assertTrue(np.isfinite(self.validation['Pronóstico']).all())
        self.assertTrue((self.validation['Pronóstico'] >= 0).all())
        fitted = Mock()
        fitted.forecast.return_value = np.full(18, -10.0)
        fitted.mle_retvals = {'success': True}
        with patch('forecasting.ExponentialSmoothing') as smoothing:
            smoothing.return_value.fit.return_value = fitted
            prediction, _ = predict(np.arange(208, dtype=float), 'Holt', 18)
            np.testing.assert_array_equal(prediction, np.zeros(18))
            fitted.mle_retvals = {'success': False}
            with self.assertRaisesRegex(ValueError, 'no convergió'):
                predict(np.arange(208, dtype=float), 'Holt', 18)

    def test_excel_roundtrip(self):
        payload = export_excel(self.detail, self.consolidated, self.selected, self.comparison)
        book = pd.ExcelFile(BytesIO(payload), engine='openpyxl')
        self.assertEqual(book.sheet_names, ['Pronostico_Detallado', 'Consolidado', 'Modelos', 'Comparacion_Modelos'])
        output = pd.read_excel(book, sheet_name='Pronostico_Detallado')
        pd.testing.assert_frame_equal(output, self.detail)
        (ROOT / 'MotoTrak_pronostico.xlsx').write_bytes(payload)

    def test_streamlit(self):
        app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=180).run()
        self.assertFalse(app.exception, str(app.exception))
        self.assertFalse(app.error, str(app.error))
        self.assertEqual([m.value for m in app.metric[:2]], [str(self.last_period), '8'])
        self.assertEqual(app.slider[0].max, self.horizon)
        self.assertTrue(any(f'próximo periodo a pronosticar: {self.last_period + 1}' in c.value for c in app.caption))
        totals = app.tabs[2].dataframe[0].value
        expected_totals = self.detail.groupby('Producto')['Pronóstico'].sum()
        for product, total, average in totals.itertuples(index=False, name=None):
            expected = self.detail['Pronóstico'].sum() if product == 'Total general' else expected_totals[product]
            self.assertEqual(total, expected)
            self.assertAlmostEqual(average, expected / self.horizon)
        self.assertEqual(totals.iloc[-1]['Total pronosticado'], totals.iloc[:3]['Total pronosticado'].sum())
        regional = app.tabs[2].dataframe[1].value
        self.assertEqual(len(regional), 8)
        self.assertEqual(regional['Total pronosticado'].sum(), self.detail['Pronóstico'].sum())
        self.assertFalse(((regional.Regional == 'Sur') & (regional.Producto == 'Tractor')).any())
        expected_regional = self.detail.groupby(['Regional', 'Producto'])['Pronóstico'].sum()
        for row in regional.itertuples(index=False, name=None):
            self.assertEqual(row[2], expected_regional.loc[(row[0], row[1])])
            self.assertAlmostEqual(row[3], row[2] / self.horizon)
        pd.testing.assert_frame_equal(app.tabs[1].dataframe[0].value, self.selected)
        pd.testing.assert_frame_equal(app.tabs[2].dataframe[4].value, self.detail)
        for table in app.tabs[1].dataframe[:2]:
            displayed = convert_arrow_bytes_to_pandas_df(table.proto.arrow_data.styler.display_values)
            for column in ['WMAPE', 'MAPE']:
                for actual, text in zip(table.value[column], displayed[column]):
                    expected = '—' if pd.isna(actual) else f'{actual * 100:.2f}'.replace('.', ',') + ' %'
                    self.assertEqual(text, expected)
        app.selectbox[0].select('Centro')
        app.selectbox[1].select('Cuatrimoto').run()
        filtered = app.tabs[2].dataframe[3].value
        self.assertEqual(len(filtered), self.horizon)
        self.assertEqual(set(filtered.Regional), {'Centro'})
        self.assertEqual(set(filtered.Producto), {'Cuatrimoto'})
        app.selectbox[0].select('Sur').run()
        self.assertEqual(set(app.selectbox[1].options), {'Moto', 'Cuatrimoto'})
        app.slider[0].set_value(1).run()
        short_totals = app.tabs[2].dataframe[0].value
        np.testing.assert_allclose(short_totals['Total pronosticado'], short_totals['Promedio por periodo'])
        self.assertEqual(len(app.tabs[2].dataframe[3].value), 1)
        self.assertEqual(len(app.tabs[2].dataframe[4].value), 8)
        self.assertFalse(app.exception, str(app.exception))
        self.assertFalse(app.error, str(app.error))
        self.assertEqual(len(app.get('download_button')), 2)


if __name__ == '__main__':
    unittest.main()
