#!/usr/bin/env python3
# PIPELINE NUBE - Grupo Jorge / precio cerdo Lonja de Lleida
# correo -> PDFs -> Excel maestro -> modelo -> prediccion -> registro -> web cifrada
import os, re, csv, json, base64, datetime, statistics as st, glob, imaplib, email
from email.header import decode_header
import pdfplumber, openpyxl
import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import extractores as EX  # extractores de TODAS las familias de PDF (lechon, despiece, aleman, europa, matanza, consumo, censo, china, export, usda...)
from openpyxl.styles import PatternFill
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

HERE=os.path.dirname(os.path.abspath(__file__))
PDFS=os.path.join(HERE,'_pdfs')
MASTER=os.path.join(HERE,'data','master.xlsx')
LOG=os.path.join(HERE,'data','validacion_modelo.csv')
VISTOS=os.path.join(HERE,'data','correos_vistos.txt')  # Message-IDs ya procesados (persiste en el repo -> no re-descargar correos = no OVERQUOTA)
CTX_FILE=os.path.join(HERE,'data','contexto_mercado.json')  # contexto rico persistido (Europa/PPA/lechon) para el bot
CONG_FILE=os.path.join(HERE,'data','predicciones_congeladas.jsonl')  # registro INMUTABLE de trayectorias a medio plazo (para evaluar a 1/4/6 meses)
SERIES_FILE=os.path.join(HERE,'data','series_historicas.json')  # TODAS las series extraidas de TODOS los PDFs (se alimenta cada dia)
ENC_OUT=os.path.join(HERE,'docs','data.enc.js')
# --- Versionado del modelo (lo que la reunion llama "V1"). Cada cambio de variables = nueva version ---
MODEL_VERSION='v1'
MODEL_FEATURES='inercia España 3 semanas + delta Francia + delta Alemania + estacionalidad'
# Banda CALIBRADA con el backtest real (P80 del error absoluto por horizonte, semi-ancho en €).
# "El 80% de las veces el precio real cayó dentro de ±este valor". Mucho más honesta que una fórmula inventada.
BANDA_CAL=[0.0,0.019,0.037,0.05,0.069,0.084,0.097,0.111,0.123,0.136,0.151,0.163,0.175,0.186,0.199,0.209,0.221,0.228,0.235,0.247,0.259,0.272,0.276,0.29,0.297,0.308,0.314]
NUM=re.compile(r'-?\d+,\d+')
def tf(s): return float(s.replace('.','').replace(',','.'))
def nbe(l): return [tf(x) for x in NUM.findall(re.split(r'€',l)[0])]

# ---------- 1) CORREO (ultimos ~30 dias) ----------
def paso_correo():
    user=os.environ.get('GMAIL_USER'); pw=os.environ.get('GMAIL_APP_PASSWORD')
    os.makedirs(PDFS,exist_ok=True)
    if not user or not pw:
        print('  (sin credenciales de correo, uso lo que haya en disco)'); return 0,[]
    # Manifiesto de correos ya procesados: evita re-descargar mensajes en cada run (causa del OVERQUOTA de Gmail).
    vistos=set()
    if os.path.exists(VISTOS):
        try: vistos=set(l.strip() for l in open(VISTOS) if l.strip())
        except: vistos=set()
    since=(datetime.date.today()-datetime.timedelta(days=12)).strftime('%d-%b-%Y')  # ventana acotada: el historico ya esta en master.xlsx
    M=imaplib.IMAP4_SSL('imap.gmail.com'); M.login(user,pw); M.select('INBOX')
    typ,data=M.search(None,f'(SINCE {since})'); ids=data[0].split()
    saved=0; nombres=[]; nuevos=[]; en_ventana=[]
    for i in ids:
        # 1) Solo la cabecera Message-ID (ligero, NO descarga adjuntos)
        typ,hd=M.fetch(i,'(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])')
        mid=''
        try:
            if hd and hd[0] and hd[0][1]: mid=(email.message_from_bytes(hd[0][1]).get('Message-ID') or '').strip()
        except: mid=''
        if mid: en_ventana.append(mid)
        if mid and mid in vistos:
            continue  # ya procesado antes -> no bajamos el mensaje completo
        # 2) Mensaje nuevo: ahora si lo descargamos entero y sacamos los PDFs
        typ,md=M.fetch(i,'(RFC822)'); msg=email.message_from_bytes(md[0][1])
        for part in msg.walk():
            if part.get_content_maintype()=='multipart': continue
            fn=part.get_filename()
            if not fn or not fn.lower().endswith('.pdf'): continue
            parts=decode_header(fn); nm=''
            for txt,enc in parts: nm+= txt.decode(enc or 'utf-8','ignore') if isinstance(txt,bytes) else txt
            nm=re.sub(r'[^A-Za-z0-9._\- ]','_',nm).strip() or 'adj.pdf'
            path=os.path.join(PDFS,nm)
            if os.path.exists(path): continue
            with open(path,'wb') as f: f.write(part.get_payload(decode=True)); saved+=1; nombres.append(nm)
        if mid: nuevos.append(mid)
    M.logout()
    # Guardar manifiesto: lo ya visto que sigue en ventana + lo nuevo (evita que crezca sin fin y que "olvide" lo de la ventana).
    if nuevos:
        actualizado=[m for m in vistos if m in set(en_ventana)]+nuevos
        try:
            with open(VISTOS,'w') as f: f.write('\n'.join(actualizado[-3000:])+'\n')
        except Exception as e: print('  (aviso: no se pudo guardar manifiesto:',e,')')
    print(f'  PDFs nuevos: {saved} | mensajes nuevos: {len(nuevos)} | ya vistos en ventana: {len(en_ventana)-len(nuevos)}')
    return saved,nombres

