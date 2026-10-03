"""Verifica salud HTTP y ejecuta una sesión real contra el servidor activo."""
from time import monotonic
from urllib.request import urlopen
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from data_processing import load_data, FINAL_PERIOD, default_workbook_path

from streamlit.proto.BackMsg_pb2 import BackMsg
from streamlit.proto.ForwardMsg_pb2 import ForwardMsg
from websockets.sync.client import connect

BASE = 'http://127.0.0.1:8501'


def main():
    history, _ = load_data(default_workbook_path(Path(__file__).resolve().parents[1]).read_bytes())
    last_period = int(history.Periodo.max())
    with urlopen(BASE + '/_stcore/health', timeout=10) as response:
        body = response.read().decode()
        if response.status != 200 or body.strip() != 'ok':
            raise RuntimeError(f'Health inesperado: {response.status} {body}')
        print(f'HTTP /_stcore/health: {response.status} {body}', flush=True)

    # La salud HTTP solo confirma que el servidor está vivo. El WebSocket
    # ejecuta app.py y detecta excepciones que se muestran en el navegador.
    with connect('ws://127.0.0.1:8501/_stcore/stream', subprotocols=['streamlit'],
                 origin=BASE, open_timeout=10, max_size=20 * 1024 * 1024) as session:
        request = BackMsg()
        request.rerun_script.SetInParent()
        session.send(request.SerializeToString())
        deadline = monotonic() + 90
        metrics, text = {}, []
        while monotonic() < deadline:
            message = ForwardMsg.FromString(session.recv(timeout=deadline - monotonic()))
            kind = message.WhichOneof('type')
            if kind == 'delta' and message.delta.HasField('new_element'):
                element = message.delta.new_element
                if element.HasField('exception'):
                    raise RuntimeError(f'{element.exception.type}: {element.exception.message}')
                if element.HasField('metric'):
                    metrics[element.metric.label] = element.metric.body
                if element.HasField('markdown'):
                    text.append(element.markdown.body)
                if element.HasField('heading'):
                    text.append(element.heading.body)
            if kind == 'script_finished':
                if message.script_finished == ForwardMsg.FINISHED_EARLY_FOR_RERUN:
                    continue
                if message.script_finished != ForwardMsg.FINISHED_SUCCESSFULLY:
                    raise RuntimeError(f'Ejecución incompleta: {message.script_finished}')
                for label, expected in [('Periodos históricos', str(last_period)), ('Series', '8')]:
                    if metrics.get(label) != expected:
                        raise RuntimeError(f'KPI inesperado: {label} = {metrics.get(label)}')
                if last_period < FINAL_PERIOD:
                    for label in ['Total de Motos', 'Total de Cuatrimotos', 'Total de Tractores', 'Total general']:
                        if label not in metrics:
                            raise RuntimeError(f'Falta KPI: {label}')
                    if not any('Resumen del horizonte de pronóstico' in value for value in text):
                        raise RuntimeError('No se renderizó el resumen del horizonte.')
                if 'Utilidad total · 52 semanas' not in metrics:
                    raise RuntimeError('No se renderizó el módulo ABC-XYZ.')
                for family in ['ABC', 'XYZ']:
                    if sum(int(metrics.get(f'SKU {label}', '-100')) for label in family) != 8:
                        raise RuntimeError(f'El módulo {family} no clasificó los 8 SKU.')
                print('Sesión Streamlit: app.py sin excepciones; pronosticador y ABC-XYZ con 8 SKU OK.')
                return
        raise TimeoutError('La sesión Streamlit no terminó en 90 segundos.')


if __name__ == '__main__':
    main()
