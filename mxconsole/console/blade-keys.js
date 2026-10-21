// mxconsole, the API keys blade (F-025): every app key (prefix only), New (the secret is shown once), Rotate (shown once,
// says when it brings a revoked app back) and Revoke (second click confirms). Each action shows its `mxc keys` twin.
// Only this file and blade-keys.css change for it.
import { h, icon, onData, api, ago, dateTime, toast, copyButton } from "./core.js";
import { register } from "./blades.js";
import { syncKeyed } from "./keyed.js";

/** The mxc command that does the same. */
export function keyTwin(action, app) {
  return action === "ls" ? "mxc keys ls" : `mxc keys ${action} ${app}`;
}
/** Names the backend accepts: letters, digits, '.', '_' and '-'; "_" is reserved for mxconsole's own keys. */
export function validAppName(name) {
  return /^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(String(name || ""));
}
/** /admin/keys rows as the table shows them. `name` is the key for in-place updates. */
export function keyRows(list) {
  return (list || []).map((k) => ({
    name: k.app, prefix: k.prefix, created: k.created_at ? dateTime(k.created_at) : "—", lastUsed: k.last_used_at ? ago(k.last_used_at) : "never",
    status: k.revoked_at ? "revoked" : "active", active: !k.revoked_at,
  }));
}

