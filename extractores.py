#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Extractores de TODAS las familias de PDF de Mercolleida.
Objetivo: que NADA de lo que manda Daniel se pierda. Cada familia -> datos estructurados
que alimentan (a) el contexto del chatbot, (b) la web y (c) candidatos de variable del modelo.

Cada funcion recibe el TEXTO ya extraido del PDF (para no abrir el fichero dos veces) y
devuelve un dict, o None si no aplica/no se pudo. Se verifica contra valores reales conocidos.
"""
import re, os, glob, datetime, unicodedata

NUM = re.compile(r'-?\d+(?:\.\d{3})*(?:,\d+)?')  # 1.763,00 / 5,26 / 310

def tf(s):
    """'1.763,00'->1763.0 ; '5,26'->5.26 ; '310'->310.0"""
    s = s.strip()
    if ',' in s:
        return float(s.replace('.', '').replace(',', '.'))
    # sin coma decimal: los puntos son miles
    return float(s.replace('.', ''))

def _nums(line):
    return [tf(x) for x in NUM.findall(line)]

def _norm(t):
    t = ''.join(c for c in unicodedata.normalize('NFD', t) if unicodedata.category(c) != 'Mn')
    return re.sub(r'\s+', ' ', t).lower()

MESES = {'enero':1,'febrero':2,'marzo':3,'abril':4,'mayo':5,'junio':6,'julio':7,
         'agosto':8,'septiembre':9,'setiembre':9,'octubre':10,'noviembre':11,'diciembre':12}

# ============ FECHA / SEMANA desde el nombre de fichero (AAMMDD) ============
def iso_de_nombre(fn):
    m = re.search(r'(\d{2})(\d{2})(\d{2})', fn)
    if not m: return None
    try:
        d = datetime.date(2000+int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except Exception:
        return None
    y, w, _ = d.isocalendar()
    return {'y': y, 'w': w, 'fecha': d.isoformat()}

# ============ SEMANAL: PRECIOS EUROPA (PE = Mercados Europeos) ============
def ex_precios_eu(t):
    """Precios de porcino de toda Europa. Combina la linea-resumen (vivo) con las cotizaciones por pais."""
    out = {}
    # Linea resumen: "España 1,32 - Alemania 1,13 - Francia 1,57 - Paises Bajos 1,06 - Dinamarca 0,83 - Bélgica 1,14"
    m = re.search(r'Espa.a\s+(\d,\d{2}).{0,80}?Alemania\s+(\d,\d{2}).{0,80}?Francia\s+(\d,\d{2})', t, re.S)
    if m:
        out['vivo_ue'] = {'espana': tf(m.group(1)), 'alemania': tf(m.group(2)), 'francia': tf(m.group(3))}
        for pais, pat in [('paises_bajos', r'Pa.ses Bajos\s+(\d,\d{2})'),
                          ('dinamarca', r'Dinamarca\s+(\d,\d{2})'),
                          ('belgica', r'B.lgica\s+(\d,\d{2})')]:
            mm = re.search(pat, t)
            if mm: out['vivo_ue'][pais.replace('_bajos', '_bajos')] = tf(mm.group(1))
    # Cotizaciones por pais (bases distintas; las guardamos con su referencia)
    porpais = {}
    for k, pat in [('italia_vivo_dop', r'CUN[^\n]*?Vivo[^\n]*?(\d,\d{3})'),
                   ('paises_bajos_canal', r'Vion[^\n]*?Canal[^\n]*?(\d,\d{2})'),
                   ('portugal_canal', r'Montijo[^\n]*?Canal[^\n]*?(\d,\d{3})'),
                   ('reino_unido_canal', r'SPP[^\n]*?Canal[^\n]*?(\d,\d{2})')]:
        mm = re.search(pat, t, re.I)
        if mm:
            try: porpais[k] = tf(mm.group(1))
            except Exception: pass
    if porpais: out['cotizaciones'] = porpais
    return out or None

# ============ SEMANAL: LECHON (LE) ============
def ex_lechon(t):
    """Precio base lechon nacional 20kg (valor de ESTA semana = 2a columna) + importacion NL."""
    out = {}
    m = re.search(r'Precio Base 20 ?kg\s+([\d.,]+)\s+([\d.,]+)', t)
    if not m:
        m = re.search(r'Precio Base Lleida 20 kilos\s+([\d.,]+)\s+([\d.,]+)', t)
    if m:
        try: out['nacional_20kg'] = tf(m.group(2))
        except Exception: pass
    mi = re.search(r'Contrato:\s*un origen\s+([\d.,]+)\s+([\d.,]+)', t)
    if mi:
        try: out['importacion_nl_contrato'] = tf(mi.group(2))
        except Exception: pass
    return out or None

# ============ SEMANAL: DESPIECE (DE) ============
def ex_despiece(t):
    """Precio base canal Barcelona + piezas clave al por mayor/fabricantes."""
    out = {}
    m = re.search(r'Precio base en Canal Clase 2.?\s*\(Rendimiento 76%\)\s+([\d.,]+)', t)
    if m:
        try: out['canal_base_bcn'] = tf(re.split(r'-', m.group(1))[0])
        except Exception: pass
    piezas = {}
    for nombre, etiqueta in [(r'Chuleta de Girona', 'chuleta_girona'), (r'Lomo ca.a', 'lomo_cana'),
                             (r'Costilla', 'costilla'), (r'Jam.n redondo clasificado', 'jamon_redondo'),
                             (r'Jam.n York', 'jamon_york'), (r'Panceta cuadro', 'panceta'),
                             (r'Bacon sin hueso', 'bacon'), (r'Magro 85/15', 'magro_85')]:
        mm = re.search(nombre + r'\s+([\d.,]+)', t)
        if mm:
            try:
                v = tf(re.split(r'-|R', mm.group(1))[0])
                if v > 0: piezas[etiqueta] = v
            except Exception: pass
    if piezas: out['piezas'] = piezas
    return out or None

# ============ SEMANAL: MERCADO ALEMAN (IP = Info-Porcino) ============
def ex_aleman(t):
    """Precios canal AMI (S/E/U/ponderado), cerda, y matanza alemana de la semana."""
    out = {}
    # Toma la ULTIMA columna de cada fila (semana mas reciente de las 3 que lista)
    for etiqueta, pat in [('canal_s', r'Canal S\s+([\d, ]+)'), ('canal_e', r'Canal E\s+([\d, ]+)'),
                          ('canal_u', r'Canal U\s+([\d, ]+)'), ('ponderado', r'Precio ponderado S-?P\s+([\d, ]+)'),
                          ('cerda_m', r'Cotizaci.n cerda M\s+([\d, ]+)')]:
        m = re.search(pat, t)
        if m:
            ns = _nums(m.group(1))
            if ns: out[etiqueta] = ns[-1]
    mm = re.search(r'Número de cerdos sacrificados\s+([\d. ]+)', t)
    if mm:
        ns = _nums(mm.group(1))
        if ns: out['cerdos_sacrificados'] = ns[-2] if len(ns) >= 2 and ns[-1] == ns[-1] else ns[-1]
    return out or None

# ============ MENSUAL: MATANZA ESPAÑA (peso canal + sacrificios) ============
def ex_matanza(t):
    """Tabla mensual: miles de cabezas | toneladas | peso medio canal, columnas por año.
    Devuelve series por (año, mes). El informe es acumulado -> el ultimo trae el año mas completo."""
    # Detectar los años de cabecera (3 bloques de 3 columnas: cabezas, toneladas, peso)
    mhead = re.search(r'Miles de cabezas.*?Toneladas.*?Peso medio canal\s*\n\s*((?:\d{4}\s*){3,})', t, re.S)
    anos = []
    if mhead:
        anos = [int(x) for x in re.findall(r'\d{4}', mhead.group(1))][:3]
    series = {}
    for mes, nm in MESES.items():
        mm = re.search(r'\n' + mes.capitalize() + r'\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)', t, re.I)
        if not mm or len(anos) < 3: continue
        g = [tf(x) for x in mm.groups()]
        # bloque1 = cabezas(3 años), bloque2 = toneladas(3), bloque3 = peso(3)
        for i, ano in enumerate(anos):
            series.setdefault(ano, {})[nm] = {'cabezas_miles': g[i], 'toneladas': g[3+i], 'peso_canal': g[6+i]}
    return {'anos': anos, 'series': series} if series else None

# ============ MENSUAL: CONSUMO ESPAÑA ============
def ex_consumo(t):
    """Consumo domestico de carne de cerdo FRESCO, miles de kilos, por (año, mes)."""
    # Toma la seccion FRESCO (antes de TRANSFORMADOS)
    seg = t.split('TRANSFORMADOS')[0]
    mhead = re.search(r'Miles de kilos.*?\n\s*((?:\d{4}\s*)+)', seg, re.S)
    anos = [int(x) for x in re.findall(r'\d{4}', mhead.group(1))] if mhead else []
    anos = [a for a in anos if 2000 <= a <= 2100][:3]
    series = {}
    for mes, nm in MESES.items():
        mm = re.search(r'\n' + mes.capitalize() + r'\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)', seg, re.I)
        if mm and len(anos) >= 3:
            g = [tf(x) for x in mm.groups()]
            for i, ano in enumerate(anos):
                series.setdefault(ano, {})[nm] = g[i]
    return {'anos': anos, 'series': series} if series else None

# ============ NARRATIVO (titular + cifra clave) para censo/china/export/usda/fao ============
def _primer_parrafo(t, maxlen=600):
    # Primer bloque de texto con sustancia (salta cabeceras de 1 linea)
    for bloque in re.split(r'\n\s*\n', t):
        b = re.sub(r'\s+', ' ', bloque).strip()
        if len(b) > 120:
            return b[:maxlen]
    return re.sub(r'\s+', ' ', t).strip()[:maxlen]

_BOILER = ('mercolleida', 'www.', 'página', 'pagina', 't. 973', 'información precios', '@', 'nif ', 'depósito legal')
def ex_titulo(t):
    for ln in t.split('\n'):
        s = ln.strip()
        low = s.lower()
        if len(s) > 15 and not any(b in low for b in _BOILER):
            return s
    return None

def periodo_de_titulo(titular):
    """De 'BALANCE ... EN ENERO-SEPTIEMBRE DE 2026' -> '2026-09' (año + mes final del periodo).
    Sirve para ordenar informes por fecha REAL del dato, no por nombre de fichero (que a veces no la lleva)."""
    if not titular: return ''
    n = _norm(titular)
    my = re.search(r'\b(20\d{2})\b', n)
    ano = my.group(1) if my else ''
    # mes final del periodo (coge el ultimo mes citado)
    mes = 0
    for nombre, num in MESES.items():
        for m in re.finditer(r'\b' + nombre + r'\b', n):
            mes = max(mes, num)
    if not ano:
        return ''
    return f'{ano}-{mes:02d}' if mes else f'{ano}-00'

def ex_narrativo(t):
    tit = ex_titulo(t)
    return {'titular': tit, 'resumen': _primer_parrafo(t), 'periodo': periodo_de_titulo(tit)} or None

# ============ Router por nombre de fichero ============
def familia(fn):
    n = _norm(fn)
    base = os.path.basename(n)
    if re.match(r'le\d', base): return 'lechon'
    if re.match(r'de\d', base): return 'despiece'
    if re.match(r'ip\d', base): return 'aleman'
    if re.match(r'pe\d', base): return 'precios_eu'
    if re.match(r'po\d', base): return 'cotizacion'   # ya lo trata el pipeline (precio España)
    if 'matanza de cerdos' in base: return 'matanza'
    if base.startswith('consumo espa'): return 'consumo'
    if 'censo porcino' in base or 'censo de reproductoras' in base: return 'censo'
    if 'avance importacion china' in base or 'importacion china' in base: return 'china_import'
    if 'balance exportacion' in base or 'avance exportacion' in base: return 'exportacion'
    if 'usda' in base: return 'usda'
    if base.startswith('previsiones fao') or 'fao-ocde' in base or 'fao para' in base: return 'fao'
    if 'balance importacion' in base: return 'importacion'
    if 'produccion porcina china' in base: return 'china_produccion'
    if base.startswith('bolet'): return 'boletin'
    if 'ppa' in base: return 'ppa'
    return None

EXTRACTOR = {
    'lechon': ex_lechon, 'despiece': ex_despiece, 'aleman': ex_aleman, 'precios_eu': ex_precios_eu,
    'matanza': ex_matanza, 'consumo': ex_consumo,
    'censo': ex_narrativo, 'china_import': ex_narrativo, 'exportacion': ex_narrativo,
    'usda': ex_narrativo, 'fao': ex_narrativo, 'importacion': ex_narrativo,
    'china_produccion': ex_narrativo,
}