# ---------- extraccion ----------
def isoweek(fn,pfx):
    m=re.match(pfx+r'(\d{2})(\d{2})(\d{2})',fn)
    if not m: return None
    try: d=datetime.date(2000+int(m.group(1)),int(m.group(2)),int(m.group(3)))
    except: return None
    return d.isocalendar()[:2]
def index_files(pfx):
    # Indexa por (año, semana) para TODOS los años (antes estaba clavado a 2026 -> el sistema
    # dejaba de actualizarse el 1-ene-2027). Ahora es agnóstico al año.
    out={}
    for f in glob.glob(os.path.join(PDFS,pfx+'[0-9]*.pdf')):
        yw=isoweek(os.path.basename(f),pfx)
        if yw and yw not in out: out[yw]=f
    return out
# --- Mapeo año -> columna del maestro (CEBADO/ALEMANIA/DINAMARCA: 1 col/año desde 2010 en col 2;
#     FRANCIA: 2 cols/año, el valor va en la segunda). Autoexamen: para 2026 dan 18 y 35, los de siempre. ---
def _col_anual(y): return 2+(y-2010)           # CEBADO LLEIDA / ALEMANIA / DINAMARCA (valor)
def _col_fra_hdr(y): return 2+2*(y-2010)       # FRANCIA (cabecera del año)
def _col_fra_val(y): return 2+2*(y-2010)+1     # FRANCIA (valor)
def _ensure_col(ws,col,header_row,year):
    # Garantiza que exista la columna del año (crea la cabecera si falta). openpyxl amplía el ancho
    # al escribir, así el 1-ene del año nuevo la columna se crea sola en vez de que todo deje de funcionar.
    if ws.cell(header_row,col).value != year:
        ws.cell(header_row,col).value=year
    return col
def esp_de_po(path):
    # Objetivo = precio REAL de la Lonja de Lleida ("Cerdo Blanco", valor de esta semana).
    try:
        with pdfplumber.open(path) as pdf: allt='\n'.join((p.extract_text() or '') for p in pdf.pages)
    except: return None
    for l in allt.split('\n'):
        s=l.strip().lower()
        if s.startswith('cerdo blanco') or s.startswith('cerdo cebado'):
            ns=NUM.findall(l)
            if len(ns)>=2: return tf(ns[1])
    for l in allt.split('\n'):  # fallback: equivalente España
        if l.strip().startswith('España'):
            ns=NUM.findall(l)
            if len(ns)>=12: return tf(ns[6])
    return None
def ale_fra_de_de(path):
    try:
        with pdfplumber.open(path) as pdf: t=pdf.pages[0].extract_text() or ''
    except: return None,None
    ale=fra=None
    for l in t.split('\n'):
        u=l.upper()
        if 'NW-AMI' in u and 'CANAL' in u:
            nb=nbe(l); ale=nb[-1] if nb else ale
        if l.strip().upper().startswith('FRANCIA') and ('MPB' in u or 'MPF' in u):
            nb=nbe(l); fra=nb[-1] if nb else fra
    return ale,fra
def din_de_ip(path):
    try:
        with pdfplumber.open(path) as pdf: t=pdf.pages[0].extract_text() or ''
    except: return None
    if 'EUROS KILO VIVO' not in t.upper(): return None
    for l in t.split('\n'):
        if l.strip().startswith('Dinamarca'):
            ns=NUM.findall(l)
            if len(ns)>=2: return tf(ns[0])
    return None
def cebado_de_de(path):
    # Lee el "Cerdo cebado" (precio vivo Lleida) que aparece en el informe de despiece, solo para mostrarlo.
    try:
        with pdfplumber.open(path) as pdf: t='\n'.join((p.extract_text() or '') for p in pdf.pages)
    except: return None
    for l in t.split('\n'):
        if l.strip().lower().startswith('cerdo cebado'):
            ns=NUM.findall(l)
            if ns: return tf(ns[0])
    return None

# ---------- CONTEXTO DE MERCADO (señales ricas que antes se tiraban: Europa, PPA, lechón) ----------
import unicodedata
def _latest_pdf(pat):
    fs=sorted(glob.glob(os.path.join(PDFS,pat)))
    return fs[-1] if fs else None
def _pdftxt(path):
    if not path: return ''
    try:
        with pdfplumber.open(path) as pdf: return '\n'.join((p.extract_text() or '') for p in pdf.pages)
    except Exception: return ''
def ctx_europa(t):
    # Precios de porcino de TODA Europa (del PDF "Mercados Europeos"), no solo Francia/Alemania/Dinamarca.
    out={}
    pats={'italia':r'CUN.*?(\d,\d{3})','paises_bajos':r'Vion.*?(\d,\d{2})','belgica':r'B[eé]lgica.*?Vivo\s+(\d,\d+)',
          'portugal':r'Montijo.*?(\d,\d{3})','reino_unido':r'SPP.*?(\d,\d{2})','polonia':r'Polonia.*?Vivo\s+(\d,\d+)'}
    for k,pat in pats.items():
        m=re.search(pat,t,re.I|re.S)
        if m:
            try: out[k]=tf(m.group(1))
            except Exception: pass
    return out or None
