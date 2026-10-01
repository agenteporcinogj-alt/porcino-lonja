// ── Cerebro del chat IA de Grupo Jorge (Cloudflare Worker) · v6 (Sonnet + registro) ──
// Secretos: ANTHROPIC_API_KEY, ADMIN_KEY. Binding KV: LOG (registro de preguntas).
// POST → chat + guarda pregunta y respuesta.  GET ?ver=1&key=ADMIN_KEY → página con el historial.

export default {
  async fetch(request, env) {
    const cors = {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    };
    if (request.method === 'OPTIONS') return new Response(null, { headers: cors });
    if (request.method === 'GET') {
      const url = new URL(request.url);
      if (url.searchParams.has('ver')) return verLog(url, env, cors);
      return new Response('OK', { headers: cors });
    }
    if (request.method !== 'POST') return json({ error: 'POST only' }, 405, cors);

    let body;
    try { body = await request.json(); } catch { return json({ error: 'bad json' }, 400, cors); }

    const estado = body.estado || {};
    const extra = String(body.extra || '').slice(0, 4000);
    const usuario = String(body.usuario || 'visitante').slice(0, 40);
    const MODELOS = { haiku: 'claude-haiku-4-5-20251001', sonnet: 'claude-sonnet-5', opus: 'claude-opus-5-5' };
    const modelo = MODELOS[String(body.modelo || '').toLowerCase()] || 'claude-sonnet-5';

    let mensajes = Array.isArray(body.mensajes) ? body.mensajes : [];
    if (!mensajes.length && body.pregunta) mensajes = [{ role: 'user', content: String(body.pregunta) }];
    mensajes = mensajes
      .filter(m => m && (m.role === 'user' || m.role === 'assistant') && typeof m.content === 'string' && m.content.trim())
      .map(m => ({ role: m.role, content: m.content.slice(0, 2000) }))
      .slice(-12);
    while (mensajes.length && mensajes[0].role !== 'user') mensajes.shift();
    if (!mensajes.length || mensajes[mensajes.length - 1].role !== 'user')
      return json({ respuesta: 'Escríbeme una pregunta 🐷' }, 200, cors);

    const pregunta = mensajes[mensajes.length - 1].content;
    const enConversacion = mensajes.some(m => m.role === 'assistant');

    const txt = pregunta.toLowerCase();
    const TEMAS = ['cerdo','porcino','precio','lonja','lleida','mercolleida','cotiz','francia','aleman',
      'dinamarca','españa','espana','semana','predic','model','matader','canal','vivo','kg','céntimo',
      'centimo','euro','€','mercado','tendencia','sub','baj','vend','aguant','compr','china','ppa','peste',
      'pienso','censo','matanza','jorge','vecino','estacional','cebo','lechon','capa','exporta','demanda',
      'oferta','margen','beneficio','semaforo','semáforo','grafic','gráfic','dibuj','pinta','compar','evol',
      'dato','informe','daniel','padre','calcula','cuanto','cuánto'];
    const FOLLOW = ['por que','porque','porqué','pq','xq','y eso','desarrolla','desarroll','explica','explíca',
      'amplia','amplía','ahonda','profundiza','detalle','ejemplo','en serio','seguro','de verdad','como asi',
      'cómo así','entonces','ademas','además','y luego','continua','continúa','sigue','y si','cuenta mas',
      'cuéntame','dime mas','dime más','otra vez','no entiendo','vale y','ok y','y respecto','razon','razón',
      'motivo','a que se debe','y por','listo','crack','interesante','ya veo','osea','o sea','mas info'];
    const esTema = TEMAS.some(t => txt.includes(t));
    const esSaludo = /^(hola|buenas|hey|ey|hi|holi|qué tal|que tal|buenos|buenass|saludos)/.test(txt) || txt.length < 5;
    const esFollow = FOLLOW.some(t => txt.includes(t));
    const permitir = enConversacion || esTema || esSaludo || esFollow;
    if (!permitir) {
      await registrar(env, usuario, pregunta, '[rechazada: off-topic]');
      return json({ respuesta: 'Puedo ayudarte con todo lo del mercado del cerdo y las predicciones de la Lonja de Lleida 🐷 — precios, el porqué, tendencia, Francia/Alemania, cuándo vender, escenarios, impacto en €, gráficas… Pregúntame por ahí y te lo clavo.' }, 200, cors);
    }

    const CORE = `Eres el analista de mercado porcino de Grupo Jorge (empresa que sacrifica ~55.000 cerdos/semana de 120 kg). Hablas claro, cercano y con criterio, como un analista veterano que se moja y ayuda a decidir. Razona a fondo antes de responder: relaciona las señales del ESTADO entre sí (vecinos, estacionalidad, inercia, escenarios, demanda) y explica el porqué con lógica, no sueltes titulares.

ÁMBITO: mercado del cerdo, Lonja de Lleida (Mercolleida), precios en €/kg, países vecinos (Francia, Alemania, Dinamarca), tu modelo de predicción y las decisiones de venta. Mantén SIEMPRE el hilo: si el usuario dice "y por qué", "desarróllalo", "y eso", "en serio", "amplía"… se refiere a lo último que hablasteis. Si te piden algo TOTALMENTE ajeno al cerdo, recházalo con simpatía en una frase y reconduce.

REGLA DE ORO — CERO INVENTOS: los NÚMEROS (precios, semanas, fechas, errores, deltas, series) los coges EXCLUSIVAMENTE del ESTADO de abajo. NUNCA inventes una cifra. Si un dato no está en el ESTADO, dilo claramente. La cotización que predecimos es "Cerdo Blanco" (real de la Lonja, €/kg vivo, 3 decimales), no el equivalente.

Impacto para el negocio: 1 céntimo/kg ≈ 66.000 €/semana ≈ 3,3 M€/año (55.000 cerdos × 120 kg). Cuando ayude, traduce céntimos a € para su volumen.

Responde en español. Ajusta la longitud a la pregunta: breve si es simple, y si te piden "desarrolla/amplía/explica más" extiéndete y estructura con guiones. No prometas certezas: es una predicción, y dilo cuando toque.`;

    const system = CORE
      + `\n\nESTADO ACTUAL (datos reales del modelo, úsalos tal cual):\n` + JSON.stringify(estado)
      + (extra ? `\n\n` + extra : ``);

    let respuesta;
    try {
      const r = await fetch('https://api.anthropic.com/v1/messages', {
        method: 'POST',
        headers: { 'x-api-key': env.ANTHROPIC_API_KEY, 'anthropic-version': '2023-06-01', 'content-type': 'application/json' },
        body: JSON.stringify({ model: modelo, max_tokens: 1400, system, messages: mensajes }),
      });
      const data = await r.json();
      const bloque = (data?.content || []).find(c => c && c.type === 'text');  // Sonnet/Opus pueden meter un bloque 'thinking' antes
      respuesta = bloque?.text
        || (data?.error?.message ? '⚠️ ' + data.error.message : 'No he podido responder ahora mismo, prueba otra vez.');
    } catch (e) {
      respuesta = 'No he podido conectar ahora mismo, prueba otra vez en un momento.';
    }

    await registrar(env, usuario, pregunta, respuesta);
    return json({ respuesta }, 200, cors);
  },
};

