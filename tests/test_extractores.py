# Pruebas de extractores.py con textos de ejemplo (como los devuelve pdfplumber).
import pytest


def test_tf_formato_espanol(EX):
    assert EX.tf('1.763,00') == 1763.0
    assert EX.tf('5,26') == 5.26
    assert EX.tf('310') == 310.0
    assert EX.tf('1.234.567') == 1234567.0


def test_iso_de_nombre_cambio_de_anio_y_semana_53(EX):
    assert EX.iso_de_nombre('PO261029.pdf') == {'y': 2026, 'w': 44, 'fecha': '2026-10-29'}
    # 31-dic-2026 (jueves) es la semana 53 de 2026; el 1-ene-2027 sigue siendo la 53 de 2026
    assert EX.iso_de_nombre('PO261231.pdf')['w'] == 53
    assert (EX.iso_de_nombre('DE270101.pdf')['y'], EX.iso_de_nombre('DE270101.pdf')['w']) == (2026, 53)
    # 29-dic-2025 (lunes) ya es la semana 1 de 2026
    assert (EX.iso_de_nombre('LE251229.pdf')['y'], EX.iso_de_nombre('LE251229.pdf')['w']) == (2026, 1)
    assert EX.iso_de_nombre('PO261340.pdf') is None   # fecha imposible
    assert EX.iso_de_nombre('Boletin.pdf') is None


EU = """MERCADOS EUROPEOS
España 1,17 - Alemania 1,02 - Francia 1,33 - Paises Bajos 0,97 - Dinamarca 0,89 - Bélgica 1,08
Italia CUN Vivo 160-176 kg 1,915 0,000
Países Bajos Vion Canal 1,11 -0,02
Portugal Montijo Canal 1,83 0,00
Reino Unido SPP Canal 2,11 -0,01
"""

def test_ex_precios_eu(EX):
    r = EX.ex_precios_eu(EU)
    assert r['vivo_ue'] == {'espana': 1.17, 'alemania': 1.02, 'francia': 1.33,
                            'paises_bajos': 0.97, 'dinamarca': 0.89, 'belgica': 1.08}
    assert r['cotizaciones'] == {'italia_vivo_dop': 1.915, 'paises_bajos_canal': 1.11,
                                 'portugal_canal': 1.83, 'reino_unido_canal': 2.11}
    assert EX.ex_precios_eu('texto sin precios') is None


def test_ex_lechon(EX):
    t = "LECHONES\nPrecio Base 20 kg 26,00 24,50 -1,50\nContrato: un origen 49,00 48,50\n"
    assert EX.ex_lechon(t) == {'nacional_20kg': 24.5, 'importacion_nl_contrato': 48.5}
    t2 = "Precio Base Lleida 20 kilos 26,00 24,50"
    assert EX.ex_lechon(t2) == {'nacional_20kg': 24.5}
    assert EX.ex_lechon('nada') is None


def test_ex_despiece(EX):
    t = ("Precio base en Canal Clase 2 (Rendimiento 76%) 1,537 1,560\n"
         "Chuleta de Girona 5,16 R\nLomo caña 6,02\nCostilla 6,95\nJamón redondo clasificado 2,74\n"
         "Jamón York 2,33\nPanceta cuadro 2,39\nBacon sin hueso 2,82\n")
    r = EX.ex_despiece(t)
    assert r['canal_base_bcn'] == 1.537
    assert r['piezas'] == {'chuleta_girona': 5.16, 'lomo_cana': 6.02, 'costilla': 6.95, 'jamon_redondo': 2.74,
                           'jamon_york': 2.33, 'panceta': 2.39, 'bacon': 2.82}


def test_ex_aleman_toma_ultima_columna(EX):
    t = ("Canal S 1,50 1,45 1,45\nCanal E 1,48 1,43 1,43\nCanal U 1,38 1,33 1,33\n"
         "Precio ponderado S-P 1,49 1,44 1,44\nCotización cerda M 0,95 0,90 0,90\n"
         "Número de cerdos sacrificados 812.345 798.123 --\n")
    r = EX.ex_aleman(t)
    assert r['canal_s'] == 1.45 and r['ponderado'] == 1.44 and r['cerda_m'] == 0.9
    assert r['cerdos_sacrificados'] == 798123.0     # la ultima con dato ('--' se ignora)


def test_ex_cereales_semana_del_contenido(EX):
    t = ("BOLETÍN SEMANA 44 / 2026\n"
         "Trigo forrajero nacional Disponible 210,00 212,00\n"
         "Cebada PE 62 nacional Disponible 195,00 196,50\n"
         "Maíz nacional Disponible 205,00 207,00\n"
         "Harina soja importación 47% 330,00 335,00\n"
         "Colza en grano 450,00 455,00\n"
         "Cordero de 19 a 23 kg 7,10\nAñojo R 6,20\nTernera R 6,40\nVaca R 4,10\n")
    r = EX.ex_cereales(t)
    assert (r['semana'], r['anio']) == (44, 2026)
    assert r['maiz'] == 207.0 and r['harina_soja'] == 335.0 and r['trigo_forrajero'] == 212.0
    assert r['cordero_19_23kg'] == 7.1 and r['vacuno_vaca_R'] == 4.1


def test_ex_pesos_doce_valores(EX):
    t = ("PESOS MEDIOS\nMataderos (kg canal) 95,10 96,20 94,00 93,50 92,80 91,00 95,76 96,30 94,10 93,70 92,90 91,20\n"
         "Productores (kg vivo) 122,00 123,10 121,00 120,50 119,80 118,00 122,70 123,20 121,30 120,60 119,90 118,20\n")
    r = EX.ex_pesos(t)
    assert r['canal']['act'][0] == 95.76 and r['canal']['ant'][0] == 95.10
    assert len(r['vivo']['act']) == 6
    # formato raro (11 valores) -> no se inventa nada
    assert EX.ex_pesos("Mataderos (kg canal) 95,10 96,20") is None