def ctx_ppa(t):
    if not t: return None
    norm=re.sub(r'\s+',' ',re.sub(r'\b\d[\d.]*\b',' ',t)).lower()
    nn=''.join(c for c in unicodedata.normalize('NFD',norm) if unicodedata.category(c)!='Mn')
    dom='sin positivos en porcino domestico' in nn
    com='sin cambios en el comercio exterior' in nn
    # Señales numéricas (la variable nº1 a vigilar: casos nuevos + ¿salta a doméstico? + aperturas de exportación)
    mN=re.search(r'N[ºo]\s*(\d{1,3})', t)
    sem=int(mN.group(1)) if mN else None
    mNew=re.search(r'(\d+)\s+NUEVOS', t)
    nuevos=int(mNew.group(1)) if mNew else None
    mAc=re.search(r'(\d{2,4})\s+[\d.]+\s+\d+\s+NUEVO', t)
    acum=int(mAc.group(1)) if mAc else None
    apertura=bool(re.search(r'autoriza|reabre|reapertura|nuevo acuerdo', t, re.I))
    resumen=('Solo focos en jabalíes, SIN positivos en cerdo doméstico y sin nuevos vetos (China/Japón no cierran por esto de momento)'
             if (dom and com) else 'Situación PPA con posibles cambios — revisar, puede afectar a vetos de exportación')
    if nuevos is not None: resumen=f'{nuevos} casos nuevos esta semana (solo jabalí). '+resumen
    if apertura: resumen+=' · OJO: esta semana hay una NUEVA apertura/acuerdo de exportación'
    return {'sin_positivos_domestico':dom,'sin_cambios_comercio_exterior':com,
            'semana_informe':sem,'casos_nuevos_jabali':nuevos,'acumulados_positivos':acum,
            'apertura_comercial':apertura,'resumen':resumen}
def ctx_lechon(t):
    m=re.search(r'Precio Base 20 ?kg\s+[\d,]+\s+([\d,]+)',t)
    try: return tf(m.group(1)) if m else None
    except Exception: return None
def cargar_series():
    if os.path.exists(SERIES_FILE):
        try: return json.load(open(SERIES_FILE))
        except Exception: pass
    return {'semanal':{},'mensual':{},'narrativo':{}}

def actualizar_series():
    # Procesa TODOS los PDFs que haya en _pdfs (los que llegan cada dia del correo), de TODAS las familias,
    # y los fusiona en SERIES_FILE. Asi cada dia el sistema aprende lo nuevo sin perder lo viejo.
    series=cargar_series()
    series.setdefault('semanal',{}); series.setdefault('mensual',{}); series.setdefault('narrativo',{})
    PAGES={'aleman':1,'precios_eu':1,'lechon':2,'despiece':2,'matanza':2,'consumo':2,'cereales':20,
           'censo':1,'china_import':1,'exportacion':1,'usda':1,'fao':1,'importacion':1,'china_produccion':1}
    nuevos=0
    for f in sorted(glob.glob(os.path.join(PDFS,'*.pdf'))):
        fn=os.path.basename(f); fam=EX.familia(fn)
        if not fam or fam in ('cotizacion','boletin','ppa'): continue
        fx=EX.EXTRACTOR.get(fam)
        if not fx: continue
        try:
            with pdfplumber.open(f) as pdf: t='\n'.join((p.extract_text() or '') for p in pdf.pages[:PAGES.get(fam,2)])
        except Exception: continue
        r=fx(t)
        if not r: continue
        if fam in ('lechon','despiece','aleman','precios_eu'):
            iso=EX.iso_de_nombre(fn)
            if not iso: continue
            key=f"{iso['y']}-W{iso['w']:02d}"; d=series['semanal'].setdefault(fam,{})
            if key not in d or iso['fecha']>=d[key].get('fecha',''): d[key]={**r,'fecha':iso['fecha']}; nuevos+=1
        elif fam=='cereales':
            # el Boletín NO lleva fecha en el nombre -> la semana/año vienen del contenido
            if not r.get('anio') or not r.get('semana'): continue
            try: fch=datetime.date.fromisocalendar(r['anio'],r['semana'],1).isoformat()
            except Exception: fch=''
            key=f"{r['anio']}-W{r['semana']:02d}"; d=series['semanal'].setdefault('cereales',{})
            if key not in d: d[key]={**{k:v for k,v in r.items() if k not in ('anio','semana')},'fecha':fch}; nuevos+=1
        elif fam in ('matanza','consumo'):
            tgt=series['mensual'].setdefault(fam,{'anos':[],'series':{}})
            for ano in r.get('anos',[]):
                if ano not in tgt['anos']: tgt['anos'].append(ano)
            for ano,meses in r.get('series',{}).items():
                tgt['series'].setdefault(str(ano),{})
                for mes,val in meses.items(): tgt['series'][str(ano)][str(mes)]=val
            tgt['anos']=sorted(set(tgt['anos'])); nuevos+=1
        else:
            iso=EX.iso_de_nombre(fn) or {}; tl=series['narrativo'].setdefault(fam,[])
            if not any(x.get('archivo')==fn for x in tl):
                tl.append({'fecha':iso.get('fecha',''),'archivo':fn,'titular':r.get('titular'),
                           'resumen':r.get('resumen'),'periodo':r.get('periodo','')})
                tl.sort(key=lambda x:(x.get('periodo') or '',x.get('fecha') or '',x.get('archivo') or '')); series['narrativo'][fam]=tl[-24:]; nuevos+=1
    series['generado']=datetime.datetime.now().isoformat()
    try: json.dump(series,open(SERIES_FILE,'w'),ensure_ascii=False)
    except Exception as e: print('  (no se pudo guardar series:',e,')')
    print(f'  Series actualizadas (familias nuevas/tocadas: {nuevos}). Semanal={ {k:len(v) for k,v in series["semanal"].items()} }')
    return series

