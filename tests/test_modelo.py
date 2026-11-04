# Modelo de precio (paso_modelo), registro, es_backtest y semana 53 / cambio de año.
import datetime, json, csv, math
import pytest
from conftest import maestro_sintetico, fijar_hoy


def test_modelo_con_datos_reales(P):
    m = P.paso_modelo()
    assert (m['last']['y'], m['last']['w']) == (2026, 44)
    assert (m['nexty'], m['nextw']) == (2026, 45)
    assert abs(m['pred'] - m['last']['v']) < 0.20            # una semana no mueve 20 cts
    assert m['mae_m'] < m['mae_n']                            # gana al ingenuo
    assert abs(sum(m['contrib'].values()) - m['delta_cts']) < 0.3   # las barras suman el total
    assert set(m['seas']) == {str(w) for w in range(1, 54)}
    assert m['seas']['53'] == m['seas']['52']                  # la 53 (pocos años) hereda la 52
    assert m['serie'][-1] == {'y': 2026, 'w': 44, 'v': 1.168}


@pytest.mark.parametrize('hasta,siguiente', [((2026, 52), (2026, 53)), ((2026, 53), (2027, 1)),
                                             ((2025, 52), (2026, 1)), ((2026, 30), (2026, 31))])
def test_semana_siguiente_semana_53(P, monkeypatch, hasta, siguiente):
    maestro_sintetico(P.MASTER, hasta=hasta)
    fijar_hoy(monkeypatch, P, hasta[0], 12, 31)
    m = P.paso_modelo()
    assert (m['last']['y'], m['last']['w']) == hasta
    assert (m['nexty'], m['nextw']) == siguiente


def test_semana_53_entra_en_el_modelo(P, monkeypatch):
    maestro_sintetico(P.MASTER, hasta=(2027, 2))
    fijar_hoy(monkeypatch, P, 2027, 1, 14)
    m = P.paso_modelo()
    assert (2026, 53) in m['esp'] and (2020, 53) in m['esp']
    assert (2025, 53) not in m['esp']                          # 2025 no tiene semana 53
    ws = [(r['y'], r['w']) for r in m['serie']]
    assert ws[-3:] == [(2026, 53), (2027, 1), (2027, 2)]


def test_semana_1_publicada_en_diciembre_no_se_ignora(P, monkeypatch):
    """La semana ISO 1 de 2026 empieza el lunes 29-dic-2025 (y el jueves 1-ene es festivo, la lonja
    puede adelantarse al miércoles 31-dic). Si ese dato ya está en el maestro, el modelo debe usarlo:
    antes solo leía años <= año natural de hoy y volvía a 'predecir' una semana ya publicada."""
    maestro_sintetico(P.MASTER, hasta=(2026, 1))
    fijar_hoy(monkeypatch, P, 2025, 12, 31)
    m = P.paso_modelo()
    assert (m['last']['y'], m['last']['w']) == (2026, 1)
    assert (m['nexty'], m['nextw']) == (2026, 2)


def test_modelo_huecos_sin_romper(P, monkeypatch):
    maestro_sintetico(P.MASTER, hasta=(2026, 20), sin={(2026, 10), (2024, 5)})
    fijar_hoy(monkeypatch, P, 2026, 5, 20)
    m = P.paso_modelo()
    assert (2026, 10) not in m['esp'] and (m['nexty'], m['nextw']) == (2026, 21)


@pytest.mark.xfail(strict=True, reason='DISEÑO (no arreglado): la estacionalidad que usa el backtest se calcula con '
                   'TODOS los años, incluidos los posteriores a la semana evaluada (fuga leve). Ver INFORME_NUBE.md')
def test_backtest_sin_mirar_al_futuro(P, monkeypatch):
    # El backtest evalúa 2019..hoy con 'seas'. Si no mirase al futuro, añadir 2025 no cambiaría la
    # estacionalidad con la que se evaluaron las semanas de 2019-2024.
    maestro_sintetico(P.MASTER, hasta=(2024, 52))
    fijar_hoy(monkeypatch, P, 2024, 12, 30)
    s2024 = P.paso_modelo()['seas']
    maestro_sintetico(P.MASTER, hasta=(2025, 52))
    import openpyxl
    wb = openpyxl.load_workbook(P.MASTER)
    for w in range(1, 53): wb['CEBADO LLEIDA'].cell(8 + w, 2 + 15).value = round(1.0 + 0.3 * math.sin(w), 3)
    wb.save(P.MASTER)
    fijar_hoy(monkeypatch, P, 2025, 12, 30)
    s2025 = P.paso_modelo()['seas']
    assert s2024 == s2025, 'la estacionalidad usada para evaluar 2019-2024 depende de datos de 2025'


# ---------------- registro / es_backtest ----------------
@pytest.mark.parametrize('fecha,esperado', [
    ('2026-10-28', False),   # miércoles antes de publicar -> en vivo
    ('2026-10-29', True),    # jueves de publicación -> backtest
    ('2026-11-02', True),
    ('2026-10-26', False),   # lunes (antes el corte era el lunes)
])
def test_es_backtest_corte_jueves(P, fecha, esperado):
    assert P.es_backtest({'anio': '2026', 'semana': '44', 'fecha_pred': fecha}) is esperado


def test_es_backtest_semana_53_y_datos_raros(P):
    assert P.es_backtest({'anio': 2026, 'semana': 53, 'fecha_pred': '2026-12-30'}) is False
    assert P.es_backtest({'anio': 2026, 'semana': 53, 'fecha_pred': '2026-12-31'}) is True
    assert P.es_backtest({'anio': 2026, 'semana': 'x', 'fecha_pred': '2026-12-31'}) is False
    assert P.es_backtest({}) is False


def test_registro_rellena_real_y_no_duplica(P, monkeypatch):
    fijar_hoy(monkeypatch, P, 2026, 11, 3)
    m = P.paso_modelo()
    h1 = P.paso_registro(m)
    h2 = P.paso_registro(m)
    assert h1 == h2
    claves = [(int(r['anio']), int(r['semana'])) for r in h2]
    assert len(claves) == len(set(claves))
    r44 = [r for r in h2 if r['semana'] in ('44', 44)][0]
    assert r44['real'] == '1.168' and r44['error_cts'] == '0.7'
    # un real nuevo se apunta solo
    m2 = dict(m); m2['esp'] = dict(m['esp']); m2['esp'][(2026, 45)] = 1.150
    h3 = P.paso_registro(m2)
    r45 = [r for r in h3 if str(r['semana']) == '45'][0]
    assert r45['real'] == '1.150' and r45['error_cts'] == '1.3'
    assert r45['fecha_pred'] == '2026-10-29'                   # la primera predicción manda
