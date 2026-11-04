# Contexto de mercado: series desde PDFs, PPA, peso semanal (semana 53) y contexto para el chatbot.
import json, os
import pytest
from conftest import escribir_pdf, fijar_hoy

PPA_OK = """RESUMEN SEMANAL PPA Nº 45
Esta semana se han notificado 3 NUEVOS casos en jabalíes.
412 1.234 3 NUEVOS
Sin positivos en porcino doméstico. Sin cambios en el comercio exterior.
"""
PPA_RARO = "Documento PPA\nTabla de anexos\nVer mapa adjunto\n"   # texto que no trae ninguna señal conocida


def test_ctx_ppa_informe_normal(P):
    r = P.ctx_ppa(PPA_OK)
    assert r['semana_informe'] == 45 and r['casos_nuevos_jabali'] == 3
    assert r['sin_positivos_domestico'] is True and r['sin_cambios_comercio_exterior'] is True
    assert r['resumen'].startswith('3 casos nuevos') and 'SIN positivos en cerdo doméstico' in r['resumen']
    assert P.ctx_ppa('') is None


def test_ctx_ppa_no_reconocido_no_dice_que_hay_granjas(P):
    """Un informe PPA cuyo texto no trae NINGUNA señal conocida (ni nº de semana, ni casos, ni las frases
    clave) se leía como sin_positivos_domestico=False -> la web pintaba '¿En granjas? SÍ ⚠️' y el bot recibía
    que hay positivos en cerdo doméstico. Es lo que hay ahora mismo en data/contexto_mercado.json."""
    r = P.ctx_ppa(PPA_RARO)
    assert r['sin_positivos_domestico'] is None
    assert r['sin_cambios_comercio_exterior'] is None
    assert 'no se ha podido leer' in r['resumen'].lower()


def test_contexto_actual_sin_falsa_alarma_ppa(P):
    # data/ no se modifica: solo comprobamos que el síntoma existe en los datos reales de hoy
    # Corregido en produccion: un informe no leido NUNCA puede quedar como 'hay positivos en granjas' (False)
    c = json.load(open(P.CTX_FILE))
    assert not (c['ppa'].get('semana_informe') is None and c['ppa'].get('sin_positivos_domestico') is False)


def test_ppa_historico_cambio_de_anio(P, monkeypatch):
    """El histórico PPA se deduplicaba solo por nº de semana: en 2027 la semana 44 'ya existía' (la de 2026)
    y no se apuntaba; además se ordenaba por semana mezclando años."""
    json.dump({'ppa_historico': [{'semana': 44, 'fecha': '2026-10-26', 'casos_nuevos': 3, 'acumulados': None, 'domestico': 0},
                                 {'semana': 52, 'fecha': '2026-12-21', 'casos_nuevos': 2, 'acumulados': None, 'domestico': 0}]},
              open(P.CTX_FILE, 'w'))
    escribir_pdf(P.PDFS, 'Resumen semanal PPA.pdf', PPA_OK.replace('Nº 45', 'Nº 44'))
    fijar_hoy(monkeypatch, P, 2027, 11, 2)
    ctx = P.paso_contexto()
    h = ctx['ppa_historico']
    assert [(x.get('anio'), x['semana']) for x in h] == [(2026, 44), (2026, 52), (2027, 44)]


def test_ppa_historico_informe_de_semana_52_leido_en_enero(P, monkeypatch):
    json.dump({'ppa_historico': []}, open(P.CTX_FILE, 'w'))
    escribir_pdf(P.PDFS, 'Resumen semanal PPA.pdf', PPA_OK.replace('Nº 45', 'Nº 52'))
    fijar_hoy(monkeypatch, P, 2027, 1, 5)
    h = P.paso_contexto()['ppa_historico']
    assert [(x['anio'], x['semana']) for x in h] == [(2026, 52)]


def test_ppa_elige_el_informe_mas_reciente(P, monkeypatch):
    json.dump({}, open(P.CTX_FILE, 'w'))
    escribir_pdf(P.PDFS, 'Actualizacion PPA.pdf', PPA_OK.replace('Nº 45', 'Nº 46').replace('3 NUEVOS', '5 NUEVOS'))
    escribir_pdf(P.PDFS, 'Resumen semanal PPA.pdf', PPA_OK)
    fijar_hoy(monkeypatch, P, 2026, 11, 12)
    ctx = P.paso_contexto()
    assert ctx['ppa']['semana_informe'] == 46 and ctx['ppa']['casos_nuevos_jabali'] == 5