def _ult_semanal(serie):
    # ultimo valor de una serie semanal {'2026-W39':{...}}
    if not serie: return None
    k=sorted(serie)[-1]; return serie[k]
def _mini_trend(serie,campo,n=12):
    # ultimos n valores de un campo (acepta campo con '.' para anidado) para que el bot vea la tendencia
    out=[]
    for k in sorted(serie)[-n:]:
        v=serie[k]
        for p in campo.split('.'):
            v=v.get(p) if isinstance(v,dict) else None
            if v is None: break
        if v is not None: out.append({'sem':k,'v':v})
    return out

def construir_contexto_rico(series, ppa_ctx=None, ppa_hist=None):
    # Construye el SNAPSHOT que viaja cifrado a la web y que recibe el chatbot: ultimo valor + tendencia
    # de cada serie + titulares de los informes de fondo. Para que el bot SEPA todo lo que manda Daniel.
    sw=series.get('semanal',{}); sm=series.get('mensual',{}); sn=series.get('narrativo',{})
    ctx={}
    # Europa (precios de todos los paises)
    eu=_ult_semanal(sw.get('precios_eu',{}))
    if eu:
        ctx['europa']={'vivo_ue':eu.get('vivo_ue'),'cotizaciones':eu.get('cotizaciones'),'fecha':eu.get('fecha'),
                       'tendencia_espana':_mini_trend(sw['precios_eu'],'vivo_ue.espana')}
    # Lechon
    le=_ult_semanal(sw.get('lechon',{}))
    if le: ctx['lechon']={**{k:v for k,v in le.items() if k!='fecha'},'fecha':le.get('fecha'),
                          'tendencia':_mini_trend(sw['lechon'],'nacional_20kg')}
    # Despiece (precio de las piezas)
    de=_ult_semanal(sw.get('despiece',{}))
    if de: ctx['despiece']=de
    # Mercado aleman (AMI canal + matanza)
    al=_ult_semanal(sw.get('aleman',{}))
    if al: ctx['aleman']={**al,'tendencia_ponderado':_mini_trend(sw['aleman'],'ponderado')}
    # Cereales y piensos (€/t) -> coste de producir el cerdo. maiz y soja son los que mas pesan.
    ce=_ult_semanal(sw.get('cereales',{}))
    if ce: ctx['cereales']={**ce,'tendencia_maiz':_mini_trend(sw['cereales'],'maiz'),
                            'tendencia_soja':_mini_trend(sw['cereales'],'harina_soja')}
    # Peso de la canal en Espana (matanza, mensual) -> lo que pidio Francisco
    mz=sm.get('matanza',{}).get('series',{})
    if mz:
        anos=sorted(mz,key=int); ultano=anos[-1]; meses=sorted(mz[ultano],key=int); ultmes=meses[-1]
        ctx['peso_canal_es']={'ano':int(ultano),'mes':int(ultmes),'valor':mz[ultano][ultmes].get('peso_canal'),
                              'cabezas_miles':mz[ultano][ultmes].get('cabezas_miles'),
                              'serie_ultano':[{'mes':int(m),'peso':mz[ultano][m].get('peso_canal')} for m in meses]}
    # Consumo (mensual)
    cn=sm.get('consumo',{}).get('series',{})
    if cn:
        anos=sorted(cn,key=int); ultano=anos[-1]; meses=sorted(cn[ultano],key=int)
        if meses: ctx['consumo_es']={'ano':int(ultano),'mes':int(meses[-1]),'fresco_miles_kg':cn[ultano][meses[-1]]}
    # Fundamentales de fondo (titulares de los ultimos informes de cada tipo)
    fund={}
    etiq={'censo':'censo_porcino','china_import':'china_importacion','exportacion':'exportacion',
          'usda':'previsiones_usda','fao':'previsiones_fao','importacion':'importacion','china_produccion':'china_produccion'}
    for fam,nom in etiq.items():
        tl=sn.get(fam,[])
        if tl: fund[nom]={'titular':tl[-1].get('titular'),'resumen':tl[-1].get('resumen'),'fecha':tl[-1].get('fecha')}
    if fund: ctx['fundamentales']=fund
    # PPA (se mantiene el extractor especifico, es la variable nº1)
    if ppa_ctx: ctx['ppa']=ppa_ctx
    if ppa_hist: ctx['ppa_historico']=ppa_hist
    return ctx

