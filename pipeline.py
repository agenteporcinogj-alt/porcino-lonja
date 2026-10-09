#!/usr/bin/env python3
# PIPELINE NUBE - Grupo Jorge / precio cerdo Lonja de Lleida
# correo -> PDFs -> Excel maestro -> modelo -> prediccion -> registro -> web cifrada
import os, re, csv, json, base64, datetime, statistics as st, glob, imaplib, email
from email.header import decode_header
import pdfplumber, openpyxl
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
ENC_OUT=os.path.join(HERE,'docs','data.enc.js')
# --- Versionado del modelo (lo que la reunion llama "V1"). Cada cambio de variables = nueva version ---
MODEL_VERSION='v1'
MODEL_FEATURES='inercia España 3 semanas + delta Francia + delta Alemania + estacionalidad'
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
    out={}
    for f in glob.glob(os.path.join(PDFS,pfx+'[0-9]*.pdf')):
        yw=isoweek(os.path.basename(f),pfx)
        if yw and yw[0]==2026 and yw[1] not in out: out[yw[1]]=f
    return out
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
    return {'sin_positivos_domestico':dom,'sin_cambios_comercio_exterior':com,
            'resumen':('Solo focos en jabalíes, sin positivos en cerdo doméstico y SIN cambios en exportación (China/Japón no vetan por esto de momento)'
                       if (dom and com) else 'Situación PPA con posibles cambios — revisar, podría afectar a bloqueos de exportación')}
def ctx_lechon(t):
    m=re.search(r'Precio Base 20 ?kg\s+[\d,]+\s+([\d,]+)',t)
    try: return tf(m.group(1)) if m else None
    except Exception: return None
def paso_contexto():
    # Extrae señales ricas de los PDF que haya en disco (los NUEVOS de este run) y las PERSISTE en JSON,
    # fusionando con lo anterior. Así el contexto sobrevive aunque un PDF viejo no se vuelva a descargar.
    ctx={}
    if os.path.exists(CTX_FILE):
        try: ctx=json.load(open(CTX_FILE))
        except Exception: ctx={}
    hoy=datetime.date.today().isoformat()
    try:
        pe=_latest_pdf('PE*.pdf')
        if pe:
            eu=ctx_europa(_pdftxt(pe))
            if eu: ctx['europa']={'datos':eu,'fecha':hoy}
        ppaf=_latest_pdf('*PPA*.pdf')
        if ppaf:
            pp=ctx_ppa(_pdftxt(ppaf))
            if pp: ctx['ppa']={**pp,'fecha':hoy}
        lef=_latest_pdf('LE*.pdf')
        if lef:
            le=ctx_lechon(_pdftxt(lef))
            if le is not None: ctx['lechon_nacional_20kg']={'valor':le,'fecha':hoy}
        try: json.dump(ctx,open(CTX_FILE,'w'),ensure_ascii=False)
        except Exception as e: print('  (no se pudo guardar contexto:',e,')')
        print(f'  Contexto mercado persistido: {list(ctx.keys())}')
    except Exception as e:
        print('  (contexto no disponible:',e,')')
    return ctx

# ---------- 2) ACTUALIZAR MAESTRO ----------
def paso_maestro():
    po=index_files('PO'); de=index_files('DE'); ip=index_files('IP')
    wb=openpyxl.load_workbook(MASTER)
    YEL=PatternFill('solid',fgColor='FFF2CC'); ORA=PatternFill('solid',fgColor='FCE4D6')
    def g(sh,w,col): return wb[sh].cell(8+w,col).value
    nuevas=[]
    for w in range(1,53):
        r=8+w
        if g('CEBADO LLEIDA',w,18) is None and w in po:
            eq=esp_de_po(po[w])
            if eq is not None: wb['CEBADO LLEIDA'].cell(r,18).value=eq; wb['CEBADO LLEIDA'].cell(r,18).fill=YEL; nuevas.append(w)
        if w in de:
            ale,fra=ale_fra_de_de(de[w])
            if g('ALEMANIA',w,18) is None and ale is not None: wb['ALEMANIA'].cell(r,18).value=ale; wb['ALEMANIA'].cell(r,18).fill=YEL
            if g('FRANCIA',w,35) is None and fra is not None: wb['FRANCIA'].cell(r,35).value=fra; wb['FRANCIA'].cell(r,35).fill=YEL
        if g('DINAMARCA',w,18) is None and w in ip:
            d=din_de_ip(ip[w])
            if d is not None: wb['DINAMARCA'].cell(r,18).value=d; wb['DINAMARCA'].cell(r,18).fill=YEL
    for sheet,col in [('ALEMANIA',18),('FRANCIA',35)]:
        lw=max([w for w in range(1,53) if g(sheet,w,col) is not None] or [0])
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
            lat={'sem':lw,'cebado':(cebado_de_de(de[lw]) if lw in de else None),
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
    wb=openpyxl.load_workbook(MASTER,data_only=True); years=list(range(2010,2027))
    def cell(sh,w,col):
        v=wb[sh].cell(8+w,col).value; return v if isinstance(v,(int,float)) else None
    seq=[]
    for y in years:
        yi=years.index(y)
        for w in range(1,53):
            e=cell('CEBADO LLEIDA',w,2+yi)
            if e is None: continue
            seq.append({'y':y,'w':w,'e':e,'f':cell('FRANCIA',w,2+2*yi+1),'a':cell('ALEMANIA',w,2+yi),'d':cell('DINAMARCA',w,2+yi)})
    esp={(r['y'],r['w']):r['e'] for r in seq}; allmean=st.mean(esp.values())
    seas={w: st.mean([esp[(y,w)] for y in years if (y,w) in esp])/allmean*100 for w in range(1,53)}
    rows=[]
    for t in range(4,len(seq)):
        c,p1,p2,p3,p4=seq[t],seq[t-1],seq[t-2],seq[t-3],seq[t-4]
        rows.append({'y':c['y'],'w':c['w'],'prev':p1['e'],'dESP':c['e']-p1['e'],'target':c['e'],
            'x':[1.0,p1['e']-p2['e'],p2['e']-p3['e'],p3['e']-p4['e'],(p1['f']-p2['f']) if p1['f'] and p2['f'] else 0.0,
                 (p1['a']-p2['a']) if p1['a'] and p2['a'] else 0.0,(seas[c['w']]-seas[p1['w']])/100.0*allmean]})
    em=[];en=[];within=0;nb=0
    for d in rows:
        if d['y']<2019: continue
        tr=[r for r in rows if (r['y']*100+r['w'])<(d['y']*100+d['w'])]
        if len(tr)<60: continue
        b=fit(tr); pr=d['prev']+sum(bi*xi for bi,xi in zip(b,d['x']))
        em.append(abs(pr-d['target'])); en.append(abs(d['prev']-d['target']))
        if abs(pr-d['target'])<=0.02: within+=1
        nb+=1
    beta=fit(rows); last=seq[-1]; p1=seq[-2]; p2=seq[-3]; p3=seq[-4]; nextw=last['w']%52+1
    x=[1.0,last['e']-p1['e'],p1['e']-p2['e'],p2['e']-p3['e'],(last['f']-p1['f']) if last['f'] and p1['f'] else 0.0,
       (last['a']-p1['a']) if last['a'] and p1['a'] else 0.0,(seas[nextw]-seas[last['w']])/100.0*allmean]
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
            'nextw':nextw,'nexty':last['y'] if nextw>last['w'] else last['y']+1,'pred':pred,'esp':esp,'serie':serie,
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
        semi=(1.2+1.4*(i**0.5))/100.0   # horquilla que se ABRE con el horizonte
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
