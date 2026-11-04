// Pruebas de las funciones puras de docs/index.html (formato español, gráficos, markdown, calculadora...)
// y de los fallos de la web: cada fallo se comprueba en el index.html ACTUAL (sigue ahí) y en el index.html
// con propuestas/index_html.patch aplicado en una copia temporal (queda arreglado). docs/ no se modifica.
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');
const { cargarIndex } = require('./cargar_index.js');

const ROOT = path.join(__dirname, '..', '..');
// En produccion el parche ya esta aplicado: la web 'actual' ya no tiene estos fallos.
const YA_PARCHEADO = !fs.existsSync(path.join(ROOT, 'propuestas', 'index_html.patch'));
const ACTUAL = cargarIndex();
function conParche() {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'idx-'));
  fs.mkdirSync(path.join(tmp, 'docs'));
  fs.copyFileSync(path.join(ROOT, 'docs', 'index.html'), path.join(tmp, 'docs', 'index.html'));
  { const _p=path.join(ROOT, 'propuestas', 'index_html.patch'); if (fs.existsSync(_p)) execFileSync('git', ['apply', _p], { cwd: tmp }); }
  return cargarIndex(path.join(tmp, 'docs', 'index.html'));
}
const PARCHE = conParche();
const AMBOS = [['actual', ACTUAL], ['parche', PARCHE]];

// ---------- funciones puras (deben ir bien en las dos versiones) ----------
for (const [n, X] of AMBOS) {
  test(`[${n}] fmt / scts en formato español`, () => {
    assert.equal(X.fmt(1.137, 3), '1,137');
    assert.equal(X.fmt(66000, 0), '66.000');
    assert.equal(X.fmt(0.5), '0,50');
    assert.equal(X.scts(-3.1), '-3,1 cts');
    assert.equal(X.scts(2), '+2,0 cts');
    assert.equal(X.fmtCalc(13.4 * 66000), '884.400');
  });

  test(`[${n}] isoWeek y weekRange (años normales y de 53 semanas)`, () => {
    assert.equal(X.isoWeek(new Date(2026, 10, 3)), 45);
    assert.equal(X.isoWeek(new Date(2026, 11, 31)), 53);
    assert.equal(X.isoWeek(new Date(2027, 0, 1)), 53);
    assert.equal(X.weekRange(2026, 45), '2 nov–8 nov');
    assert.equal(X.weekRange(2026, 1), '29 dic–4 ene');
    assert.equal(X.weekRange(2026, 53), '28 dic–3 ene');
    assert.equal(X.weekRange(2027, 1), '4 ene–10 ene');
  });

  test(`[${n}] chartGeom: escalas y casos límite`, () => {
    const g = X.chartGeom({ labels: ['a', 'b', 'c'], series: [{ name: 's', data: [1, 2, 3] }] });
    assert.equal(g.N, 3); assert.ok(g.lo < 1 && g.hi > 3);
    assert.equal(g.X(0), g.mL); assert.equal(g.X(2), g.W - g.mR);
    assert.ok(g.Y(g.hi) === g.mT && Math.abs(g.Y(g.lo) - (g.H - g.mB)) < 1e-9);
    const uno = X.chartGeom({ series: [{ data: [5] }] });          // un punto: sin dividir por cero
    assert.ok(Number.isFinite(uno.X(0)) && uno.lo < 5 && uno.hi > 5);
    const vacio = X.chartGeom({});
    assert.equal(vacio.series.length, 0);
    const cj = X.chartGeom({ datasets: [{ label: 'x', data: [1, 'no', NaN, 2] }] });   // formato Chart.js
    assert.equal(cj.series[0].name, 'x'); assert.ok(cj.lo < 1 && cj.hi > 2);
  });

  test(`[${n}] mdToHtml escapa HTML y pinta negritas, listas y tablas`, () => {
    const h = X.mdToHtml('**hola** <img src=x onerror=alert(1)>\n- uno\n| a | b |\n|---|---|\n| 1 | <b>2</b> |');
    assert.ok(!/<img/.test(h) && h.includes('&lt;img'));
    assert.ok(h.includes('<b>hola</b>') && h.includes('• uno') && h.includes('<table'));
    assert.ok(h.includes('&lt;b&gt;2&lt;/b&gt;'));
  });

  test(`[${n}] safeCalc solo acepta aritmética`, () => {
    assert.equal(X.safeCalc('13,4*66000').val, 884400);
    assert.equal(X.safeCalc('alert(1)+1'), null);
    assert.equal(X.safeCalc('1+1\n2+2'), null);
    assert.equal(X.safeCalc('constructor.constructor("x")()'), null);
    assert.equal(X.computeInlineCalc('son [calc:2*3] €'), 'son 6 €');
  });

  test(`[${n}] salvageChart rescata datos de un Chart.js y nunca deja código`, () => {
    const raw = 'Mira:\n```html\n<canvas id="c"></canvas><script>const labels=["s1","s2"];const central=[1.1,1.2];new Chart(c,{})</script>\n```';
    const out = X.salvageChart(raw);
    assert.ok(!/<script|<canvas/.test(out) && out.includes('```chart'));
  });

  test(`[${n}] chartHorizonWeeks`, () => {
    assert.equal(X.chartHorizonWeeks('dibuja el precio del último año'), 52);
    assert.equal(X.chartHorizonWeeks('gráfica a 3 meses'), 13);
    assert.equal(X.chartHorizonWeeks('qué precio esperas'), 0);
  });
}