def paso_contexto():
    # 1) Actualiza las series con los PDFs nuevos del correo. 2) Procesa PPA (extractor dedicado).
    # 3) Construye el contexto rico para el chatbot/web. 4) Persiste contexto para trazabilidad.
    series=actualizar_series()
    prev={}
    if os.path.exists(CTX_FILE):
        try: prev=json.load(open(CTX_FILE))
        except Exception: prev={}
    hoy=datetime.date.today().isoformat()
    ppa_ctx=prev.get('ppa'); ppa_hist=prev.get('ppa_historico',[])
    try:
        # Elegir el informe PPA MÁS RECIENTE por nº de semana (no por orden alfabético del fichero,
        # que elegía mal si convivían "Actualización PPA..." y "Resumen semanal PPA...").
        mejor=None; mejor_sem=-1
        for f in glob.glob(os.path.join(PDFS,'*PPA*.pdf')):
            cand=ctx_ppa(_pdftxt(f))
            if cand and (cand.get('semana_informe') or -1) > mejor_sem:
                mejor=cand; mejor_sem=cand.get('semana_informe') or -1
        if mejor is None:  # fallback: ningún informe traía nº de semana -> el último por nombre
            lf=_latest_pdf('*PPA*.pdf'); mejor=ctx_ppa(_pdftxt(lf)) if lf else None
        pp=mejor
        if pp:
                ppa_ctx={**pp,'fecha':hoy}
                sem=pp.get('semana_informe')
                if sem is not None and not any(h.get('semana')==sem for h in ppa_hist):
                    ppa_hist.append({'semana':sem,'fecha':hoy,'casos_nuevos':pp.get('casos_nuevos_jabali'),
                                     'acumulados':pp.get('acumulados_positivos'),'domestico':(0 if pp.get('sin_positivos_domestico') else 1)})
                    ppa_hist=sorted(ppa_hist,key=lambda h:h.get('semana') or 0)[-60:]
    except Exception as e:
        print('  (PPA no disponible:',e,')')
    ctx=construir_contexto_rico(series,ppa_ctx,ppa_hist)
    try: json.dump(ctx,open(CTX_FILE,'w'),ensure_ascii=False)
    except Exception as e: print('  (no se pudo guardar contexto:',e,')')
    print(f'  Contexto rico: {list(ctx.keys())}')
    return ctx

# ---------- 2) ACTUALIZAR MAESTRO ----------
def paso_maestro():
    po=index_files('PO'); de=index_files('DE'); ip=index_files('IP')  # claves (año,semana)
    wb=openpyxl.load_workbook(MASTER)
    YEL=PatternFill('solid',fgColor='FFF2CC'); ORA=PatternFill('solid',fgColor='FCE4D6')
    nuevas=[]
    anos_presentes=sorted({y for (y,w) in list(po)+list(de)+list(ip)})
    # España (CEBADO LLEIDA) — semanas 1..53 (2026 es año ISO de 53 semanas)
    for (y,w),path in sorted(po.items()):
        col=_ensure_col(wb['CEBADO LLEIDA'],_col_anual(y),8,y); r=8+w
        if wb['CEBADO LLEIDA'].cell(r,col).value is None:
            eq=esp_de_po(path)
            if eq is not None: wb['CEBADO LLEIDA'].cell(r,col).value=eq; wb['CEBADO LLEIDA'].cell(r,col).fill=YEL; nuevas.append((y,w))
    # Alemania + Francia (del informe de despiece DE)
    for (y,w),path in sorted(de.items()):
        ale,fra=ale_fra_de_de(path); r=8+w
        cA=_ensure_col(wb['ALEMANIA'],_col_anual(y),8,y)
        cF=_col_fra_val(y); _ensure_col(wb['FRANCIA'],_col_fra_hdr(y),7,y)
        if wb['ALEMANIA'].cell(r,cA).value is None and ale is not None: wb['ALEMANIA'].cell(r,cA).value=ale; wb['ALEMANIA'].cell(r,cA).fill=YEL
        if wb['FRANCIA'].cell(r,cF).value is None and fra is not None: wb['FRANCIA'].cell(r,cF).value=fra; wb['FRANCIA'].cell(r,cF).fill=YEL
    # Dinamarca (del Info-Porcino IP)
    for (y,w),path in sorted(ip.items()):
        cD=_ensure_col(wb['DINAMARCA'],_col_anual(y),8,y); r=8+w
        if wb['DINAMARCA'].cell(r,cD).value is None:
            d=din_de_ip(path)
            if d is not None: wb['DINAMARCA'].cell(r,cD).value=d; wb['DINAMARCA'].cell(r,cD).fill=YEL
    # Relleno hacia adelante de ALE/FRA (series con huecos) SOLO en los años presentes, semanas 1..53
    for y in anos_presentes:
        for sheet,col in [('ALEMANIA',_col_anual(y)),('FRANCIA',_col_fra_val(y))]:
            weeks=[w for w in range(1,54) if wb[sheet].cell(8+w,col).value is not None]
            if not weeks: continue
            lw=max(weeks)
            for w in range(2,lw+1):
                c=wb[sheet].cell(8+w,col)
                if c.value is None and wb[sheet].cell(8+w-1,col).value is not None:
                    c.value=wb[sheet].cell(8+w-1,col).value; c.fill=ORA
    wb.save(MASTER)
    print(f'  Semanas España nuevas en maestro: {nuevas}')
    # resumen para el "diario del cartero" (semana más reciente leída)
    lat={}
    try:
        cand=sorted(set(list(de)+list(po)+list(ip)))
        lw=cand[-1] if cand else None
        if lw is not None:
            ale,fra=(ale_fra_de_de(de[lw]) if lw in de else (None,None))
            lat={'sem':lw[1],'anio':lw[0],'cebado':(cebado_de_de(de[lw]) if lw in de else None),
                 'fra':fra,'ale':ale,'din':(din_de_ip(ip[lw]) if lw in ip else None)}
    except Exception as e:
        lat={}
    return {'nuevas':nuevas,'lat':lat}