MATANZA = """MATANZA DE CERDOS
Miles de cabezas Toneladas Peso medio canal
2024 2025 2026 2024 2025 2026 2024 2025 2026
Enero 4.400 4.500 4.300 412.000,50 425.000,00 410.000,00 93,60 94,40 95,30
Febrero 4.100 4.200 4.000 383.000 395.000 381.000 93,40 94,05 95,20
"""

def test_ex_matanza(EX):
    r = EX.ex_matanza(MATANZA)
    assert r['anos'] == [2024, 2025, 2026]
    assert r['series'][2026][1] == {'cabezas_miles': 4300.0, 'toneladas': 410000.0, 'peso_canal': 95.3}
    assert r['series'][2025][2]['peso_canal'] == 94.05


def test_ex_consumo_solo_fresco(EX):
    t = ("CONSUMO DE CARNE DE CERDO FRESCA Y TRANSFORMADOS\nMiles de kilos\n2024 2025 2026\n"
         "Enero 40.000 41.000 39.500\n"
         "DE TRANSFORMADOS\nMiles de kilos\n2024 2025 2026\nEnero 99.999 99.999 99.999\n")
    r = EX.ex_consumo(t)
    assert r['series'][2026][1] == 39500.0


def test_ex_narrativo_y_titulo(EX):
    t = "www.mercolleida.com\nBALANCE DE EXPORTACIÓN DE ENERO-SEPTIEMBRE DE 2026\n\n" + ("texto " * 40)
    r = EX.ex_narrativo(t)
    assert r['titular'].startswith('BALANCE DE EXPORTACIÓN')
    assert r['periodo'] == '2026-09'
    vacio = EX.ex_narrativo('')
    assert not (vacio.get('titular') or vacio.get('resumen'))   # el pipeline lo descarta


@pytest.mark.parametrize('titular,esperado', [
    ('BALANCE ... EN ENERO-SEPTIEMBRE DE 2026', '2026-09'),
    ('Previsiones UE para 2026 (Otoño)', '2026-00'),
    ('', ''),
    ('Sin año', ''),
    # periodo que cruza el año: el final del periodo es marzo de 2026, no octubre de 2025
    ('IMPORTACIONES DE OCTUBRE 2025 A MARZO 2026', '2026-03'),
    ('CENSO DE DICIEMBRE 2025 - ENERO 2026', '2026-01'),
    ('ENERO-SEPTIEMBRE 2026 FRENTE A ENERO-SEPTIEMBRE 2025', '2026-09'),
])
def test_periodo_de_titulo(EX, titular, esperado):
    assert EX.periodo_de_titulo(titular) == esperado


@pytest.mark.parametrize('nombre,fam', [
    ('PO261029.pdf', 'cotizacion'), ('DE261102.pdf', 'despiece'), ('IP261028.pdf', 'aleman'),
    ('LE261027.pdf', 'lechon'), ('PE261029.pdf', 'precios_eu'),
    ('Matanza de cerdos en España.pdf', 'matanza'),
    ('Consumo España carne cerdo.pdf', 'consumo'), ('Consumo Espana carne cerdo.pdf', 'consumo'),
    ('Consumo Espa_a carne cerdo.pdf', 'consumo'),
    ('Censo porcino mayo 2026.pdf', 'censo'), ('Censos Porcinos de la UE.pdf', 'censo'),
    ('Balance importación China.pdf', 'china_import'), ('Balance importaci_n China.pdf', 'china_import'),
    ('Exportación España.pdf', 'exportacion'), ('Exportaci_n Espa_a.pdf', 'exportacion'),
    ('Previsiones USDA.pdf', 'usda'), ('Previsiones FAO 2026.pdf', 'fao'),
    ('Balance importación UE.pdf', 'importacion'),
    ('Previsiones UE para 2026 _Otono_.pdf', 'previsiones_ue'),
    ('Producción porcina China.pdf', 'china_produccion'), ('Producci_n porcina China.pdf', 'china_produccion'),
    ('Boletín.pdf', 'cereales'), ('Bolet_n.pdf', 'cereales'), ('Boletin.pdf', 'cereales'),
    ('Cómo empezamos la semana 45.pdf', 'apertura_semana'), ('Como empezamos la semana 45.pdf', 'apertura_semana'),
    ('_Como empezamos la semana 45_.pdf', 'apertura_semana'),
    ('C_mo empezamos la semana 45.pdf', 'apertura_semana'),   # resto del fallo del '_' en lugar de acento
    ('Encuesta precios.pdf', 'otros_informes'),
    ('Resumen semanal PPA.pdf', 'ppa'), ('Actualizacion PPA.pdf', 'ppa'),
    ('factura.pdf', None),
])
def test_familia(EX, nombre, fam):
    assert EX.familia(nombre) == fam


def test_toda_familia_con_extractor(EX):
    # toda familia que devuelve el router (salvo las que trata el pipeline aparte) tiene extractor
    fams = {'lechon', 'despiece', 'aleman', 'precios_eu', 'matanza', 'consumo', 'censo', 'china_import',
            'exportacion', 'usda', 'fao', 'importacion', 'previsiones_ue', 'china_produccion', 'cereales',
            'apertura_semana', 'otros_informes'}
    assert fams <= set(EX.EXTRACTOR)