register("keys", (blade) => ({
  title: "API keys", crumb: "API keys", sub: "shared by every model · requests without a valid key get 401",
  icon: "key-round", hue: "var(--hue-keys)", side: 640, narrow: 520, min: 560,
  tools: [],
  mount(body) {
    let rows = null, loadedAt = 0, error = "", formOpen = false;
    const cache = new Map();
    const timers = new Map();  // app -> revoke-confirm timeout

    // -- skeleton ------------------------------------------------------------------------------
    const nameInput = h("input", { class: "mono", placeholder: " ", "aria-label": "App name", maxlength: "64", autocomplete: "off", spellcheck: "false" });
    const nameField = h("label", { class: "mo" }, nameInput, h("span", { class: "fl" }, "App name, e.g. my-app"));
    const formErr = h("div", { class: "mf-help err", role: "alert", hidden: true });
    const create = h("button", { class: "btn fill sm", type: "submit" }, icon("plus", 13, "currentColor", 2), "Create key");
    const form = h("form", { class: "card", hidden: true, onSubmit: (e) => { e.preventDefault(); newKey(); } },
      h("div", { class: "card-h" }, h("h3", {}, "New key")),
      h("div", { class: "card-b" }, nameField, formErr, h("div", { class: "ox-row kn-acts" }, create, h("button", { class: "btn text sm", type: "button", onClick: () => toggleForm(false) }, "Cancel"))),
      h("div", { class: "src" }, "mxc keys new <app>"));
    const revealTitle = h("h3", {}), revealNote = h("div", { class: "ox-note" }), revealKey = h("code", { class: "secret mono" });
    const revealCopy = h("span", {}), revealSrc = h("div", { class: "src" });
    const reveal = h("section", { class: "card kn-reveal", role: "status", hidden: true },
      h("div", { class: "card-h" }, h("span", { class: "ox-tile", style: { "--c": "var(--oq-ok)" } }, icon("key-round", 18, "currentColor", 1.75)), revealTitle, h("span", { class: "sp" }),
        h("button", { class: "xbtn", type: "button", "aria-label": "Done", title: "Done", onClick: clearSecret }, icon("x", 16))),
      h("div", { class: "card-b" }, revealNote, h("div", { class: "keyline" }, revealKey, revealCopy)), revealSrc);
    const tbody = h("tbody", {});
    const empty = h("div", { class: "empty", hidden: true }, h("div", { class: "ring" }, icon("key-round", 20)), h("b", {}, "No keys yet"), "Create one for each app that calls your models.",
      h("div", {}, h("button", { class: "btn fill sm", type: "button", onClick: () => toggleForm(true) }, icon("plus", 13, "currentColor", 2), "New key")));
    const note = h("div", { class: "ox-fail", role: "alert", hidden: true });
    const table = h("section", { class: "card" }, h("div", { class: "card-h" }, h("h3", {}, "Keys"), h("span", { class: "sp" }), h("span", { class: "badge" }, "prefix only")),
      h("div", { class: "card-b ox-flush ox-scroll" }, h("table", { class: "tbl keys" },
        h("thead", {}, h("tr", {}, ["App", "Key", "Created", "Last used", "Status", ""].map((t) => h("th", {}, t)))), tbody), empty, note),
      h("div", { class: "src" }, "mxc keys ls"));
    blade.el.classList.add("oq-flat");
    body.replaceChildren(h("div", { class: "ox-pad" }, form, reveal, table));
    const newBtn = h("button", { class: "btn fill sm", type: "button", "data-act": "new", onClick: () => toggleForm(!formOpen) }, icon("plus", 13, "currentColor", 2), "New key");
    blade.toolsEl.replaceChildren(newBtn);

    function toggleForm(open) {
      formOpen = open; form.hidden = !open; formErr.hidden = true; nameField.classList.remove("err");
      if (open) { nameInput.value = ""; nameInput.focus(); }
    }
    function clearSecret() { reveal.hidden = true; revealKey.textContent = ""; revealCopy.replaceChildren(); }
    const formError = (message) => { formErr.hidden = !message; formErr.textContent = message; nameField.classList.toggle("err", !!message); };
    function showSecret(app, key, title, text, twin) {
      formError("");
      revealTitle.textContent = title;
      revealNote.textContent = text;
      revealKey.textContent = key;
      revealCopy.replaceChildren(copyButton(() => key, "Copy", "btn tonal sm"));
      revealSrc.textContent = twin;
      reveal.hidden = false;
    }

    // -- data and actions ----------------------------------------------------------------------
    async function load() {
      loadedAt = Date.now();
      try { rows = keyRows(await api("GET", "/admin/keys")); error = ""; } catch (e) { error = e.message; }
      draw();
    }
    async function newKey() {
      const app = nameInput.value.trim();
      formError("");
      if (!validAppName(app)) { formError("Use letters, digits, '.', '_' or '-', starting with a letter or digit."); return; }
      create.disabled = true;
      try {
        const out = await api("POST", "/admin/keys", { app });
        toggleForm(false);
        showSecret(app, out.key, `Key for ${app}`, "Copy it now. It is shown once and cannot be recovered; rotate it if you lose it.", keyTwin("new", app));
        toast(`Created ${app} · $ ${keyTwin("new", app)}`);
      } catch (e) { formError(e.status === 409 ? `There is already a key for ${app}. Rotate it instead.` : e.message); }
      create.disabled = false;
      load();
    }
    async function rotate(app) {
      try {
        const out = await api("POST", `/admin/keys/${encodeURIComponent(app)}/rotate`);
        showSecret(app, out.key, `New key for ${app}`, out.revived ? "Shown once. The old secret stopped working, and this brings the revoked app back to life." : "Shown once. The old secret has stopped working: update the app that uses it.", keyTwin("rotate", app));
        toast(`Rotated ${app} · $ ${keyTwin("rotate", app)}`);
      } catch (e) { toast(`Could not rotate ${app}: ${e.message}`); }
      load();
    }
    async function revoke(app) {
      try { await api("DELETE", `/admin/keys/${encodeURIComponent(app)}`); toast(`Revoked ${app} · $ ${keyTwin("revoke", app)}`); } catch (e) { toast(`Could not revoke ${app}: ${e.message}`); }
      load();
    }

    // -- rows, one element per app, updated in place -------------------------------------------
    function makeRow(r) {
      const cells = Array.from({ length: 6 }, () => h("td", {}));
      const tr = h("tr", { class: "krow", "data-id": r.name }, ...cells);
      const rot = h("button", { class: "btn tonal sm", type: "button", onClick: () => confirmAct(r.name, rot, "Rotate", "Click again to rotate: the old key stops working now", rotate) }, icon("rotate-cw", 12, "currentColor", 1.75), h("span", {}, "Rotate"));
      const rev = h("button", { class: "btn danger sm", type: "button", onClick: () => confirmAct(r.name, rev, "Revoke", "Click again to revoke", revoke) }, icon("trash-2", 12, "currentColor", 1.75), h("span", {}, "Revoke"));
      cells[5].append(h("div", { class: "acts" }, rot, rev));
      Object.assign(tr, { _cells: cells, _rot: rot, _rev: rev });
      return tr;
    }
    function confirmAct(app, btn, label, armedLabel, run) {  // the second click confirms; one armed button at a time per row
      const key = `${app}:${label}`;
      if (!btn.classList.contains("armed")) {
        btn.classList.add("armed"); btn.lastChild.textContent = armedLabel;
        timers.set(key, setTimeout(() => { btn.classList.remove("armed"); btn.lastChild.textContent = label; }, 4000));
        return;
      }
      clearTimeout(timers.get(key));
      btn.classList.remove("armed"); btn.lastChild.textContent = label;
      run(app);
    }
    function updateRow(tr, r) {
      const [app, prefix, created, used, status] = tr._cells;
      app.className = "nm mono"; app.textContent = r.name;
      prefix.className = "mono ink2"; prefix.textContent = `${r.prefix}…`;
      created.className = "ink2"; created.textContent = r.created;
      used.className = "ink2"; used.textContent = r.lastUsed;
      status.replaceChildren(h("span", { class: `ox-st ${r.active ? "run" : ""}` }, h("i"), r.status));
      tr._rot.title = r.active ? `$ ${keyTwin("rotate", r.name)}` : `$ ${keyTwin("rotate", r.name)} (brings it back)`;
      tr._rev.title = `$ ${keyTwin("revoke", r.name)}`;
      tr._rev.disabled = !r.active;
    }
    function draw() {
      if (!blade.alive) return;
      note.hidden = !error || rows !== null && rows.length > 0;
      note.replaceChildren(...(error ? [icon("triangle-alert", 15), `Could not load keys: ${error}`] : []));
      table.querySelector("table").hidden = !rows || rows.length === 0;
      empty.hidden = !(rows && rows.length === 0 && !error);
      if (rows) syncKeyed(tbody, cache, rows, makeRow, updateRow);
    }
    blade.redraw = draw;
    blade.onClose = (() => {
      const off = onData(() => { if (Date.now() - loadedAt > 10000) load(); });  // "last used" moves while you look
      return () => { off(); timers.forEach(clearTimeout); clearSecret(); };
    })();
    load();
  },
}));