# ---------- 3) MODELO ----------
def solve(A,b):
    n=len(A); M=[row[:]+[b[i]] for i,row in enumerate(A)]
    for c in range(n):
        p=max(range(c,n),key=lambda r:abs(M[r][c])); M[c],M[p]=M[p],M[c]; piv=M[c][c]
        if abs(piv)<1e-12: continue
        for r in range(n):
            if r!=c and abs(M[r][c])>1e-12:
                f=M[r][c]/piv
                for k in range(c,n+1): M[r][k]-=f*M[c][k]
    return [M[i][n]/M[i][i] if abs(M[i][i])>1e-12 else 0.0 for i in range(n)]
def fit(data):
    k=len(data[0]['x']); ATA=[[0.0]*k for _ in range(k)]; ATy=[0.0]*k
    for d in data:
        for i in range(k):
            ATy[i]+=d['x'][i]*d['dESP']
            for j in range(k): ATA[i][j]+=d['x'][i]*d['x'][j]
    return solve(ATA,ATy)
def paso_modelo():
    # Años dinámicos: de 2010 al año ACTUAL (antes clavado a 2026 -> se paraba en 2027).
    wb=openpyxl.load_workbook(MASTER,data_only=True); years=list(range(2010,datetime.date.today().year+1))
    def cell(sh,w,col):
        v=wb[sh].cell(8+w,col).value; return v if isinstance(v,(int,float)) else None
    seq=[]
    for y in years:
        yi=years.index(y)
        for w in range(1,54):  # 1..53: hay años ISO de 53 semanas (2015, 2020, 2026...)
            e=cell('CEBADO LLEIDA',w,2+yi)
            if e is None: continue
            seq.append({'y':y,'w':w,'e':e,'f':cell('FRANCIA',w,2+2*yi+1),'a':cell('ALEMANIA',w,2+yi),'d':cell('DINAMARCA',w,2+yi)})
    esp={(r['y'],r['w']):r['e'] for r in seq}; allmean=st.mean(esp.values())
    # seas robusto: una semana necesita >=5 años para una estimación estacional fiable. La semana 53
    # solo tiene 2 años (2015, 2020, ambos de precios bajos) -> su media está SESGADA a la baja y
    # predeciría una caída falsa a fin de diciembre. Esas semanas heredan la anterior (patrón vecino).
    seas={}
    for w in range(1,54):
        vals=[esp[(y,w)] for y in years if (y,w) in esp]
        if len(vals)>=5: seas[w]=st.mean(vals)/allmean*100
    for w in range(1,54):
        if w not in seas: seas[w]=seas.get(w-1) or seas.get(w+1) or 100.0
    rows=[]
    for t in range(4,len(seq)):
        c,p1,p2,p3,p4=seq[t],seq[t-1],seq[t-2],seq[t-3],seq[t-4]
        rows.append({'y':c['y'],'w':c['w'],'prev':p1['e'],'dESP':c['e']-p1['e'],'target':c['e'],
            'x':[1.0,p1['e']-p2['e'],p2['e']-p3['e'],p3['e']-p4['e'],(p1['f']-p2['f']) if p1['f'] and p2['f'] else 0.0,
                 (p1['a']-p2['a']) if p1['a'] and p2['a'] else 0.0,(seas.get(c['w'],100.0)-seas.get(p1['w'],100.0))/100.0*allmean]})
    em=[];en=[];within=0;nb=0
    for d in rows:
        if d['y']<2019: continue
        tr=[r for r in rows if (r['y']*100+r['w'])<(d['y']*100+d['w'])]
        if len(tr)<60: continue
        b=fit(tr); pr=d['prev']+sum(bi*xi for bi,xi in zip(b,d['x']))
        em.append(abs(pr-d['target'])); en.append(abs(d['prev']-d['target']))
        if abs(pr-d['target'])<=0.02: within+=1
        nb+=1
    beta=fit(rows); last=seq[-1]; p1=seq[-2]; p2=seq[-3]; p3=seq[-4]
    # Semana siguiente por FECHA real (maneja 52->53->1 y el salto de año sin errores de %52)
    _nd=datetime.date.fromisocalendar(last['y'],last['w'],1)+datetime.timedelta(days=7)
    nexty,nextw=_nd.isocalendar()[:2]
    x=[1.0,last['e']-p1['e'],p1['e']-p2['e'],p2['e']-p3['e'],(last['f']-p1['f']) if last['f'] and p1['f'] else 0.0,
       (last['a']-p1['a']) if last['a'] and p1['a'] else 0.0,(seas.get(nextw,100.0)-seas.get(last['w'],100.0))/100.0*allmean]
    pred=round(last['e']+sum(bi*xi for bi,xi in zip(beta,x)),3)
    contrib={'inercia':round((beta[1]*x[1]+beta[2]*x[2]+beta[3]*x[3])*100,1),'francia':round(beta[4]*x[4]*100,1),
             'alemania':round(beta[5]*x[5]*100,1),'estacional':round(beta[6]*x[6]*100,1)}
    vecinos={'francia_delta':round((last['f']-p1['f'])*100,1) if last['f'] and p1['f'] else None,
             'alemania_delta':round((last['a']-p1['a'])*100,1) if last['a'] and p1['a'] else None}
    serie=[{'y':r['y'],'w':r['w'],'v':r['e']} for r in seq[-104:]]
    win=seq[-104:]
    def idx(win,key):
        base=next((r[key] for r in win if r.get(key) is not None),None)
        return [round(r[key]/base*100,1) if (r.get(key) is not None and base) else None for r in win]
    serie_paises={'labels':[{'y':r['y'],'w':r['w']} for r in win],
                  'esp':idx(win,'e'),'fra':idx(win,'f'),'ale':idx(win,'a'),'din':idx(win,'d')}
    return {'mae_m':sum(em)/len(em),'mae_n':sum(en)/len(en),'within2':round(within/nb*100) if nb else 0,
            'last':{'y':last['y'],'w':last['w'],'v':round(last['e'],3)},
            'nextw':nextw,'nexty':nexty,'pred':pred,'esp':esp,'serie':serie,
            'delta_cts':round((pred-last['e'])*100,1),'contrib':contrib,'vecinos':vecinos,
            'seas':{str(w):round(seas[w],1) for w in seas},'serie_paises':serie_paises}

