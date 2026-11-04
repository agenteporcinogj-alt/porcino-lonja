// Carga el <script> principal de docs/index.html en un sandbox de Node (sin navegador)
// y expone sus funciones puras para poder probarlas. No modifica docs/.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

function stubEl() {
  const el = {
    innerHTML: '', textContent: '', value: '', hidden: false, className: '', style: {}, dataset: {},
    classList: { add() {}, remove() {}, toggle() {} },
    addEventListener() {}, appendChild() {}, querySelector() { return null; }, querySelectorAll() { return []; },
    getBoundingClientRect() { return { left: 0, top: 0, width: 0, height: 0, right: 0 }; },
    closest() { return stubEl(); }, focus() {}, remove() {},
  };
  return el;
}

function cargarIndex(htmlPath) {
  const file = htmlPath || path.join(__dirname, '..', '..', 'docs', 'index.html');
  const html = fs.readFileSync(file, 'utf8');
  // El script principal es el <script> sin src que va tras data.enc.js
  const m = html.match(/<script src="data\.enc\.js"><\/script>\s*<script>([\s\S]*?)<\/script>/);
  if (!m) throw new Error('no encuentro el script principal en ' + file);
  const exportar = ['fmt', 'scts', 'MES', 'weekRange', 'isoWeek', 'mdToHtml', 'chartGeom', 'chatChart', 'ccFmt',
    'safeCalc', 'fmtCalc', 'buildEstado', 'buildScenario', 'salvageChart', 'chartHorizonWeeks', 'trajChartBlock',
    'computeInlineCalc', 'stripModelChartExcuse', 'cleanForLog', 'EXTRA_INSTR', 'FICHA_EMPRESA', 'DOSSIER_MERCADO',
    'norm', 'lexScores', 'decryptData'];
  const src = m[1] + '\n;globalThis.__x={' + exportar.map(n => `${n}:(typeof ${n}!=='undefined'?${n}:undefined)`).join(',') +
    ',setD:(d)=>{D=d;}};';
  const els = {};
  const ctx = {
    console, Math, JSON, Date, Number, String, Array, Object, RegExp, Uint8Array, TextEncoder, TextDecoder, Promise,
    setTimeout: () => 0, requestAnimationFrame: () => 0, performance: { now: () => 0 },
    atob: s => Buffer.from(s, 'base64').toString('binary'),
    crypto: globalThis.crypto,
    fetch: () => Promise.reject(new Error('sin red en pruebas')),
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    location: { reload() {} },
    document: {
      getElementById: id => (els[id] = els[id] || stubEl()),
      createElement: () => stubEl(), querySelector: () => null, addEventListener() {},
    },
  };
  ctx.window = ctx; ctx.window.addEventListener = () => {};
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  vm.runInContext(src, ctx, { filename: 'index.html<script>' });
  return ctx.__x;
}
module.exports = { cargarIndex };
