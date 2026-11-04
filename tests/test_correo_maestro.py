# paso_correo (con un IMAP falso, sin red) y paso_maestro (con PDFs falsos de texto).
import os, email
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from email.mime.text import MIMEText
from email.header import Header
import pytest
import openpyxl
from conftest import escribir_pdf, fijar_hoy


def correo(mid, adjuntos, de='daniel@ejemplo.com'):
    m = MIMEMultipart(); m['Message-ID'] = mid; m['From'] = de; m['Subject'] = 'informes'
    m.attach(MIMEText('adjunto', 'plain'))
    for nombre, datos, ctype in adjuntos:
        part = MIMEApplication(datos, _subtype=ctype.split('/')[1]) if datos is not None else MIMEApplication(b'', _subtype='pdf')
        if datos is None:
            part.set_payload(None)
        part.add_header('Content-Disposition', 'attachment', filename=nombre)
        m.attach(part)
    return m.as_bytes()


class FakeIMAP:
    buzon = []                     # lista de bytes de mensajes
    def __init__(self, host): self.host = host
    def login(self, u, p): pass
    def select(self, b): return 'OK', [b'1']
    def search(self, charset, crit): return 'OK', [' '.join(str(i + 1) for i in range(len(self.buzon))).encode()]
    def fetch(self, i, spec):
        raw = self.buzon[int(i) - 1]
        if 'HEADER.FIELDS' in spec:
            msg = email.message_from_bytes(raw)
            campos = spec.split('(')[-1].rstrip('])').split()
            hdr = ''.join(f'{c}: {msg[c]}\r\n' for c in campos if msg[c]) + '\r\n'
            return 'OK', [(b'1 (BODY[HEADER])', hdr.encode())]
        return 'OK', [(b'1 (RFC822)', raw)]
    def logout(self): pass


@pytest.fixture
def imap(P, monkeypatch):
    monkeypatch.setenv('GMAIL_USER', 'u'); monkeypatch.setenv('GMAIL_APP_PASSWORD', 'p')
    monkeypatch.delenv('REMITENTES_PERMITIDOS', raising=False)
    monkeypatch.setattr(P.imaplib, 'IMAP4_SSL', FakeIMAP)
    open(P.VISTOS, 'w').close()
    FakeIMAP.buzon = []
    return FakeIMAP


def test_correo_sin_credenciales(P, monkeypatch):
    monkeypatch.delenv('GMAIL_USER', raising=False)
    assert P.paso_correo() == (0, [])


def test_correo_adjunto_con_acento_codificado(P, imap):
    nombre = Header('Boletín Producción.pdf', 'utf-8').encode()       # =?utf-8?b?...?=
    imap.buzon = [correo('<a@x>', [(nombre, b'%PDF-1', 'application/pdf')])]
    n, nombres = P.paso_correo()
    assert n == 1 and nombres == ['Boletin Produccion.pdf']
    assert os.path.exists(os.path.join(P.PDFS, 'Boletin Produccion.pdf'))
    assert open(P.VISTOS).read().split() == ['<a@x>']
    # segunda pasada: ya visto -> no se vuelve a descargar
    os.remove(os.path.join(P.PDFS, 'Boletin Produccion.pdf'))
    assert P.paso_correo() == (0, [])


def test_correo_mismo_nombre_en_dos_correos_no_pierde_el_nuevo(P, imap):
    """Dos correos con un adjunto del MISMO nombre y distinto contenido (p.ej. 'Boletin.pdf' de dos semanas)
    en la misma pasada: antes se guardaba el primero (el más antiguo), se tiraba el nuevo y el correo quedaba
    'visto' para siempre."""
    imap.buzon = [correo('<s44@x>', [('Boletin.pdf', b'semana 44', 'application/pdf')]),
                  correo('<s45@x>', [('Boletin.pdf', b'semana 45', 'application/pdf')]),
                  correo('<s45bis@x>', [('Boletin.pdf', b'semana 45', 'application/pdf')])]   # reenvío idéntico
    n, nombres = P.paso_correo()
    contenidos = sorted(open(os.path.join(P.PDFS, f), 'rb').read() for f in os.listdir(P.PDFS))
    assert contenidos == [b'semana 44', b'semana 45']
    assert n == 2


def test_correo_adjunto_vacio_no_rompe(P, imap):
    imap.buzon = [correo('<v@x>', [('PO261029.pdf', None, 'application/pdf'), ('DE261029.pdf', b'ok', 'application/pdf')])]
    n, nombres = P.paso_correo()
    assert nombres == ['DE261029.pdf']


def test_correo_filtra_remitentes_si_se_configura(P, imap, monkeypatch):
    """Cualquiera que conozca la dirección puede meter PDFs (y precios) en el sistema. Con
    REMITENTES_PERMITIDOS solo se aceptan los adjuntos de esas direcciones o dominios."""
    imap.buzon = [correo('<ok@x>', [('PO261029.pdf', b'bueno', 'application/pdf')], de='Daniel <daniel@grupojorge.es>'),
                  correo('<ko@x>', [('PO261105.pdf', b'falso', 'application/pdf')], de='atacante@malo.com')]
    monkeypatch.setenv('REMITENTES_PERMITIDOS', '@grupojorge.es, otro@ejemplo.com')
    n, nombres = P.paso_correo()
    assert nombres == ['PO261029.pdf']
    assert set(open(P.VISTOS).read().split()) == {'<ok@x>', '<ko@x>'}   # el rechazado tampoco se re-descarga


