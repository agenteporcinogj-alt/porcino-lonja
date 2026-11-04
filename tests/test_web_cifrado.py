# Cifrado AES-GCM/PBKDF2 (ida y vuelta, también contra el JavaScript de la web) y paso_web.
import base64, json, os, re, shutil, subprocess
import pytest
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from conftest import fijar_hoy, ROOT


def descifrar(b64, pw):
    raw = base64.b64decode(b64)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=raw[:16], iterations=200000).derive(pw.encode())
    return AESGCM(key).decrypt(raw[16:28], raw[28:], None).decode()


def test_cifrar_ida_y_vuelta(P):
    txt = json.dumps({'precio': 1.137, 'texto': 'Peña ñandú €'}, ensure_ascii=False)
    a = P.cifrar(txt, 'clave'); b = P.cifrar(txt, 'clave')
    assert a != b                                       # sal e IV aleatorios
    assert descifrar(a, 'clave') == txt
    with pytest.raises(Exception):
        descifrar(a, 'otra')
    assert len(base64.b64decode(a)) == 16 + 12 + len(txt.encode()) + 16


@pytest.mark.skipif(not shutil.which('node'), reason='node no instalado')
def test_cifrado_python_lo_descifra_la_web(P):
    """El blob que genera pipeline.cifrar lo descifra decryptData() de docs/index.html (WebCrypto en node)."""
    txt = json.dumps({'ultimo': {'v': 1.168}, 'ñ': '€'}, ensure_ascii=False)
    blob = P.cifrar(txt, 'contraseña con ñ')
    js = ("const {cargarIndex}=require(process.argv[1]);const X=cargarIndex();"
          "X.decryptData(process.argv[2],process.argv[3]).then(d=>{console.log(JSON.stringify(d));})"
          ".catch(e=>{console.error('ERR',e);process.exit(1);});")
    r = subprocess.run(['node', '-e', js, os.path.join(ROOT, 'tests', 'js', 'cargar_index.js'), blob, 'contraseña con ñ'],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == json.loads(txt)


def _payload(P, monkeypatch, pw='secreta'):
    if pw is None: monkeypatch.delenv('WEB_PASSWORD', raising=False)
    else: monkeypatch.setenv('WEB_PASSWORD', pw)
    m = P.paso_modelo(); h = P.paso_registro(m)
    P.paso_web(m, h, {'ts': 'x', 'pdfs_nuevos': 0, 'nombres': [], 'nuevas': [], 'lat': {}}, {'ppa': {'resumen': 'x'}})
    return m


def test_paso_web_payload_completo(P, monkeypatch):
    fijar_hoy(monkeypatch, P, 2026, 11, 3, 13, 5)
    m = _payload(P, monkeypatch)
    t = open(P.ENC_OUT).read()
    assert t.startswith('window.ENC="') and t.endswith('";')
    d = json.loads(descifrar(re.search(r'"([^"]+)"', t).group(1), 'secreta'))
    for k in ('generado', 'ultimo', 'prediccion', 'historial', 'serie', 'serie_paises', 'contrib', 'semaforo',
              'medio_plazo', 'contexto_mercado', 'diario', 'seas'):
        assert k in d, k
    assert d['prediccion'] == {'y': 2026, 'w': 45, 'v': m['pred']}
    assert d['semaforo']['estado'] == 'VENDE' and 'BAJAR ~3,1 cts' in d['semaforo']['txt']   # coma decimal
    bt = {(r['anio'], r['semana']): r['backtest'] for r in d['historial']}
    assert bt[('2026', '44')] is False and bt[('2026', '40')] is False
    assert bt[('2026', '39')] is True        # apuntada el viernes 25-sep, después del jueves de publicación
    assert d['medio_plazo']['n_congeladas'] == 4 and d['medio_plazo']['n_congeladas_v2'] == 2


def test_paso_web_hora_de_madrid(P, monkeypatch):
    """En GitHub Actions el reloj está en UTC: a las 13:05 UTC de un martes de noviembre en Lleida son las
    14:05. La web enseñaba 'Actualizado ... 13:05' sin decir que era UTC."""
    fijar_hoy(monkeypatch, P, 2026, 11, 3, 13, 5)
    _payload(P, monkeypatch)
    d = json.loads(descifrar(re.search(r'"([^"]+)"', open(P.ENC_OUT).read()).group(1), 'secreta'))
    assert d['generado'] == '03/11/2026 14:05'


@pytest.mark.parametrize('pw', ['', None])
def test_paso_web_sin_contrasena_no_publica(P, monkeypatch, pw):
    """Si falta el secreto WEB_PASSWORD, GitHub pasa la variable VACÍA: antes se cifraba con contraseña ''
    (o con la conocida 'cerdo') y se publicaba. Ahora no se escribe la web y se avisa."""
    _payload(P, monkeypatch, pw=pw)
    assert not os.path.exists(P.ENC_OUT)