PO_PESOS = ("COTIZACIÓN\nCerdo Blanco 1,168 1,137 -0,031\n"
            "PESOS MEDIOS\nMataderos (kg canal) 95,10 96,20 94,00 93,50 92,80 91,00 95,76 96,30 94,10 93,70 92,90 91,20\n"
            "Productores (kg vivo) 122,00 123,10 121,00 120,50 119,80 118,00 122,70 123,20 121,30 120,60 119,90 118,20\n")


def test_peso_semanal_no_inventa_semanas_53(P):
    """El PO de la semana 53 de 2026 trae la columna de 'la misma semana' de 5 años previos. 2025, 2024,
    2023, 2022 y 2021 no tienen semana 53 -> no pueden crearse claves '2025-W53' etc. (sí '2020-W53')."""
    json.dump({'semanal': {}, 'mensual': {}, 'narrativo': {}}, open(P.SERIES_FILE, 'w'))
    escribir_pdf(P.PDFS, 'PO261231.pdf', PO_PESOS)          # jueves 31-dic-2026 = 2026-W53
    escribir_pdf(P.PDFS, 'PO270107.pdf', PO_PESOS)          # 2027-W1: 'anterior' = 2026-W53
    s = P.actualizar_series()
    claves = set(s['peso_semanal'])
    assert '2026-W53' in claves
    for y in (2021, 2022, 2023, 2024, 2025):
        assert f'{y}-W53' not in claves
    assert '2027-W01' in claves and '2026-W01' in claves


def test_peso_semanal_el_po_mas_reciente_manda(P):
    json.dump({'semanal': {}, 'mensual': {}, 'narrativo': {}}, open(P.SERIES_FILE, 'w'))
    escribir_pdf(P.PDFS, 'PO261029.pdf', PO_PESOS)
    escribir_pdf(P.PDFS, 'PO261105.pdf', PO_PESOS.replace('95,10', '95,55'))   # revisa la semana 44
    s = P.actualizar_series()
    assert s['peso_semanal']['2026-W44']['canal'] == 95.55
    assert s['peso_semanal']['2026-W45']['canal'] == 95.76


def test_actualizar_series_todas_las_familias(P, monkeypatch):
    fijar_hoy(monkeypatch, P, 2026, 11, 3)
    json.dump({'semanal': {}, 'mensual': {}, 'narrativo': {}}, open(P.SERIES_FILE, 'w'))
    escribir_pdf(P.PDFS, 'LE261103.pdf', 'Precio Base 20 kg 24,50 25,00')
    escribir_pdf(P.PDFS, 'PE261029.pdf', 'España 1,17 - Alemania 1,02 - Francia 1,33')
    escribir_pdf(P.PDFS, 'Boletin.pdf', 'SEMANA 45 / 2026\nMaíz nacional Disponible 205,00 207,00\n')
    escribir_pdf(P.PDFS, 'Exportacion Espana.pdf', 'BALANCE DE EXPORTACIÓN ENERO-AGOSTO 2026\n\n' + 'dato ' * 40)
    escribir_pdf(P.PDFS, 'Previsiones USDA.pdf', '')                  # PDF-imagen: sin texto -> no entra
    escribir_pdf(P.PDFS, 'factura.pdf', 'nada')                       # familia desconocida -> se ignora
    s = P.actualizar_series()
    assert s['semanal']['lechon']['2026-W45']['nacional_20kg'] == 25.0
    assert s['semanal']['precios_eu']['2026-W44']['vivo_ue']['espana'] == 1.17
    assert s['semanal']['cereales']['2026-W45']['maiz'] == 207.0
    assert s['narrativo']['exportacion'][-1]['periodo'] == '2026-08'
    assert 'usda' not in s['narrativo']
    # segunda pasada: no duplica los narrativos
    s2 = P.actualizar_series()
    assert len(s2['narrativo']['exportacion']) == 1


def test_contexto_rico_con_series_reales(P):
    ctx = P.construir_contexto_rico(P.cargar_series(), {'resumen': 'x'}, [{'semana': 1}])
    for k in ('europa', 'lechon', 'despiece', 'aleman', 'cereales', 'peso_canal_es', 'consumo_es', 'fundamentales', 'ppa'):
        assert k in ctx, k
    assert ctx['europa']['tendencia_espana'][-1]['sem'] >= ctx['europa']['tendencia_espana'][0]['sem']
    assert len(ctx['lechon']['tendencia']) <= 12
    assert P.construir_contexto_rico({}) == {}
