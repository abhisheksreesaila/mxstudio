// mxconsole: the blades console. A dark top bar, a journey of blades docked side by side, My models at home.
// Each blade is its own blade-*.js (registered on import) with its own CSS; see blades.js for the contract.
// The URL hash opens a blade (#keys, #model/<name>, #bench/<name>, ...; see routes.js) and follows what is open.
import { $, store, onData, startPolling } from "./core.js";
import { mountJourney, push, truncate, stack, onStackChange, isRegistered } from "./blades.js";
import { mountTopbar } from "./topbar.js";
import { startSystemPolling } from "./system-data.js";
import { parseHash, hashFor } from "./routes.js";
import "./blade-home.js";
import "./blade-model.js";
import "./blade-add.js";
import "./blade-security.js";
import "./blade-keys.js";
import "./blade-bench.js";
import "./blade-logs.js";
import "./blade-notebook.js";
import "./blade-gateway.js";
import "./blade-settings.js";

const WAIT_FOR_MODELS_MS = 4000;

/** Open what the hash names. A route that needs a model but names none takes the first one (once the list has loaded). */
async function openHash(hash) {
  const plan = parseHash(hash);
  if (plan.redirect) { location.replace(plan.redirect); return; }
  let steps = plan.steps;
  if (plan.needsModel) {
    await modelsLoaded();
    const first = store.models && store.models[0];
    steps = first ? parseHash(`#${plan.needsModel}/${first.name}`).steps : [];
  }
  truncate(1);
  for (const s of steps) if (isRegistered(s.name)) push(s.name, s.opts, stack.length - 1);
}
function modelsLoaded() {
  if (store.models || store.error) return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => { off(); clearTimeout(t); resolve(); };
    const off = onData(() => { if (store.models || store.error) done(); });
    const t = setTimeout(done, WAIT_FOR_MODELS_MS);
  });
}

const startHash = location.hash; // read before the stack change below rewrites it
const decode = (v) => { try { return decodeURIComponent(v); } catch (_) { return v; } };
// both sides decoded, so a name with a space or a slash compares equal
const here = () => decode(location.hash.slice(1));
const want = () => decode(hashFor(stack));
mountJourney($("journey"));
mountTopbar($("topbar"), $("hwpop"));
push("home");
startPolling();
startSystemPolling();

// the hash follows what is open (replaceState: no history entry per click, and no hashchange to answer)
onStackChange(() => {
  if (here() === want()) return;
  const hash = hashFor(stack);
  history.replaceState(null, "", `${location.pathname}${location.search}${hash ? `#${hash}` : ""}`);
});
window.addEventListener("hashchange", () => {
  if (here() !== want()) openHash(location.hash);
});
if (startHash.length > 1) openHash(startHash);
