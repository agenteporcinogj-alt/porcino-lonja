# Infraestructura comun de las pruebas.
# - NUNCA escribe en data/ ni en docs/: todas las rutas del pipeline se redirigen a una carpeta temporal.
# - Sin red: pdfplumber e IMAP se sustituyen por dobles que leen texto plano.
# - PORCINO_SRC=<carpeta> permite ejecutar las MISMAS pruebas contra otra version del codigo
#   (p.ej. la de main) para comprobar que fallaban antes del arreglo.
import os, sys, shutil, hashlib, importlib, datetime, types, glob
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.environ.get('PORCINO_SRC', ROOT)
DATA = os.path.join(ROOT, 'data')
if SRC not in sys.path:
    sys.path.insert(0, SRC)


def _huella(carpetas):
    h = hashlib.sha256()
    for c in carpetas:
        for f in sorted(glob.glob(os.path.join(ROOT, c, '**', '*'), recursive=True)):
            if os.path.isfile(f):
                h.update(f.encode()); h.update(open(f, 'rb').read())
    return h.hexdigest()


@pytest.fixture(scope='session', autouse=True)
def no_tocar_data_ni_docs():
    """Garantia: al terminar las pruebas data/, docs/ y .github/ estan exactamente igual que al empezar."""
    antes = _huella(['data', 'docs', '.github'])
    yield
    assert _huella(['data', 'docs', '.github']) == antes, 'Las pruebas han modificado data/, docs/ o .github/'


# ---------- doble de pdfplumber: un "PDF" es un fichero de texto; las paginas se separan con \f ----------
class _Pag:
    def __init__(self, t): self.t = t
    def extract_text(self): return self.t

class _PDF:
    def __init__(self, path):
        txt = open(path, encoding='utf-8').read()
        self.pages = [_Pag(p) for p in txt.split('\f')]
    def __enter__(self): return self
    def __exit__(self, *a): return False


def escribir_pdf(carpeta, nombre, *paginas):
    p = os.path.join(carpeta, nombre)
    with open(p, 'w', encoding='utf-8') as f: f.write('\f'.join(paginas))
    return p


@pytest.fixture
def EX():
    import extractores
    return importlib.reload(extractores)


@pytest.fixture
def P(tmp_path, monkeypatch):
    """Modulo pipeline con TODAS sus rutas apuntando a tmp_path (copias de data/)."""
    import pipeline
    P = importlib.reload(pipeline)
    d = tmp_path / 'data'; d.mkdir()
    for f in os.listdir(DATA):
        shutil.copy(os.path.join(DATA, f), d / f)
    pdfs = tmp_path / '_pdfs'; pdfs.mkdir()
    (tmp_path / 'docs').mkdir()
    monkeypatch.setattr(P, 'PDFS', str(pdfs))
    monkeypatch.setattr(P, 'MASTER', str(d / 'master.xlsx'))
    monkeypatch.setattr(P, 'LOG', str(d / 'validacion_modelo.csv'))
    monkeypatch.setattr(P, 'VISTOS', str(d / 'correos_vistos.txt'))
    monkeypatch.setattr(P, 'CTX_FILE', str(d / 'contexto_mercado.json'))
    monkeypatch.setattr(P, 'CONG_FILE', str(d / 'predicciones_congeladas.jsonl'))
    monkeypatch.setattr(P, 'SERIES_FILE', str(d / 'series_historicas.json'))
    monkeypatch.setattr(P, 'ENC_OUT', str(tmp_path / 'docs' / 'data.enc.js'))
    monkeypatch.setattr(P.pdfplumber, 'open', lambda path: _PDF(path))
    P._tmp = tmp_path
    return P


def fijar_hoy(monkeypatch, P, y, m, d, hh=12, mi=0):
    """Congela 'hoy' dentro del pipeline (date.today() y datetime.now())."""
    real = datetime

    class FDate(real.date):
        @classmethod
        def today(cls): return cls(y, m, d)

    class FDateTime(real.datetime):
        @classmethod
        def now(cls, tz=None):
            base = real.datetime(y, m, d, hh, mi, tzinfo=real.timezone.utc)
            return base.astimezone(tz) if tz else base.replace(tzinfo=None)

    shim = types.SimpleNamespace(date=FDate, datetime=FDateTime, timedelta=real.timedelta, timezone=real.timezone)
    monkeypatch.setattr(P, 'datetime', shim)
    return shim


# ---------- maestro sintetico con el mismo formato que data/master.xlsx ----------
def maestro_sintetico(path, hasta=(2026, 44), desde_y=2010, sin=None):
    """Crea un master.xlsx con datos sinteticos (2010..hasta). sin: set de (y,w) que se dejan vacios."""
    import math, openpyxl
    sin = sin or set()
    wb = openpyxl.Workbook(); wb.remove(wb.active)
    hojas = {n: wb.create_sheet(n) for n in ('CEBADO LLEIDA', 'ALEMANIA', 'FRANCIA', 'DINAMARCA')}
    for n in ('CEBADO LLEIDA', 'ALEMANIA', 'DINAMARCA'):
        hojas[n].cell(8, 1).value = 'SEMANAS'
    hojas['FRANCIA'].cell(8, 1).value = 'SEMANAS'
    t = 0
    for y in range(desde_y, hasta[0] + 1):
        nw = datetime.date(y, 12, 28).isocalendar()[1]
        for n in ('CEBADO LLEIDA', 'ALEMANIA', 'DINAMARCA'):
            hojas[n].cell(8, 2 + (y - 2010)).value = y
        hojas['FRANCIA'].cell(7, 2 + 2 * (y - 2010)).value = y
        for w in range(1, nw + 1):
            if (y, w) > hasta: break
            t += 1
            if (y, w) in sin: continue
            base = 1.40 + 0.15 * math.sin(2 * math.pi * (w - 10) / 52) + 0.05 * math.sin(t / 37.0)
            hojas['CEBADO LLEIDA'].cell(8 + w, 2 + (y - 2010)).value = round(base, 3)
            hojas['ALEMANIA'].cell(8 + w, 2 + (y - 2010)).value = round(base * 1.25 + 0.01 * math.sin(t / 5), 3)
            hojas['FRANCIA'].cell(8 + w, 2 + 2 * (y - 2010) + 1).value = round(base * 1.2 + 0.01 * math.cos(t / 7), 3)
            hojas['DINAMARCA'].cell(8 + w, 2 + (y - 2010)).value = round(base * 7, 2)
    wb.save(path)
    return path
