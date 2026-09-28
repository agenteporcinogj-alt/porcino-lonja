// ── Cerebro del chat IA de Grupo Jorge (Cloudflare Worker) · v2 con memoria ──
// La API key va como SECRETO en Cloudflare (env.ANTHROPIC_API_KEY): nunca viaja al navegador.
// La web manda: { mensajes:[{role,content}...], estado (números reales del modelo), usuario }
// El Worker: portero con sentido común → llama a Claude con el HILO + correa "solo cerdo" → registra.

export default {
  async fetch(request, env) {
    const cors = {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    };
    if (request.method === 'OPTIONS') return new Response(null, { headers: cors });
    if (request.method !== 'POST') return json({ error: 'POST only' }, 405, cors);

    let body;
    try { body = await request.json(); } catch { return json({ error: 'bad json' }, 400, cors); }

    const estado = body.estado || {};
    const usuario = String(body.usuario || 'anon').slice(0, 40);

    // Hilo de conversación (con compatibilidad hacia atrás con {pregunta})
    let mensajes = Array.isArray(body.mensajes) ? body.mensajes : [];
    if (!mensajes.length && body.pregunta) mensajes = [{ role: 'user', content: String(body.pregunta) }];
    mensajes = mensajes
      .filter(m => m && (m.role === 'user' || m.role === 'assistant') && typeof m.content === 'string' && m.content.trim())
      .map(m => ({ role: m.role, content: m.content.slice(0, 1500) }))
      .slice(-10);
    while (mensajes.length && mensajes[0].role !== 'user') mensajes.shift();      // debe empezar por user
    if (!mensajes.length || mensajes[mensajes.length - 1].role !== 'user')
      return json({ respuesta: 'Escríbeme una pregunta 🐷' }, 200, cors);

    const pregunta = mensajes[mensajes.length - 1].content;
    const enConversacion = mensajes.some(m => m.role === 'assistant');

    // ── PORTERO con sentido común ──
    const txt = pregunta.toLowerCase();
    const TEMAS = ['cerdo','porcino','precio','lonja','lleida','mercolleida','cotiz','francia','aleman',
      'dinamarca','españa','espana','semana','predic','model','matader','canal','vivo','kg','céntimo',
      'centimo','euro','€','mercado','tendencia','sub','baj','vend','aguant','compr','china','ppa','peste',
      'pienso','censo','matanza','jorge','vecino','estacional','cebo','lechon','capa','exporta','demanda',
      'oferta','margen','beneficio','semaforo','semáforo','grafic','gráfic','dato','informe','daniel','padre'];
    const FOLLOW = ['por que','porque','porqué','pq','xq','y eso','desarrolla','desarroll','explica','explíca',
      'amplia','amplía','ahonda','profundiza','detalle','ejemplo','en serio','seguro','de verdad','como asi',
      'cómo así','entonces','ademas','además','y luego','continua','continúa','sigue','y si','cuenta mas',
      'cuéntame','dime mas','dime más','otra vez','no entiendo','vale y','ok y','y respecto','razon','razón',
      'motivo','a que se debe','y por','listo','crack','interesante','ya veo','osea','o sea','mas info'];
    const esTema = TEMAS.some(t => txt.includes(t));
    const esSaludo = /^(hola|buenas|hey|ey|hi|holi|qué tal|que tal|buenos|buenass|saludos)/.test(txt) || txt.length < 5;
    const esFollow = FOLLOW.some(t => txt.includes(t));
    // Si ya vamos hablando de cerdo, confiamos en los follow-ups (el modelo reconduce lo ajeno).
    const permitir = enConversacion || esTema || esSaludo || esFollow;
    if (!permitir) {
      await registrar(env, usuario, pregunta, '[rechazada: off-topic]');
      return json({ respuesta: 'Puedo ayudarte con todo lo del mercado del cerdo y las predicciones de la Lonja de Lleida 🐷 — precios, el porqué, tendencia, Francia/Alemania, cuándo vender, escenarios, impacto en € … Pregúntame por ahí y te lo clavo.' }, 200, cors);
    }

    // ── Correa: solo cerdo + números SIEMPRE del estado real (cero inventos) ──
    const system = `Eres el analista de mercado porcino de Grupo Jorge (empresa que sacrifica ~55.000 cerdos/semana de 120 kg). Hablas claro, cercano y con criterio, como un analista veterano que se moja.

ÁMBITO: mercado del cerdo, Lonja de Lleida (Mercolleida), precios en €/kg, países vecinos (Francia, Alemania, Dinamarca), tu modelo de predicción y las decisiones de venta. Mantén SIEMPRE el hilo: si el usuario dice "y por qué", "desarróllalo", "y eso", "en serio", "amplía"… se refiere a lo último que hablasteis, responde a eso. Si te piden algo TOTALMENTE ajeno al cerdo (recetas, deportes, código, política…), recházalo con simpatía en una frase y reconduce al mercado — sin cortar la conversación en seco.

REGLA DE ORO — CERO INVENTOS: los NÚMEROS (precios, semanas, fechas, errores, deltas) los coges EXCLUSIVAMENTE del ESTADO de abajo. NUNCA inventes una cifra. Si un dato no está en el ESTADO, dilo claramente. La cotización que predecimos es "Cerdo Blanco" (real de la Lonja, €/kg vivo, 3 decimales), no el equivalente.

Impacto para el negocio: 1 céntimo/kg ≈ 66.000 €/semana ≈ 3,3 M€/año (55.000 cerdos × 120 kg). Cuando ayude, traduce los céntimos a € para su volumen.

ESTADO ACTUAL (datos reales del modelo, úsalos tal cual):
${JSON.stringify(estado)}

Responde en español. Por defecto BREVE (3-6 frases); si te piden "desarrolla/amplía/explica más", extiéndete y estructura con guiones. Usa € y céntimos. Explica el porqué apoyándote en Francia/Alemania y la estacionalidad del ESTADO. No prometas certezas: es una predicción, y dilo cuando toque.`;

    let respuesta;
    try {
      const r = await fetch('https://api.anthropic.com/v1/messages', {
        method: 'POST',
        headers: {
          'x-api-key': env.ANTHROPIC_API_KEY,
          'anthropic-version': '2023-06-01',
          'content-type': 'application/json',
        },
        body: JSON.stringify({
          model: 'claude-haiku-4-5-20251001',
          max_tokens: 800,
          system,
          messages: mensajes,
        }),
      });
      const data = await r.json();
      respuesta = data?.content?.[0]?.text
        || (data?.error?.message ? '⚠️ ' + data.error.message : 'No he podido responder ahora mismo, prueba otra vez.');
    } catch (e) {
      respuesta = 'No he podido conectar ahora mismo, prueba otra vez en un momento.';
    }

    await registrar(env, usuario, pregunta, respuesta);
    return json({ respuesta }, 200, cors);
  },
};

// Log compartido opcional (si más adelante conectas un KV llamado LOG).
async function registrar(env, usuario, pregunta, respuesta) {
  try {
    if (!env.LOG) return;
    const key = `q:${Date.now()}:${Math.random().toString(36).slice(2, 7)}`;
    await env.LOG.put(key, JSON.stringify({ t: new Date().toISOString(), usuario, pregunta, respuesta }));
  } catch (_) {}
}

function json(obj, status, cors) {
  return new Response(JSON.stringify(obj), { status, headers: { ...cors, 'content-type': 'application/json' } });
}
