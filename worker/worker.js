// ── Cerebro del chat IA de Grupo Jorge (Cloudflare Worker) ──
// La API key va como SECRETO en Cloudflare (env.ANTHROPIC_API_KEY): nunca viaja al navegador.
// La web manda: { pregunta, estado (números reales del modelo), usuario }
// El Worker: filtra off-topic (gratis) → llama a Claude con correa "solo cerdo" → registra la pregunta.

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

    const pregunta = String(body.pregunta || '').slice(0, 500).trim();
    const estado = body.estado || {};
    const usuario = String(body.usuario || 'anon').slice(0, 40);
    if (!pregunta) return json({ respuesta: 'Escríbeme una pregunta 🐷' }, 200, cors);

    // ── PORTERO gratis: ¿esto es de cerdo/mercado? ──
    const txt = pregunta.toLowerCase();
    const TEMAS = ['cerdo','porcino','precio','lonja','lleida','mercolleida','cotiz','francia','aleman',
      'dinamarca','españa','espana','semana','predic','model','matader','canal','vivo','kg','céntimo',
      'centimo','euro','€','mercado','tendencia','sub','baj','vend','aguant','china','ppa','peste',
      'pienso','censo','matanza','jorge','vecino','estacional','cebo','lechon','capa','exporta','demanda','oferta'];
    const esTema = TEMAS.some(t => txt.includes(t));
    const esSaludo = /^(hola|buenas|hey|ey|hi|holi|qué tal|que tal|buenos|buenass)/.test(txt) || txt.length < 5;
    if (!esTema && !esSaludo) {
      await registrar(env, usuario, pregunta, '[rechazada: off-topic]');
      return json({ respuesta: 'Solo puedo ayudarte con el mercado del cerdo y las predicciones de la Lonja de Lleida 🐷. Pregúntame por precios, tendencia, Francia/Alemania, cuándo vender…' }, 200, cors);
    }

    // ── Correa: solo cerdo + números SIEMPRE del estado real (cero inventos) ──
    const system = `Eres el analista de mercado porcino de Grupo Jorge (empresa que sacrifica ~55.000 cerdos/semana de 120 kg).
Hablas SOLO del mercado del cerdo, la Lonja de Lleida (Mercolleida), precios en €/kg, países vecinos (Francia, Alemania, Dinamarca) y las predicciones de nuestro modelo. Si te preguntan de cualquier otra cosa, recházalo con educación y reconduce al cerdo.

REGLA DE ORO — CERO INVENTOS: los NÚMEROS (precios, semanas, fechas, errores) los coges EXCLUSIVAMENTE del ESTADO de abajo. NUNCA inventes una cifra. Si un dato no está en el ESTADO, di claramente que no lo tienes. La cotización que predecimos es "Cerdo Blanco" (real de la Lonja, €/kg vivo, 3 decimales), no el equivalente.

Dato de impacto para el negocio: 1 céntimo/kg ≈ 66.000 €/semana ≈ 3,3 M€/año.

ESTADO ACTUAL (datos reales del modelo, úsalos tal cual):
${JSON.stringify(estado)}

Responde en español, cercano pero profesional, claro y BREVE (3-6 frases). Usa € y céntimos. Cuando expliques el porqué de una subida/bajada, apóyate en Francia/Alemania y la estacionalidad que veas en el ESTADO. No prometas certezas: es una predicción.`;

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
          max_tokens: 500,
          system,
          messages: [{ role: 'user', content: pregunta }],
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
