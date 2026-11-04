# Congelación de trayectorias a 26 semanas (V1 y V2), evaluación por horizonte y predictor de peso.
import json, datetime
import pytest
from conftest import fijar_hoy


def _m(py=2026, pw=45, pred=1.137, seas=None):
    seas = seas or {str(w): 100.0 for w in range(1, 54)}
    return {'nexty': py, 'nextw': pw, 'pred': pred, 'seas': seas, 'last': {'y': 2026, 'w': pw - 1 if pw > 1 else 52, 'v': 1.168}}


def _leer(P):
    return [json.loads(l) for l in open(P.CONG_FILE)]


@pytest.fixture
def vacio(P):
    open(P.CONG_FILE, 'w').close()
    return P


def test_congela_una_vez_v1_y_v2(vacio, monkeypatch):
    P = vacio
    fijar_hoy(monkeypatch, P, 2026, 11, 3)                      # martes de la semana 45
    pm = {'corr': {h: -0.01 for h in range(1, 14)}, 'ctx': {'anomalia_vs_normal_kg': -2.0}}
    P.paso_congelar(_m(pw=46), pm)
    P.paso_congelar(_m(pw=46), pm)                              # segunda pasada del día: nada nuevo
    recs = _leer(P)
    assert [r['forecast_id'] for r in recs] == ['2026-W46-v1', '2026-W46-v2-peso']
    for r in recs:
        assert len(r['trayectoria']) == 26 and r['issued_at'] == '2026-11-03'


def test_no_congela_si_ya_no_es_a_ciegas(vacio, monkeypatch):
    P = vacio
    fijar_hoy(monkeypatch, P, 2026, 11, 12)                     # jueves de la semana 46: ya publicado
    P.paso_congelar(_m(pw=46), None)
    assert _leer(P) == []
    fijar_hoy(monkeypatch, P, 2026, 11, 11)                     # miércoles: todavía a ciegas
    P.paso_congelar(_m(pw=46), None)
    assert len(_leer(P)) == 1


def test_trayectoria_cruza_semana_53_sin_huecos_ni_duplicados(vacio, monkeypatch):
    P = vacio
    fijar_hoy(monkeypatch, P, 2026, 11, 3)
    P.paso_congelar(_m(pw=46), None)
    t = _leer(P)[0]['trayectoria']
    ws = [(p['y'], p['w']) for p in t]
    assert len(set(ws)) == 26
    assert ws[0] == (2026, 46) and (2026, 53) in ws and ws[ws.index((2026, 53)) + 1] == (2027, 1)
    for a, b in zip(ws, ws[1:]):      # consecutivas por FECHA
        da = datetime.date.fromisocalendar(a[0], a[1], 1); db = datetime.date.fromisocalendar(b[0], b[1], 1)
        assert (db - da).days == 7
    assert [p['h'] for p in t] == list(range(26))


def test_banda_de_la_primera_semana_no_es_cero(vacio, monkeypatch):
    """h=0 es la predicción a 1 semana vista (la semana objetivo): su error no es 0. BANDA_CAL[0]=0.0 es
    el horizonte 0 (el último dato real ya conocido), así que cada punto debe usar BANDA_CAL[h+1].
    Antes la banda de la 1ª semana medía 0 y todas las demás iban una semana 'adelantadas' (más estrechas)."""
    P = vacio
    fijar_hoy(monkeypatch, P, 2026, 11, 3)
    P.paso_congelar(_m(pw=46), None)
    t = _leer(P)[0]['trayectoria']
    semi = [round((p['hi'] - p['lo']) / 2, 3) for p in t]
    assert semi[0] == P.BANDA_CAL[1] > 0
    assert semi[12] == P.BANDA_CAL[13]
    assert semi[25] == P.BANDA_CAL[26]
    assert semi == sorted(semi)                                  # se abre con el horizonte


