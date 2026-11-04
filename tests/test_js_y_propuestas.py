# Lanza las pruebas de JavaScript (node --test) desde pytest y comprueba que los parches de propuestas/ aplican.
import glob, os, shutil, subprocess
import pytest
from conftest import ROOT

NODE = shutil.which('node')


@pytest.mark.skipif(not NODE, reason='node no instalado')
@pytest.mark.parametrize('fichero', sorted(os.path.basename(f) for f in glob.glob(os.path.join(ROOT, 'tests', 'js', '*.test.js'))))
def test_node(fichero):
    r = subprocess.run([NODE, '--test', os.path.join(ROOT, 'tests', 'js', fichero)], capture_output=True, text=True, timeout=300,
                       env={**os.environ})
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]


def test_parches_ya_aplicados_en_produccion():
    """En el lab estos arreglos iban como parche en propuestas/; en produccion ya estan aplicados."""
    wf = open(os.path.join(ROOT, '.github', 'workflows', 'daily.yml')).read()
    assert 'concurrency:\n  group: pipeline-diario\n  cancel-in-progress: false' in wf and 'timeout-minutes: 20' in wf
    assert 'MAX_CONSULTAS_DIA' in open(os.path.join(ROOT, 'worker', 'worker.js')).read()