// Guarda la pregunta y respuesta en el KV (todo en metadata para listarlo de una).
async function registrar(env, usuario, pregunta, respuesta) {
  try {
    if (!env.LOG) return;
    const limpia = String(respuesta).replace(/```[\s\S]*?```/g, '[gráfica/cálculo]').replace(/\s+/g, ' ').trim();
    const key = `q:${Date.now()}:${Math.random().toString(36).slice(2, 7)}`;
    await env.LOG.put(key, 'x', {
      metadata: { t: new Date().toISOString(), usuario, pregunta: String(pregunta).slice(0, 220), respuesta: limpia.slice(0, 560) },
      expirationTtl: 60 * 60 * 24 * 365,
    });
  } catch (_) {}
}

// Página para ver el historial (protegida con ADMIN_KEY).
async function verLog(url, env, cors) {
  const key = url.searchParams.get('key') || '';
  if (!env.ADMIN_KEY || key !== env.ADMIN_KEY)
    return new Response('No autorizado. Usa ?ver=1&key=TU_ADMIN_KEY', { status: 401, headers: cors });
  if (!env.LOG) return htmlResp('<h1>🐷 Registro</h1><p>Falta el KV (LOG).</p>', cors);
  const list = await env.LOG.list({ limit: 1000 });
  const rows = list.keys.map(k => k.metadata || {}).filter(m => m.t).sort((a, b) => (a.t < b.t ? 1 : -1));
  let h = '<h1>🐷 Historial del chatbot · ' + rows.length + ' preguntas</h1>';
  h += '<table><tr><th>Cuándo</th><th>Quién</th><th>Pregunta</th><th>Respuesta</th></tr>';
  for (const m of rows) {
    const f = new Date(m.t); const cuando = isNaN(f) ? esc(m.t) : f.toLocaleString('es-ES');
    h += '<tr><td class=t>' + esc(cuando) + '</td><td class=u>' + esc(m.usuario || '?') + '</td><td>' + esc(m.pregunta || '') + '</td><td class=r>' + esc(m.respuesta || '') + '</td></tr>';
  }
  h += '</table>';
  return htmlResp(h, cors);
}
function htmlResp(inner, cors) {
  const css = 'body{font-family:system-ui,Arial;background:#141414;color:#eee;margin:0;padding:16px}h1{font-size:1.1rem}table{border-collapse:collapse;width:100%;font-size:.86rem}td,th{border-bottom:1px solid #333;padding:8px;text-align:left;vertical-align:top}th{color:#e0a35b}.u{color:#5b8def;font-weight:600;white-space:nowrap}.t{color:#888;white-space:nowrap;font-size:.76rem}.r{color:#bbb;max-width:420px}';
  return new Response('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Historial · Grupo Jorge</title><style>' + css + '</style>' + inner, { status: 200, headers: { ...cors, 'content-type': 'text/html; charset=utf-8' } });
}
function esc(s) { return String(s).replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c])); }
function json(obj, status, cors) { return new Response(JSON.stringify(obj), { status, headers: { ...cors, 'content-type': 'application/json' } }); }
