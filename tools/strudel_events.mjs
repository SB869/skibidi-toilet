// Query a Strudel program's events without a browser: what the scheduler would trigger, cycle by cycle.
// usage: node tools/strudel_events.mjs CODE.js FROM TO > events.json
//   prints a JSON list of the events that start in [FROM, TO) cycles: {"b": begin, "e": end, "v": {s, note, gain, ...}}
// Uses the app's own Strudel packages (web/node_modules: core, mini, transpiler), so patterns, mini-notation, perlin and the
// seeded randomness (degradeBy) evaluate exactly as in the app; sound functions (samples, setcpm) are no-ops here.
import { register } from 'node:module';
import { readFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';

const W = fileURLToPath(new URL('../web/node_modules/', import.meta.url));
// one copy of @strudel/core (its source, which mini imports too); its repl's live-coding audio dependency stubbed
const hooks = `
const MAP = { '@strudel/core': ${JSON.stringify(pathToFileURL(W + '@strudel/core/index.mjs').href)},
              '@strudel/mini': ${JSON.stringify(pathToFileURL(W + '@strudel/mini/index.mjs').href)} };
export async function resolve(spec, ctx, next) {
  if (spec === '@kabelsalat/web') return { url: 'data:text/javascript,export class SalatRepl {}', shortCircuit: true };
  if (MAP[spec]) return { url: MAP[spec], shortCircuit: true };
  return next(spec, ctx);
}`;
register('data:text/javascript,' + encodeURIComponent(hooks));

const [file, from, to] = process.argv.slice(2);
if (!file) { console.error('usage: node tools/strudel_events.mjs CODE.js FROM TO'); process.exit(2); }
const log = console.log; console.log = () => {};          // the packages greet on load
const core = await import('@strudel/core');
const mini = await import('@strudel/mini');
const { transpiler } = await import(pathToFileURL(W + '@strudel/transpiler/index.mjs').href);
let cps = 0.5;
await core.evalScope(core, mini, { samples: () => {}, setcpm: (c) => { cps = c / 60; }, setcps: (c) => { cps = c; } });
const { pattern } = await core.evaluate(readFileSync(file, 'utf8'), transpiler);
console.log = log;
const haps = pattern.queryArc(Number(from ?? 0), Number(to ?? 1)).filter((h) => h.hasOnset());
haps.sort((a, b) => a.whole.begin.valueOf() - b.whole.begin.valueOf());
const out = haps.map((h) => ({ b: h.whole.begin.valueOf(), e: h.whole.end.valueOf(), v: h.value }));
process.stdout.write(JSON.stringify({ cps, events: out }));
