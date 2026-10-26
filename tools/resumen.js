#!/usr/bin/env node
/*
 * Genera docs/resumen.html: una versión de solo texto del panel (sin JavaScript),
 * fácil de leer para Microsoft 365 Copilot, buscadores y lectores de pantalla.
 * Toma los datos de docs/index.html (bloque REVISADO…WEBINARS) y el estado de docs/estado.json.
 * Se ejecuta en GitHub Actions en cada revisión del vigía.
 */
'use strict';
const fs = require('fs');
const path = require('path');

const RAIZ = path.join(__dirname, '..', 'docs');
const PANEL = 'https://dmartinezs26.github.io/vigia-continuidad/';
const TZ = 'America/Santiago';

// --- datos del panel ---------------------------------------------------------
const html = fs.readFileSync(path.join(RAIZ, 'index.html'), 'utf8');
const ini = html.indexOf("var AN='");
const finWeb = html.indexOf('var WEBINARS=[', ini);
const fin = html.indexOf('];', finWeb);
if (ini < 0 || finWeb < 0 || fin < 0) throw new Error('No se encontró el bloque de datos en index.html');
// El bloque es código de este mismo repositorio (no contenido externo).
const D = new Function(html.slice(ini, fin + 2) + '\nreturn {REVISADO, AUD, KIND, ITEMS, FREEZES, WEBINARS};')();

let estado = null;
try { estado = JSON.parse(fs.readFileSync(path.join(RAIZ, 'estado.json'), 'utf8')); } catch (e) { estado = null; }

