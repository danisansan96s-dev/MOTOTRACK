"""Arranque y cargas en una copia limpia, sin Excel ni datos persistidos."""
from pathlib import Path
from tempfile import TemporaryDirectory
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DeploymentTest(unittest.TestCase):
    def run_clean_copy(self, code):
        with TemporaryDirectory() as temp:
            destination = Path(temp)
            for source in ROOT.glob('*.py'):
                shutil.copy2(source, destination / source.name)
            shutil.copytree(ROOT / '.streamlit', destination / '.streamlit')
            # Un proceso nuevo impide reutilizar módulos del proyecto original:
            # cada ruta de la app y de ABC-XYZ debe pertenecer a esta copia vacía.
            result = subprocess.run(
                [sys.executable, '-c', code, str(ROOT)], cwd=destination,
                capture_output=True, text=True, timeout=240)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_starts_without_local_excel_files(self):
        self.run_clean_copy('''
from pathlib import Path
from streamlit.testing.v1 import AppTest

root = Path.cwd()
assert not list(root.rglob('*.xlsx'))
app = AppTest.from_file('app.py', default_timeout=180).run()
assert not app.exception, app.exception
assert not app.error, app.error
assert len(app.get('file_uploader')) == 2
assert app.checkbox(key='persist_local_costs').value is False
assert any('Carga un archivo de demanda' in message.value for message in app.info)
assert not list(root.rglob('*.xlsx'))
''')

    def test_uploads_session_costs_and_unwritable_storage(self):
        self.run_clean_copy('''
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys
import pandas as pd
from streamlit.delta_generator import DeltaGenerator
from streamlit.testing.v1 import AppTest

source = Path(sys.argv[1])
demand_content = (source / 'moto-track-209.xlsx').read_bytes()
cost_content = (source / 'Costos_MotoTrak_Actividad.xlsx').read_bytes()
demand_upload = SimpleNamespace(getvalue=lambda: demand_content)
cost_upload = None

def uploaded(*args, **kwargs):
    return cost_upload if kwargs.get('key') == 'costs_upload' else demand_upload

def healthy(app):
    assert not app.exception, app.exception
    assert not app.error, app.error

with patch.object(DeltaGenerator, 'file_uploader', side_effect=uploaded):
    app = AppTest.from_file('app.py', default_timeout=180).run()
    healthy(app)
    assert [metric.value for metric in app.metric[:2]] == ['209', '8']
    assert app.slider[0].max == 17
    detail = app.tabs[2].dataframe[4].value.copy()
    assert sorted(detail.Periodo.unique()) == list(range(210, 227))
    assert detail.groupby(['Regional', 'Producto']).ngroups == 8
    assert not ((detail.Regional == 'Sur') & (detail.Producto == 'Tractor')).any()
    assert len(app.get('download_button')) == 1
    assert app.tabs[3].info
    assert not list(Path.cwd().rglob('*.xlsx'))

    cost_upload = SimpleNamespace(getvalue=lambda: cost_content)
    app.run()
    healthy(app)
    classification = app.tabs[3].dataframe[0].value.copy()
    assert len(classification) == 8
    assert len(app.get('download_button')) == 2
    pd.testing.assert_frame_equal(app.tabs[2].dataframe[4].value, detail)
    assert not list(Path.cwd().rglob('*.xlsx'))

    # Retirar la carga mantiene los últimos costos válidos solo en esta sesión.
    cost_upload = None
    app.run()
    healthy(app)
    pd.testing.assert_frame_equal(app.tabs[3].dataframe[0].value, classification)

    # Otra sesión conserva el pronosticador pero espera su propio archivo de costos.
    independent = AppTest.from_file('app.py', default_timeout=180).run()
    healthy(independent)
    assert independent.tabs[3].info
    assert len(independent.get('download_button')) == 1

    # Una ubicación sin permisos de escritura no impide clasificar ni descargar.
    with patch('abc_ui.persist_costs', side_effect=PermissionError('solo lectura')) as save:
        app.checkbox(key='persist_local_costs').check().run()
        healthy(app)
        assert save.called
        pd.testing.assert_frame_equal(app.tabs[3].dataframe[0].value, classification)
        pd.testing.assert_frame_equal(app.tabs[2].dataframe[4].value, detail)
        assert len(app.get('download_button')) == 2
    assert not list(Path.cwd().rglob('*.xlsx'))

import abc_ui
assert Path(abc_ui.__file__).resolve().parent == Path.cwd()
''')


if __name__ == '__main__':
    unittest.main()