def test_correo_sin_filtro_acepta_todo(P, imap):
    imap.buzon = [correo('<ko@x>', [('PO261105.pdf', b'x', 'application/pdf')], de='cualquiera@otro.com')]
    assert P.paso_correo()[1] == ['PO261105.pdf']


# ---------------- maestro ----------------
PO = "MERCOLLEIDA\nCerdo Blanco 1,168 1,137 -0,031\n"
DE = "DESPIECE\nAlemania NW-AMI Canal 57% 1,40 1,32 €/kg\nFRANCIA MPB 1,562 1,502 €\nCerdo cebado 1,137\n"
IP = "INFO PORCINO\nEUROS KILO VIVO\nDinamarca 7,50 1,00\n"


def test_maestro_escribe_en_su_celda(P):
    escribir_pdf(P.PDFS, 'PO261105.pdf', PO)        # jueves 5-nov-2026 = W45
    escribir_pdf(P.PDFS, 'DE261109.pdf', DE)        # W46
    escribir_pdf(P.PDFS, 'IP261109.pdf', IP)
    r = P.paso_maestro()
    assert r['nuevas'] == [(2026, 45)]
    wb = openpyxl.load_workbook(P.MASTER)
    assert wb['CEBADO LLEIDA'].cell(8 + 45, P._col_anual(2026)).value == 1.137
    assert wb['ALEMANIA'].cell(8 + 46, P._col_anual(2026)).value == 1.32
    assert wb['FRANCIA'].cell(8 + 46, P._col_fra_val(2026)).value == 1.502
    assert wb['DINAMARCA'].cell(8 + 46, P._col_anual(2026)).value == 7.5
    assert r['lat']['sem'] == 46 and r['lat']['cebado'] == 1.137


def test_maestro_no_sobrescribe(P):
    escribir_pdf(P.PDFS, 'PO261029.pdf', PO.replace('1,137', '9,999'))     # W44 ya está (1,168)
    P.paso_maestro()
    wb = openpyxl.load_workbook(P.MASTER)
    assert wb['CEBADO LLEIDA'].cell(8 + 44, P._col_anual(2026)).value == 1.168


def test_maestro_anio_nuevo_y_semana_53(P):
    escribir_pdf(P.PDFS, 'PO261231.pdf', PO)        # 2026-W53 -> fila 61
    escribir_pdf(P.PDFS, 'PO270107.pdf', PO)        # 2027-W1 -> columna nueva
    escribir_pdf(P.PDFS, 'DE270107.pdf', DE)
    P.paso_maestro()
    wb = openpyxl.load_workbook(P.MASTER)
    assert wb['CEBADO LLEIDA'].cell(61, P._col_anual(2026)).value == 1.137
    assert wb['CEBADO LLEIDA'].cell(8, P._col_anual(2027)).value == 2027
    assert wb['CEBADO LLEIDA'].cell(9, P._col_anual(2027)).value == 1.137
    assert wb['FRANCIA'].cell(7, P._col_fra_hdr(2027)).value == 2027
    assert wb['FRANCIA'].cell(9, P._col_fra_val(2027)).value == 1.502
    assert P._col_anual(2026) == 18 and P._col_fra_val(2026) == 35   # los de siempre


def test_maestro_relleno_hacia_delante(P):
    escribir_pdf(P.PDFS, 'DE270107.pdf', DE)        # 2027-W1
    escribir_pdf(P.PDFS, 'DE270121.pdf', DE)        # 2027-W3 (falta la W2)
    P.paso_maestro()
    wb = openpyxl.load_workbook(P.MASTER)
    assert wb['ALEMANIA'].cell(8 + 2, P._col_anual(2027)).value == 1.32   # rellenado con la W1


def test_extraccion_pdf_raro_no_rompe(P):
    escribir_pdf(P.PDFS, 'PO261105.pdf', 'pagina escaneada sin texto')
    escribir_pdf(P.PDFS, 'DE261109.pdf', '')
    r = P.paso_maestro()
    assert r['nuevas'] == []
    assert P.esp_de_po(os.path.join(P.PDFS, 'PO261105.pdf')) is None
    assert P.ale_fra_de_de(os.path.join(P.PDFS, 'DE261109.pdf')) == (None, None)
    assert P.din_de_ip('/no/existe.pdf') is None


def test_esp_de_po_fallback_equivalente(P):
    t = "España 1,1 1,2 1,3 1,4 1,5 1,6 1,170 1,8 1,9 2,0 2,1 2,2\n"
    escribir_pdf(P.PDFS, 'PO261105.pdf', t)
    assert P.esp_de_po(os.path.join(P.PDFS, 'PO261105.pdf')) == 1.17