def test_v2_correccion_peso_por_horizonte(vacio, monkeypatch):
    P = vacio
    fijar_hoy(monkeypatch, P, 2026, 11, 3)
    pm = {'corr': {h: -0.02 for h in range(1, 14)}, 'ctx': {'anomalia_vs_normal_kg': 1.5}}
    P.paso_congelar(_m(pw=46), pm)
    v1, v2 = [r['trayectoria'] for r in _leer(P)]
    d = [round(b['central'] - a['central'], 3) for a, b in zip(v1, v2)]
    assert d[0] == 0.0                                           # 1 semana: manda el modelo semanal
    assert all(x == -0.02 for x in d[1:13])                      # 2-13 semanas: corrección plena
    assert d[13] == round(-0.02 * 6 / 7, 3)                      # 14 semanas: empieza a desvanecerse
    assert all(x == 0.0 for x in d[19:])                         # >=20 semanas: nada
    for a, b in zip(v1, v2):                                     # misma anchura de banda
        assert round(a['hi'] - a['lo'], 3) == round(b['hi'] - b['lo'], 3)


def test_registro_antiguo_sin_version_no_duplica(vacio, monkeypatch):
    P = vacio
    fijar_hoy(monkeypatch, P, 2026, 11, 3)
    with open(P.CONG_FILE, 'w') as f:
        f.write(json.dumps({'target_y': 2026, 'target_w': 46, 'model_version': 'v1', 'trayectoria': []}) + '\n')
        f.write('esto no es json\n')
    P.paso_congelar(_m(pw=46), None)
    assert len(open(P.CONG_FILE).read().strip().split('\n')) == 2


def test_eval_horizontes(P):
    cong = [{'model_version': 'v1', 'trayectoria': [
        {'y': 2026, 'w': 44, 'h': 0, 'central': 1.175, 'lo': 1.156, 'hi': 1.194},
        {'y': 2026, 'w': 45, 'h': 1, 'central': 1.160, 'lo': 1.123, 'hi': 1.197},
        {'y': 2026, 'w': 46, 'h': 2, 'central': 1.150, 'lo': 1.100, 'hi': 1.200}]},
        {'model_version': 'v1', 'trayectoria': [
        {'y': 2026, 'w': 44, 'h': 0, 'central': 1.200, 'lo': 1.190, 'hi': 1.210}]}]
    esp = {(2026, 44): 1.168, (2026, 45): 1.150}
    ev = {(r['version'], r['horizonte_sem']): r for r in P.eval_horizontes(cong, esp)}
    assert set(ev) == {('v1', 0), ('v1', 1)}                     # sin real -> no se evalúa
    assert ev[('v1', 0)]['n'] == 2 and ev[('v1', 0)]['cobertura_pct'] == 50
    assert ev[('v1', 0)]['mae_cts'] == pytest.approx((0.007 + 0.032) / 2 * 100, abs=0.06)


def test_congeladas_reales_bandas_de_la_semana_objetivo(P):
    """Documenta el efecto del fallo en los datos ya congelados (data/ no se toca): en todas las
    trayectorias existentes el punto h=0 tiene banda 0 y por eso su 'cobertura' en la web sale ~0%."""
    recs = P.cargar_congeladas()
    assert recs and all(r['trayectoria'][0]['lo'] == r['trayectoria'][0]['hi'] for r in recs)


# ---------------- predictor de peso de canal (con data/series_historicas.json) ----------------
def test_modelo_peso_signo_y_trayectoria(P):
    m = P.paso_modelo()
    pm = P.modelo_peso(P.cargar_series(), m['esp'], m['seas'])
    c = pm['ctx']
    t = c['traj']
    assert len(t) == 13 and len({(p['y'], p['w']) for p in t}) == 13
    a = c['anomalia_vs_normal_kg']
    # más ligero de lo normal -> menos oferta -> APOYO al precio (el bot llegó a invertirlo)
    if a < 0:
        assert 'LIGEROS' in c['lectura'] and 'APOYO' in c['lectura'] and c['sentido_efecto_precio'].startswith('al alza')
    else:
        assert 'PESADOS' in c['lectura'] and 'PRESIÓN' in c['lectura']
    # la corrección V2 va en €/kg vivo y tiene el signo contrario a la anomalía (beta<0)
    assert all(abs(v) < 0.2 for v in pm['corr'].values())
    assert c['ultimo']['rendimiento'] > 0.7 and c['ultimo']['vivo_kg'] > c['ultimo']['canal_kg']


def test_modelo_peso_pocos_datos(P):
    assert P.modelo_peso({'peso_semanal': {}}) is None