// ---------- fallos de la web: siguen en el actual, arreglados con el parche ----------
test('FALLO weekRange cuando el 1 de enero es domingo (2023, 2034)', () => {
  if (!YA_PARCHEADO) assert.equal(ACTUAL.weekRange(2023, 1), '26 dic–1 ene');        // mal: es la semana 52 de 2022
  assert.equal(PARCHE.weekRange(2023, 1), '2 ene–8 ene');
  assert.equal(PARCHE.weekRange(2034, 1), '2 ene–8 ene');
});

function payloadMinimo(pw, py) {
  const seas = {}; for (let w = 1; w <= 53; w++) seas[String(w)] = 100;
  return { ultimo: { y: py, w: pw - 1, v: 1.168 }, prediccion: { y: py, w: pw, v: 1.137 }, delta_cts: -3.1, seas,
    contrib: {}, vecinos: {}, precision: {}, semaforo: {}, escenarios: [], eventos: [], historial: [], serie: [], generado: 'x' };
}
test('FALLO proyección anual del chat (%52): en 2026 se salta la semana 53', () => {
  ACTUAL.setD(payloadMinimo(45, 2026));
  const a = ACTUAL.buildEstado().proyeccion_anual.etiquetas;
  if (!YA_PARCHEADO) assert.ok(!a.includes('s53/26'));                                   // mal
  PARCHE.setD(payloadMinimo(45, 2026));
  const b = PARCHE.buildEstado().proyeccion_anual.etiquetas;
  assert.deepEqual([...b.slice(6, 10)], ['s51/26', 's52/26', 's53/26', 's1/27']);
  assert.equal(new Set(b).size, 52);
  PARCHE.setD(payloadMinimo(50, 2025));                               // 2025 tiene 52: no inventa una 53
  const c = PARCHE.buildEstado().proyeccion_anual.etiquetas;
  assert.deepEqual([...c.slice(0, 4)], ['s50/25', 's51/25', 's52/25', 's1/26']);
});

test('FALLO XSS en las gráficas del chat (etiquetas, nombres y color que manda la IA)', () => {
  const malo = { type: 'line', title: 't', labels: ['<img src=x onerror=alert(1)>', 'b'],
    series: [{ name: '<img src=y onerror=alert(2)>', data: [1, 2] }, { name: 'ok', data: [2, 1], color: 'red"><img src=z onerror=alert(3)>' }] };
  const a = ACTUAL.chatChart(malo);
  if (!YA_PARCHEADO) assert.ok(/<img src=x/.test(a) && /<img src=y/.test(a) && /<img src=z/.test(a));   // mal: HTML ejecutable
  const b = PARCHE.chatChart(malo);
  assert.ok(!/<img/.test(b));
  assert.ok(b.includes('&lt;img src=x'));
});

test('el parche no cambia el dibujo de una gráfica normal', () => {
  const ok = { type: 'line', title: '**Precio**', labels: ['s45/26', 's46/26'], series: [{ name: 'Precio', data: [1.137, 1.13], color: 'var(--accent)' }, { name: 'B', data: [1.1, 1.2], color: '#5b8def' }] };
  assert.equal(PARCHE.chatChart(ok), ACTUAL.chatChart(ok));
});

test('EXTRA_INSTR es mucho más largo que el tope de 4.000 que tenía el Worker', () => {
  assert.ok(ACTUAL.EXTRA_INSTR.length > 16000);
  assert.ok(ACTUAL.EXTRA_INSTR.length < 24000);    // cabe en el nuevo tope MAX_EXTRA del Worker
});
