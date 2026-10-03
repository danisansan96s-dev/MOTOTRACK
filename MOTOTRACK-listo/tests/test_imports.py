"""La entrada Streamlit debe poder importarse en un intérprete nuevo."""
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ImportTest(unittest.TestCase):
    def test_all_modules_and_app_import_in_fresh_process(self):
        # Un proceso separado evita que las importaciones previas de otras
        # pruebas oculten incompatibilidades entre app.py y sus módulos.
        code = '''
import importlib
import py_compile
from pathlib import Path

root = Path.cwd()
for name in ('data_processing', 'forecasting', 'visualizations', 'export', 'abc_xyz', 'abc_ui', 'app'):
    py_compile.compile(str(root / (name + '.py')), doraise=True)
    module = importlib.import_module(name)
    assert Path(module.__file__).resolve() == root / (name + '.py'), module.__file__
    print(name + ': sintaxis e importaciones OK')

import app
assert callable(app.main)
assert callable(app.horizon_summary)
assert callable(app.regional_summary)
assert callable(app.model_table)
assert callable(app.summary_table)
'''
        result = subprocess.run([sys.executable, '-c', code], cwd=ROOT,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('app: sintaxis e importaciones OK', result.stdout)


if __name__ == '__main__':
    unittest.main()