# ---------- 4) REGISTRO ----------
def paso_registro(m):
    L={}
    if os.path.exists(LOG):
        for r in csv.DictReader(open(LOG)): L[(int(r['anio']),int(r['semana']))]=r
    for (y,w),r in L.items():
        if (not r.get('real')) and (y,w) in m['esp']:
            real=m['esp'][(y,w)]; r['real']=f"{real:.3f}"; r['error_cts']=f"{abs(float(r['prediccion'])-real)*100:.1f}"
    key=(m['nexty'],m['nextw'])
    if key not in L:
        L[key]={'anio':m['nexty'],'semana':m['nextw'],'fecha_pred':datetime.date.today().isoformat(),
                'prediccion':f"{m['pred']:.3f}",'real':'','error_cts':''}
    with open(LOG,'w',newline='') as fh:
        w=csv.DictWriter(fh,fieldnames=['anio','semana','fecha_pred','prediccion','real','error_cts']); w.writeheader()
        for k in sorted(L): w.writerow(L[k])
    return [L[k] for k in sorted(L)]

# ---------- 4b) CONGELAR TRAYECTORIA A MEDIO PLAZO (registro inmutable) ----------
def paso_congelar(m):
    # Guarda, UNA SOLA VEZ por semana objetivo y solo si es una prediccion A CIEGAS (emitida antes de que
    # empiece esa semana), la trayectoria completa a 26 semanas con horquilla. NUNCA se reescribe.
    # Esto es lo que permitira medir la precision a 1/4/6 meses cuando pase el tiempo (peticion de Francisco/Daniel).
    py,pw=m['nexty'],m['nextw']
    try:
        lunes=datetime.date.fromisocalendar(py,pw,1)
    except Exception:
        return
    hoy=datetime.date.today()
    if hoy>=lunes:  # la semana objetivo ya empezo/paso -> NO es prediccion a ciegas, no congelar
        return
    existentes=set()
    if os.path.exists(CONG_FILE):
        for line in open(CONG_FILE):
            try: r=json.loads(line); existentes.add((r['target_y'],r['target_w'],r.get('model_version')))
            except Exception: pass
    if (py,pw,MODEL_VERSION) in existentes:  # ya congelada esta semana con esta version
        return
    pv=m['pred']; seas=m['seas']; sref=seas.get(str(pw)) or 100.0
    traj=[]
    for i in range(26):
        fw=((pw-1+i)%52)+1; fy=py+((pw-1+i)//52)
        sf=seas.get(str(fw)) or sref
        central=round(pv*(sf/sref),3)
        semi=BANDA_CAL[i] if i<len(BANDA_CAL) else BANDA_CAL[-1]   # banda CALIBRADA con el backtest real (P80)
        traj.append({'y':fy,'w':fw,'h':i,'central':central,'lo':round(central-semi,3),'hi':round(central+semi,3)})
    rec={'forecast_id':f'{py}-W{pw:02d}-{MODEL_VERSION}','issued_at':hoy.isoformat(),
         'model_version':MODEL_VERSION,'model_features':MODEL_FEATURES,
         'target_y':py,'target_w':pw,'ultimo_real':m['last'],'trayectoria':traj}
    try:
        with open(CONG_FILE,'a') as f: f.write(json.dumps(rec,ensure_ascii=False)+'\n')
        print(f'  Prediccion CONGELADA: {rec["forecast_id"]} ({len(traj)} semanas, horizonte ~6 meses)')
    except Exception as e:
        print('  (no se pudo congelar:',e,')')

def cargar_congeladas():
    out=[]
    if os.path.exists(CONG_FILE):
        for line in open(CONG_FILE):
            try: out.append(json.loads(line))
            except Exception: pass
    return out
def eval_horizontes(congeladas, esp):
    # Compara cada punto de cada trayectoria CONGELADA con el precio real (cuando ya existe), por horizonte.
    # Mide la precision REAL a medio plazo de forma honesta (acumula con el tiempo). esp: dict (y,w)->precio.
    por_h={}
    for rec in congeladas:
        ver=rec.get('model_version','?')
        for pt in rec.get('trayectoria',[]):
            real=esp.get((pt['y'],pt['w']))
            if real is None or pt.get('h') is None: continue
            k=(ver,pt['h'])
            por_h.setdefault(k,{'err':[],'dentro':0,'n':0})
            e=abs(pt['central']-real); por_h[k]['err'].append(e); por_h[k]['n']+=1
            if pt['lo']-1e-9<=real<=pt['hi']+1e-9: por_h[k]['dentro']+=1
    out=[]
    for (ver,h),d in sorted(por_h.items()):
        out.append({'version':ver,'horizonte_sem':h,'n':d['n'],
                    'mae_cts':round(sum(d['err'])/len(d['err'])*100,1),
                    'cobertura_pct':round(d['dentro']/d['n']*100)})
    return out

# ---------- 5) CIFRAR PARA LA WEB ----------
def cifrar(plaintext,password):
    salt=os.urandom(16)
    kdf=PBKDF2HMAC(algorithm=hashes.SHA256(),length=32,salt=salt,iterations=200000)
    key=kdf.derive(password.encode())
    iv=os.urandom(12); ct=AESGCM(key).encrypt(iv,plaintext.encode(),None)
    return base64.b64encode(salt+iv+ct).decode()
def es_backtest(r):
    # Es backtest si la "predicción" se apuntó cuando la semana ya había empezado (no fue a ciegas).
    try:
        mon=datetime.date.fromisocalendar(int(r['anio']),int(r['semana']),1)
        fp=datetime.date.fromisoformat(r['fecha_pred'])
        return fp>=mon
    except Exception:
        return False
def paso_web(m,historial,diario=None,contexto=None):
    delta=m['delta_cts']; base=m['pred']
    c1=lambda x:('%.1f'%x).replace('.',',')
    if delta>=1.0: sem={'estado':'AGUANTA','color':'verde','txt':'El precio va a SUBIR ~'+c1(delta)+' cts la semana que viene. Si puedes, aguanta la venta.'}
    elif delta<=-1.0: sem={'estado':'VENDE','color':'rojo','txt':'El precio va a BAJAR ~'+c1(abs(delta))+' cts la semana que viene. Vender ahora protege margen.'}
    else: sem={'estado':'ESTABLE','color':'ambar','txt':'Precio estable (±'+c1(abs(delta))+' cts). Sin presión para adelantar ni retrasar ventas.'}
    escenarios=[
      {'nombre':'Base (modelo)','valor':round(base,3),'txt':'Lo más probable con los datos actuales.'},
      {'nombre':'Se agrava PPA / cierran mercados','valor':round(base-0.15,3),'txt':'Menos exportación, sobra carne → precio abajo. (~-15 cts, ilustrativo)'},
      {'nombre':'China reabre / sube demanda','valor':round(base+0.15,3),'txt':'Más demanda exterior → precio arriba. (~+15 cts, ilustrativo)'}]
    eventos=[{'fecha':'Nov 2025','titulo':'Densidades: el Supremo anuló la norma','txt':'El Tribunal Supremo (sentencia de 3-nov-2025, BOE 21-nov-2025) ANULÓ la exigencia de más espacio por cerdo del RD 159/2023, por un informe de impacto económico insuficiente. Se vuelve al estándar de 2002. Traducción para el mercado: NO hay recorte forzoso de cabaña → la oferta española no baja por esta vía, así que el escenario "menos cerdos → precio arriba" queda descartado. OJO: viene nueva normativa de bienestar (RD 809/2025) para 2026, así que no es la última palabra.'}]
    payload={'generado':datetime.datetime.now().strftime('%d/%m/%Y %H:%M'),
             'ultimo':m['last'],'prediccion':{'y':m['nexty'],'w':m['nextw'],'v':m['pred']},'delta_cts':delta,
             'precision':{'modelo_cts':round(m['mae_m']*100,1),'naive_cts':round(m['mae_n']*100,1),'within2':m['within2']},
             'historial':[{'semana':r['semana'],'anio':r.get('anio'),'pred':r['prediccion'],'real':r.get('real',''),'error':r.get('error_cts',''),'backtest':es_backtest(r)} for r in historial],
             'serie':m['serie'],'serie_paises':m['serie_paises'],'contrib':m['contrib'],'vecinos':m['vecinos'],
             'seas':m['seas'],'semaforo':sem,'escenarios':escenarios,'eventos':eventos,
             'contexto_mercado':(contexto or {}),
             'medio_plazo':(lambda c: ({'actual':c[-1],'eval':eval_horizontes(c,m['esp']),'version':MODEL_VERSION,'n_congeladas':len(c)}) if c else {'actual':None,'eval':[],'version':MODEL_VERSION,'n_congeladas':0})(cargar_congeladas()),
             'diario':(diario or {})}
    pw=os.environ.get('WEB_PASSWORD','cerdo')
    enc=cifrar(json.dumps(payload,ensure_ascii=False),pw)
    open(ENC_OUT,'w').write('window.ENC="'+enc+'";')
    print(f'  Web cifrada actualizada ({len(enc)} bytes).')

if __name__=='__main__':
    print('== 1) Correo =='); cor=paso_correo() or (0,[])
    print('== 2) Maestro =='); mae=paso_maestro() or {}
    print('== 3) Modelo =='); m=paso_modelo()
    print('== 4) Registro =='); h=paso_registro(m)
    print('== 4b) Congelar medio plazo =='); paso_congelar(m)
    print('== 4c) Contexto =='); ctx=paso_contexto()
    diario={'ts':datetime.datetime.now().strftime('%d/%m/%Y %H:%M'),
            'pdfs_nuevos':cor[0],'nombres':cor[1][:8],
            'nuevas':mae.get('nuevas',[]),'lat':mae.get('lat',{})}
    print('== 5) Web =='); paso_web(m,h,diario,ctx)
    print(f"\nOK. Último {m['last']['w']}={m['last']['v']} | Predicción sem {m['nextw']}={m['pred']} | error {round(m['mae_m']*100,1)} cts")
