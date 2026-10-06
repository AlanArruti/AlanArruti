// Genera languages-light.svg y languages-dark.svg con los lenguajes de todos los
// repos a los que accede el token: propios y como colaborador, incluidos privados.
import { writeFileSync } from 'node:fs';

const TOKEN = process.env.GH_TOKEN;
if (!TOKEN) {
  console.error('Falta el secret LANGS_TOKEN (token classic con scope "repo").');
  process.exit(1);
}

const EXCLUDE = new Set(
  (process.env.EXCLUDE_LANGS || '').split(',').map((s) => s.trim().toLowerCase()).filter(Boolean),
);
const MAX_LANGS = 8; // el resto se agrupa en "Otros"
const MIN_SHARE = 0.01; // lenguajes por debajo del 1% también van a "Otros"

const QUERY = `query ($cursor: String) {
  viewer {
    repositories(first: 100, after: $cursor, isFork: false, ownerAffiliations: [OWNER, COLLABORATOR]) {
      pageInfo { hasNextPage endCursor }
      nodes {
        languages(first: 50, orderBy: { field: SIZE, direction: DESC }) {
          edges { size node { name color } }
        }
      }
    }
  }
}`;

async function graphql(variables) {
  const res = await fetch('https://api.github.com/graphql', {
    method: 'POST',
    headers: { Authorization: `bearer ${TOKEN}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ query: QUERY, variables }),
  });
  const json = await res.json();
  if (!res.ok || json.errors) throw new Error(JSON.stringify(json.errors ?? json));
  return json.data;
}

const totals = new Map();
let cursor = null;
do {
  const { repositories } = (await graphql({ cursor })).viewer;
  for (const repo of repositories.nodes) {
    const edges = repo.languages.edges.filter((e) => !EXCLUDE.has(e.node.name.toLowerCase()));
    for (const { size, node } of edges) {
      const lang = totals.get(node.name) ?? { name: node.name, color: node.color, size: 0 };
      lang.size += size;
      totals.set(node.name, lang);
    }
  }
  cursor = repositories.pageInfo.hasNextPage ? repositories.pageInfo.endCursor : null;
} while (cursor);

const total = [...totals.values()].reduce((sum, l) => sum + l.size, 0);
if (!total) throw new Error('No se encontraron lenguajes: revisá los permisos del token.');

const sorted = [...totals.values()].sort((a, b) => b.size - a.size);
const shown = sorted.filter((l, i) => i < MAX_LANGS && l.size / total >= MIN_SHARE);
const otherSize = total - shown.reduce((sum, l) => sum + l.size, 0);
const items = shown.map((l) => ({ ...l, share: l.size / total }));
if (otherSize > 0) items.push({ name: 'Otros', color: null, size: otherSize, share: otherSize / total });

const THEMES = {
  light: { text: '#1f2328', muted: '#59636e', other: '#818b98' },
  dark: { text: '#f0f6fc', muted: '#9198a1', other: '#656c76' },
};
const FONT = `-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', Helvetica, Arial, sans-serif`;

const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const pct = (share) => (share < 0.001 ? '<0.1%' : `${(share * 100).toFixed(1)}%`);

function render(theme) {
  const W = 480;
  const GAP = 2;
  const BAR_Y = 4;
  const BAR_H = 10;
  const ROW_H = 24;
  const LEGEND_Y = 44;
  const colW = W / 2;
  const rows = Math.ceil(items.length / 2);
  const H = LEGEND_Y + (rows - 1) * ROW_H + 8;
  const color = (item) => item.color ?? theme.other;

  // Barra apilada: cada segmento proporcional, separados por un hueco de 2px.
  const usable = W - GAP * (items.length - 1);
  let x = 0;
  const segments = items.map((item) => {
    const w = Math.max(2, usable * item.share);
    const rect = `<rect x="${x.toFixed(2)}" y="${BAR_Y}" width="${w.toFixed(2)}" height="${BAR_H}" fill="${color(item)}"/>`;
    x += w + GAP;
    return rect;
  });

  // Leyenda en dos columnas, ordenada de arriba hacia abajo.
  const legend = items.map((item, i) => {
    const lx = i < rows ? 0 : colW + 12;
    const ly = LEGEND_Y + (i % rows) * ROW_H;
    return [
      `<circle cx="${lx + 5}" cy="${ly - 4.5}" r="5" fill="${color(item)}"/>`,
      `<text x="${lx + 18}" y="${ly}" class="name">${esc(item.name)}</text>`,
      `<text x="${lx + colW - 24}" y="${ly}" class="pct" text-anchor="end">${pct(item.share)}</text>`,
    ].join('');
  });

  const desc = items.map((item) => `${item.name} ${pct(item.share)}`).join(', ');

  return `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-labelledby="title desc">
<title id="title">Lenguajes más usados</title>
<desc id="desc">${esc(desc)}</desc>
<style>
text { font-family: ${FONT}; }
.name { font-size: 13px; fill: ${theme.text}; }
.pct { font-size: 13px; fill: ${theme.muted}; font-variant-numeric: tabular-nums; }
</style>
<defs><clipPath id="bar"><rect x="0" y="${BAR_Y}" width="${W}" height="${BAR_H}" rx="4"/></clipPath></defs>
<g clip-path="url(#bar)">${segments.join('')}</g>
${legend.join('\n')}
</svg>
`;
}

writeFileSync('languages-light.svg', render(THEMES.light));
writeFileSync('languages-dark.svg', render(THEMES.dark));
console.log(items.map((i) => `${i.name} ${pct(i.share)}`).join(', '));