// --- utilidades ----------------------------------------------------------------
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const okUrl = u => (typeof u === 'string' && /^https:\/\//.test(u)) ? u : null;
const link = (txt, u) => okUrl(u) ? `<a href="${esc(u)}">${esc(txt)}</a>` : esc(txt);
const hoyIso = new Intl.DateTimeFormat('en-CA', {timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit'}).format(new Date());
const ms = iso => Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10));
const diff = iso => Math.round((ms(iso) - ms(hoyIso)) / 864e5);
const fecha = iso => new Intl.DateTimeFormat('es-CL', {timeZone: 'UTC', day: 'numeric', month: 'long', year: 'numeric'}).format(new Date(ms(iso)));
const horaCl = iso => new Intl.DateTimeFormat('es-CL', {timeZone: TZ, day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23'}).format(new Date(iso));
const cuando = d => d === 0 ? 'hoy' : d === 1 ? 'mañana' : d > 0 ? `en ${d} días` : `hace ${-d} días`;
const aud = i => (i.aud || []).map(a => D.AUD[a] || a).join(', ');
const NIVEL = {ok: 'Operativo', mant: 'Mantención', aviso: 'Aviso', degradado: 'Degradado', caida: 'Caída', sin_dato: 'Sin datos'};

function item(i) {
  let c;
  if (i.k === 'rel') c = `Liberado el ${fecha(i.rel)}`;
  else if (i.eff) c = `${fecha(i.eff)} (${cuando(diff(i.eff))})`;
  else c = `Anunciado el ${i.ann ? fecha(i.ann) : '—'}, sin fecha de liberación`;
  return `<li><strong>${esc(i.t)}</strong> — ${esc(c)}.<br>` +
    `Qué cambia: ${esc(i.s)}<br>` +
    `A quién afecta: ${esc(aud(i))}. Impacto: ${esc(i.imp || 'Sin dato')}. ${esc(i.act || '')}.<br>` +
    (i.do ? `Qué hacer: ${esc(i.do)}<br>` : '') +
    `Original: ${link(i.en, i.url)}</li>`;
}
const lista = arr => arr.length ? `<ul>\n${arr.map(item).join('\n')}\n</ul>` : '<p>Sin elementos.</p>';

// --- secciones -----------------------------------------------------------------
const I = D.ITEMS;
const urgente = I.filter(i => i.k !== 'rel' && i.eff && diff(i.eff) >= -7 && diff(i.eff) <= 30 &&
  (i.imp === 'Alto' || i.imp === 'Medio' || i.act === 'Acción requerida')).sort((a, b) => a.eff.localeCompare(b.eff));
const deps = I.filter(i => i.k === 'dep').sort((a, b) => (a.eff || '9999').localeCompare(b.eff || '9999'));
const viene = I.filter(i => i.k === 'ui' || i.k === 'plat').sort((a, b) => (b.ann || '').localeCompare(a.ann || ''));
const lib = I.filter(i => i.k === 'rel').sort((a, b) => b.rel.localeCompare(a.rel));
const apis = I.filter(i => i.k === 'api').sort((a, b) => (b.eff || b.ann || '').localeCompare(a.eff || a.ann || ''));
const frz = D.FREEZES.filter(f => f.to >= hoyIso);
const web = D.WEBINARS.filter(w => Date.parse(w.at) > Date.now());

let est = '<p>El vigía todavía no ha reportado.</p>';
if (estado && estado.revisado) {
  const edad = Math.round((Date.now() - Date.parse(estado.revisado)) / 60000);
  const viejo = edad > 20;
  est = `<p><strong>${esc(viejo ? 'Sin actualizar' : (NIVEL[estado.nivel] || estado.nivel))}: ${esc(estado.titular)}</strong>. ` +
    `Revisado el ${esc(horaCl(estado.revisado))} (hora de Chile)${viejo ? `, hace ${edad} minutos: puede estar desactualizado` : ''}.</p>\n` +
    `<ul>\n${(estado.servicios || []).map(s => `<li>${esc(s.n)}: <strong>${esc(NIVEL[s.estado] || s.estado)}</strong> — ${esc(s.nota)}</li>`).join('\n')}\n</ul>\n` +
    ((estado.incidentes || []).length
      ? `<p>Incidentes que nos afectan:</p>\n<ul>\n${estado.incidentes.map(i => `<li>${link(i.t, i.url)} [${esc(i.region)}] — ${esc(i.estado)}</li>`).join('\n')}\n</ul>`
      : '<p>Sin incidentes abiertos en use1 ni sae1.</p>');
}

const out = `<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Continuidad Genesys Cloud – resumen en texto</title>
<meta name="description" content="Estado de Genesys Cloud, AWS y Microsoft para use1 y sae1, deprecaciones, cambios anunciados y liberados de Genesys Cloud. Versión de solo texto del panel de Continuidad Operacional de e-contact.">
<style>body{font-family:system-ui,Arial,sans-serif;max-width:860px;margin:24px auto;padding:0 16px;line-height:1.5;color:#16151C;background:#fff}h2{margin-top:32px;border-bottom:1px solid #ddd;padding-bottom:4px}li{margin:8px 0}small{color:#555}</style>
</head>
<body>
<h1>Continuidad Genesys Cloud – resumen</h1>
<p><small>Versión de solo texto del <a href="${PANEL}">panel de continuidad</a>. Generado el ${esc(horaCl(new Date().toISOString()))} (hora de Chile). Noticias revisadas el ${esc(fecha(D.REVISADO))}. Regiones de Genesys Cloud: use1 (Americas US East, N. Virginia) y sae1 (Americas São Paulo). Los cambios llegan a sae1 los martes y a use1 los jueves, desde la 01:00 hora local.</small></p>

<h2>Estado de servicio</h2>
${est}

<h2>Urgente (vence en 30 días o entró en vigor hace 7 días o menos)</h2>
${lista(urgente)}

<h2>Deprecaciones</h2>
${lista(deps)}

<h2>Lo que viene (anunciado, aún no liberado)</h2>
${lista(viene)}

<h2>Liberado recientemente</h2>
${lista(lib)}

<h2>Cambios de API e integraciones</h2>
${lista(apis)}

<h2>Semanas sin liberaciones (congelamientos)</h2>
${frz.length ? `<ul>\n${frz.map(f => `<li>${esc(fecha(f.from))} al ${esc(fecha(f.to))}: ${esc(f.why)}</li>`).join('\n')}\n</ul>` : '<p>Sin congelamientos próximos.</p>'}

<h2>Webinars de Genesys</h2>
${web.length ? `<ul>\n${web.map(w => `<li>${esc(horaCl(w.at))} (hora de Chile): ${esc(w.n)} — ${esc(w.x)}</li>`).join('\n')}\n</ul>` : '<p>Sin webinars próximos.</p>'}

<h2>Fuentes oficiales</h2>
<ul>
<li><a href="https://status.mypurecloud.com/">Estado de Genesys Cloud</a></li>
<li><a href="https://help.genesys.cloud/announcements/">Anuncios de Genesys Cloud</a></li>
<li><a href="https://help.genesys.cloud/release-notes/genesys-cloud/">Notas de actualización de Genesys Cloud</a></li>
<li><a href="https://health.aws.amazon.com/health/status">Estado de AWS</a></li>
<li><a href="https://azure.status.microsoft/">Estado de Azure</a></li>
</ul>
</body>
</html>
`;
fs.writeFileSync(path.join(RAIZ, 'resumen.html'), out);
console.log(`resumen.html: ${urgente.length} urgentes, ${deps.length} deprecaciones, ${viene.length} por venir, ${lib.length} liberados, ${apis.length} APIs`);
