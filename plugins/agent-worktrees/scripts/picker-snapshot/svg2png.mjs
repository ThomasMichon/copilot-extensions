// Rasterize a picker capture SVG to PNG -- the standing NF A/B / demo render
// flow (agent-worktrees #88).
//
// WHY THIS EXISTS (do not "simplify" by swapping the font): the picker's capture
// SVG is emitted by Rich, which positions every glyph at an x-coordinate computed
// from *Fira Code*'s metrics and names it via a CDN @font-face. If you rasterize
// with a different monospace (e.g. Consolas) its glyph advance won't match Rich's
// cell grid, so the box-drawing BORDER characters stop tiling and render choppy.
// So we always render with the SVG's own Fira Code (auto-downloaded + cached to
// ./.fonts); loadSystemFonts only covers the few glyphs Fira Code lacks (the gear).
//
// Usage:  node svg2png.mjs <in.svg> <out.png> [zoom=3]
import { Resvg } from '@resvg/resvg-js';
import fs from 'node:fs';
import path from 'node:path';
import https from 'node:https';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FONT_DIR = path.join(HERE, '.fonts');
const FONTS = {
  'FiraCode-Regular.ttf':
    'https://cdnjs.cloudflare.com/ajax/libs/firacode/6.2.0/ttf/FiraCode-Regular.ttf',
  'FiraCode-Bold.ttf':
    'https://cdnjs.cloudflare.com/ajax/libs/firacode/6.2.0/ttf/FiraCode-Bold.ttf',
};

function download(url, dest) {
  return new Promise((resolve, reject) => {
    const f = fs.createWriteStream(dest);
    https
      .get(url, (res) => {
        if (res.statusCode !== 200) {
          reject(new Error(`HTTP ${res.statusCode} for ${url}`));
          return;
        }
        res.pipe(f);
        f.on('finish', () => f.close(resolve));
      })
      .on('error', reject);
  });
}

async function ensureFonts() {
  fs.mkdirSync(FONT_DIR, { recursive: true });
  const files = [];
  for (const [name, url] of Object.entries(FONTS)) {
    const p = path.join(FONT_DIR, name);
    if (!fs.existsSync(p)) {
      process.stderr.write(`picker-snapshot: fetching ${name}...\n`);
      await download(url, p);
    }
    files.push(p);
  }
  return files;
}

const [inp, outp, zoomArg] = process.argv.slice(2);
if (!inp || !outp) {
  console.error('usage: node svg2png.mjs <in.svg> <out.png> [zoom=3]');
  process.exit(2);
}
const zoom = Number(zoomArg || 3);
const fontFiles = await ensureFonts();
const svg = fs.readFileSync(inp, 'utf8');
const resvg = new Resvg(svg, {
  background: '#0c0c0c',
  fitTo: { mode: 'zoom', value: zoom },
  font: { fontFiles, loadSystemFonts: true, defaultFontFamily: 'Fira Code' },
});
fs.writeFileSync(outp, resvg.render().asPng());
console.log(`picker-snapshot: wrote ${outp} (Fira Code, zoom ${zoom})`);
