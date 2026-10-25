// omxconsole, the library (F-055): a search bar and a groups column (Show · Kind · Your labels), label editing,
// ticking models and labelling them. My models uses it.
// The rules live in library-logic.js (pure); this file draws them and talks to /admin/labels.
// Everything goes in as text nodes. Changes are shown at once and put back, with the server's reason, if refused.
import { h, icon, api, store, refresh, toast, emitData } from "./core.js";
import { KINDS, counts, visibleModels, bulkState, toggleOn, added, removed, created, renamed, deleted, ordered, restoredLabel, restoredOrder, cleanLabel, reorder, dragNames, optimistic } from "./library-logic.js";

const enc = encodeURIComponent;
export const kindName = (k) => (KINDS.find(([id]) => id === k) || [k, k])[1];
export function groupName(key) {
  if (key === "all") return "All models";
  if (key === "running") return "Running";
  if (key === "recent") return "Recent";
  return key.startsWith("kind:") ? kindName(key.slice(5)) : `#${key.slice(6)}`;
}
const KIND_COLOUR = { chat: "var(--oq-data-1)", coder: "var(--oq-data-4)", vision: "var(--oq-data-2)", embeddings: "var(--oq-data-3)", image: "var(--oq-warn)" };
/** A model's kind and #labels as small tags. */
export const tagsEl = (m) => h("span", { class: "tags" }, h("span", { class: "tag k", style: { "--c": KIND_COLOUR[m.kind] || "var(--oq-ink-2)" } }, kindName(m.kind)), (m.labels || []).map((l) => h("span", { class: "tag" }, `#${l}`)));

const typing = () => { const a = document.activeElement; return !!a && (/^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName) || a.isContentEditable); };

/** onChange: called whenever what the results show may have changed (search, group, ticks, labels).
 *  onRemove(names): when given, the tick bar gets a "Remove N" button that hands over the ticked names (the page asks, then removes). */
export function createLibrary({ onChange, placeholder = "Search by name, kind or #label", recent = null, onRemove = null }) {
  const lib = { q: "", group: "all", labels: [], ticked: new Set(), editing: false };
  let sig = "", shapeNow = "", barSig = "", busy = 0, adding = false;
  const models = () => store.models || [];
  const cur = () => ({ labels: lib.labels, models: models() });
  const put = (s) => { lib.loaded = true; lib.labels = s.labels; store.models = s.models; };
  const force = () => { sig = ""; shapeNow = ""; lib.update(); };
  const changed = () => { lib.update(); onChange(); };

  /** Show a change now, send it, and undo just that change (with a toast) if the daemon says no. */
  async function mutate(next, undo, request, onFail) {
    busy++;
    const ok = await optimistic({ get: cur, set: (s) => { put(s); changed(); }, next, undo, request, onError: (m) => { if (onFail) { onFail(); changed(); } toast(m); } });
    busy--;
    if (ok) await Promise.all([refresh(), lib.loadLabels()]);
    return ok;
  }

  // -- search ---------------------------------------------------------------------------------
  const input = h("input", { placeholder, autocomplete: "off", "aria-label": "Search models", onInput: (e) => { lib.q = e.target.value; onChange(); },
    onKeydown: (e) => { if (e.key === "Escape") { lib.q = input.value = ""; input.blur(); onChange(); } } });
  lib.search = h("label", { class: "psearch big" }, icon("search", 16), input, h("kbd", {}, "/"));
  lib.setPlaceholder = (text) => { input.placeholder = text; };
  lib.focusSearch = () => input.focus();
  lib.clearSearch = () => { lib.q = input.value = ""; onChange(); };
  lib.listen = () => {
    const key = (e) => { if (e.key === "/" && !typing() && !e.metaKey && !e.ctrlKey) { e.preventDefault(); input.focus(); } };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  };

  // -- the groups column ----------------------------------------------------------------------
  lib.groups = h("nav", { class: "groups", "aria-label": "Groups" });
  const pick = (key) => { lib.group = lib.group === key && key !== "all" ? "all" : key; changed(); };
  const item = (key, label, n, lead, extra = {}) => h("button", { class: `gi${lib.group === key ? " on" : ""}`, type: "button", "aria-pressed": String(lib.group === key), "data-group": key, onClick: () => pick(key), ...extra },
    lead, h("span", { class: "l" }, label), h("small", {}, String(n)));
  const dot = (colour) => h("i", { class: "d", style: { "--c": colour } });

  function newLabelRow() {
    if (!adding) return h("button", { class: "gi new", type: "button", "data-act": "new-label", onClick: () => { adding = true; force(); const i = lib.groups.querySelector(".gi.new input"); if (i) i.focus(); } }, icon("plus", 15), h("span", { class: "l" }, "New label"));
    const field = h("input", { placeholder: "Name, then Enter", "aria-label": "New label name", maxlength: "40",
      onKeydown: (e) => { if (e.key === "Escape") { adding = false; force(); } else if (e.key === "Enter") { e.preventDefault(); makeLabel(field.value); } },
      onBlur: () => { if (adding && !field.value.trim()) { adding = false; force(); } } });
    return h("div", { class: "gi new" }, icon("plus", 15), field);
  }
  async function makeLabel(raw) {
    const name = cleanLabel(raw);
    adding = false;
    if (!name) { force(); return; }
    if (lib.labels.includes(name)) { toast(`There is already a label called ${name}`); force(); return; }
    const ticked = [...lib.ticked];  // new label while models are ticked: it goes on them straight away
    await mutate((s) => (ticked.length ? added(created(s, name), ticked, name) : created(s, name)),
      (s) => deleted(ticked.length ? removed(s, ticked, name) : s, name),
      () => (ticked.length ? api("POST", `/admin/labels/${enc(name)}/add`, { models: ticked }) : api("POST", "/admin/labels", { name })));
  }
  async function renameLabel(from, raw, field) {
    const to = cleanLabel(raw);
    if (!to || to === from) { field.value = from; return; }
    if (lib.labels.includes(to)) { toast(`There is already a label called ${to}`); field.value = from; return; }
    const selected = lib.group === `label:${from}`;
    if (selected) lib.group = `label:${to}`;
    await mutate((s) => renamed(s, from, to), (s) => renamed(s, to, from), () => api("PATCH", `/admin/labels/${enc(from)}`, { name: to }),
      () => { if (selected) lib.group = `label:${from}`; });
  }
  async function deleteLabel(name) {
    const group = lib.group, index = lib.labels.indexOf(name), had = models().filter((m) => (m.labels || []).includes(name)).map((m) => m.name);
    await mutate((s) => deleted(s, name), (s) => restoredLabel(s, name, index, had), () => api("DELETE", `/admin/labels/${enc(name)}`),
      () => { lib.group = group; });  // the group falls back to All while the delete is pending, and comes back if it is refused
  }
  const saveOrder = (names) => { const previous = [...lib.labels]; return mutate((s) => ordered(s, names), (s) => restoredOrder(s, previous), () => api("PUT", "/admin/labels/order", { names })); };

  function editRow(name) {
    const field = h("input", { value: name, maxlength: "40", "aria-label": `Rename ${name}`, onChange: () => renameLabel(name, field.value, field),
      onKeydown: (e) => { if (e.key === "Enter") field.blur(); else if (e.key === "Escape") { field.value = name; field.blur(); } } });
    const handle = h("button", { class: "hdl", type: "button", "aria-label": `Reorder ${name}: drag, or use the arrow keys`, onPointerdown: (e) => startReorder(e, row, handle),
      onKeydown: (e) => {
        const i = lib.labels.indexOf(name);
        if (e.key === "ArrowUp" && i > 0) { e.preventDefault(); saveOrder(reorder(lib.labels, name, lib.labels[i - 1])).then(() => lib.groups.querySelector(`.gi.edit[data-lab="${name}"] .hdl`)?.focus()); }
        if (e.key === "ArrowDown" && i < lib.labels.length - 1) { e.preventDefault(); saveOrder(reorder(lib.labels, name, lib.labels[i + 2] ?? null)).then(() => lib.groups.querySelector(`.gi.edit[data-lab="${name}"] .hdl`)?.focus()); }
      } }, icon("grip-vertical", 16));
    const row = h("div", { class: "gi edit", "data-lab": name }, handle, field,
      h("button", { class: "x", type: "button", "aria-label": `Delete ${name}`, onClick: () => deleteLabel(name) }, icon("x", 16)));
    return row;
  }

  /** Drag a label's handle (mouse, finger or pen) to a new place; the row follows live and the order is saved on release. */
  function startReorder(e, row, handle) {
    if (e.button > 0) return;
    e.preventDefault();
    const box = row.parentElement;
    handle.setPointerCapture(e.pointerId);
    row.classList.add("dragging");
    const names = () => [...box.querySelectorAll(".gi.edit")].map((r) => r.dataset.lab);
    const move = (ev) => {
      const over = document.elementFromPoint(ev.clientX, ev.clientY)?.closest(".gi.edit");
      if (!over || over === row || over.parentElement !== box) return;
      const r = over.getBoundingClientRect();
      box.insertBefore(row, ev.clientY < r.top + r.height / 2 ? over : over.nextSibling);
    };
    const end = (commit) => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", up);
      handle.removeEventListener("pointercancel", cancel);
      row.classList.remove("dragging");
      const next = names();
      force();
      if (commit && next.join() !== lib.labels.join()) saveOrder(next); else lib.update();
    };
    const up = () => end(true), cancel = () => end(false);
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up);
    handle.addEventListener("pointercancel", cancel);
  }

  function drawGroups() {
    const c = counts(models(), lib.labels);
    const shape = JSON.stringify([lib.group, lib.editing, adding, lib.labels]);
    const recentN = recent ? models().filter((m) => recent.includes(m.name)).length : 0;  // the switcher passes its Recent list; My models has none
    const now = shape + JSON.stringify(c) + recentN;
    if (now === sig) return;
    const a = document.activeElement;
    if (shape === shapeNow && a && lib.groups.contains(a) && a.tagName === "INPUT") return;  // a poll only changing counts never takes a field from under your fingers
    sig = now;
    shapeNow = shape;
    lib.groups.classList.toggle("editing", lib.editing);
    lib.groups.replaceChildren(
      h("div", { class: "gh" }, "Show"),
      item("all", "All models", c.all, icon("box", 16)),
      item("running", "Running", c.running, dot("var(--oq-ok)")),
      ...(recent ? [item("recent", "Recent", recentN, icon("clock", 16))] : []),
      h("div", { class: "gh" }, "Kind ", h("em", {}, "· automatic")),
      ...KINDS.map(([k, name]) => item(`kind:${k}`, name, c.kinds[k], dot(KIND_COLOUR[k]))),
      h("div", { class: "gh mine" }, h("span", { class: "t" }, "Your labels"),
        h("button", { class: "ed", type: "button", "data-act": "edit-labels", onClick: () => { lib.editing = !lib.editing; adding = false; changed(); } }, lib.editing ? "Done" : "Edit labels")),
      ...lib.labels.map((l) => (lib.editing ? editRow(l) : item(`label:${l}`, l, c.labels[l], h("span", { class: "hash" }, "#"), { "data-lab": l }))),
      newLabelRow());
    const on = lib.groups.querySelector(".gi.on");  // the phone's chip row scrolls the chosen chip into view
    if (on && lib.groups.scrollWidth > lib.groups.clientWidth) lib.groups.scrollLeft = Math.max(0, on.offsetLeft - 40);
  }

  // -- drag models onto a label (desktop) -------------------------------------------------------
  /** Make a row or card draggable by model name: a ticked one takes every ticked model along. */
  lib.draggable = (el, name) => {
    el.draggable = true;
    el.addEventListener("dragstart", (e) => {
      e.dataTransfer.effectAllowed = "copy";
      e.dataTransfer.setData("text/plain", JSON.stringify(dragNames(name, [...lib.ticked])));
    });
  };
  const target = (e) => (lib.editing ? null : e.target.closest?.(".gi[data-lab]"));
  const clearDrop = (except) => lib.groups.querySelectorAll(".gi.drop").forEach((x) => x !== except && x.classList.remove("drop"));
  lib.groups.addEventListener("dragover", (e) => { const t = target(e); clearDrop(t); if (t) { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; t.classList.add("drop"); } });
  lib.groups.addEventListener("dragleave", (e) => { if (!lib.groups.contains(e.relatedTarget)) clearDrop(null); });
  lib.groups.addEventListener("drop", async (e) => {
    const t = target(e);
    clearDrop(null);
    if (!t) return;
    e.preventDefault();
    let names = [];
    try { names = JSON.parse(e.dataTransfer.getData("text/plain")); } catch (_) { /* not ours */ }
    names = Array.isArray(names) ? names.filter((n) => typeof n === "string") : [];
    if (!names.length) return;
    const label = t.dataset.lab;
    const lacking = names.filter((n) => !(models().find((m) => m.name === n)?.labels || []).includes(label));
    const ok = await mutate((s) => added(s, names, label), (s) => removed(s, lacking, label), () => api("POST", `/admin/labels/${enc(label)}/add`, { models: names }));
    if (ok) {
      toast(`${names.length === 1 ? names[0] : `${names.length} models`} labelled #${label}`);
      lib.groups.querySelectorAll(`.gi[data-lab="${label}"]`).forEach((x) => { x.classList.add("got"); setTimeout(() => x.classList.remove("got"), 700); });
    }
  });

  // -- ticking, and the bar that labels the ticked ----------------------------------------------
  lib.bar = h("div", { class: "selbar", role: "toolbar", "aria-label": "Label the ticked models" });
  lib.tick = (name) => { if (!lib.ticked.delete(name)) lib.ticked.add(name); changed(); };
  lib.tickAll = (names) => { const all = names.length > 0 && names.every((n) => lib.ticked.has(n)); names.forEach((n) => (all ? lib.ticked.delete(n) : lib.ticked.add(n))); changed(); };
  lib.clearTicks = () => { lib.ticked.clear(); changed(); };
  async function tap(label) {
    const names = [...lib.ticked];
    const { action } = toggleOn(cur(), names, label);
    const lacking = names.filter((n) => !(models().find((m) => m.name === n)?.labels || []).includes(label));
    await mutate((s) => toggleOn(s, names, label).state, (s) => (action === "add" ? removed(s, lacking, label) : added(s, names, label)), () => api("POST", `/admin/labels/${enc(label)}/${action}`, { models: names }));
  }
  function drawBar() {
    const names = [...lib.ticked];
    lib.bar.classList.toggle("on", names.length > 0);
    const now = JSON.stringify([names, lib.labels.map((l) => bulkState(models(), names, l))]);
    if (now === barSig) return;  // a poll that changed nothing keeps the bar's buttons (and the focus on them)
    barSig = now;
    if (!names.length) { lib.bar.replaceChildren(); return; }
    lib.bar.replaceChildren(h("b", {}, `${names.length} selected`), h("span", { class: "lbl" }, "Labels:"),
      ...lib.labels.map((l) => { const st = bulkState(models(), names, l); return h("button", { class: `chip${st === "all" ? " on" : st === "some" ? " part" : ""}`, type: "button", "aria-pressed": st === "all" ? "true" : st === "some" ? "mixed" : "false", "data-lab": l, onClick: () => tap(l) }, h("span", { class: "hash" }, "#"), l); }),
      ...(lib.labels.length ? [] : [h("span", { class: "lbl" }, "none yet: use New label")]),
      h("span", { class: "sp" }), h("button", { class: "fb", type: "button", "data-act": "clear-ticks", onClick: lib.clearTicks }, "Clear"),
      ...(onRemove ? [h("button", { class: "fb danger", type: "button", "data-act": "remove-ticked", onClick: () => onRemove([...lib.ticked]) }, icon("trash-2", 14), `Remove ${names.length}`)] : []));
  }

  // -- the parts the page calls ------------------------------------------------------------------
  lib.visible = () => visibleModels(models(), { group: lib.group, q: lib.q });
  lib.counts = () => counts(models(), lib.labels);
  /** Your labels, in your order (also those no model has yet). */
  lib.loadLabels = async () => {
    if (busy) return;
    try {
      const list = await api("GET", "/admin/labels");
      if (busy) return;
      const names = list.map((l) => l.name);
      lib.loaded = true;
      if (names.join() !== lib.labels.join()) { lib.labels = names; changed(); }
    } catch (_) { /* the page's own "daemon not answering" note covers it */ }
  };
  /** Call after every poll and every change: forget ticks of models that are gone, redraw the column and the bar. */
  lib.update = () => {
    const have = new Set(models().map((m) => m.name));
    for (const n of [...lib.ticked]) if (!have.has(n)) lib.ticked.delete(n);
    if (lib.group.startsWith("label:") && !lib.labels.includes(lib.group.slice(6)) && lib.loaded) lib.group = "all";
    drawGroups();
    drawBar();
  };
  lib.loadLabels();
  return lib;
}

// -- labels on one model (a model's Overview, F-056) -------------------------------------------------
/** Put a label on a model (on = true) or take it off, shown at once and put back, with the daemon's reason, if refused. */
export async function setModelLabel(model, label, on) {
  const have = ((store.models || []).find((m) => m.name === model)?.labels || []).includes(label);
  if (have === on) return true;
  const ok = await optimistic({
    get: () => ({ labels: [], models: store.models || [] }), set: (s) => { store.models = s.models; emitData(); },
    next: (s) => (on ? added : removed)(s, [model], label), undo: (s) => (on ? removed : added)(s, [model], label),
    request: () => api("POST", `/admin/labels/${enc(label)}/${on ? "add" : "remove"}`, { models: [model] }), onError: toast,
  });
  if (ok) await refresh();
  return ok;
}

/** The model's #labels as removable tags and a "+ label" that opens a field (suggesting your existing labels). A new name creates the label.
 *  getName: the model now shown (the page swaps models in place). Call update() after every poll. */
export function labelsEditor(getName) {
  const el = h("span", { class: "lbls" });
  let adding = false, known = [];
  const redraw = () => {
    const m = (store.models || []).find((x) => x.name === getName());
    if (!m) { el.replaceChildren(); return; }
    const mine = m.labels || [];
    const field = adding ? h("input", { class: "lbl-in", list: "lbl-known", placeholder: "label, then Enter", "aria-label": "Add a label", maxlength: "40",
      onKeydown: (e) => { if (e.key === "Enter") { e.preventDefault(); commit(field.value); } else if (e.key === "Escape") { e.stopPropagation(); adding = false; redraw(); } },
      onBlur: () => { if (adding && !field.value.trim()) { adding = false; redraw(); } } }) : null;
    el.replaceChildren(
      ...mine.map((l) => h("span", { class: "tag lab", "data-lab": l }, `#${l}`,
        h("button", { type: "button", class: "x", "aria-label": `Remove label ${l}`, onClick: () => setModelLabel(m.name, l, false) }, icon("x", 12)))),
      ...(field ? [h("datalist", { id: "lbl-known" }, known.filter((l) => !mine.includes(l)).map((l) => h("option", { value: l})))] : []),
      field || h("button", { type: "button", class: "tag add", "data-act": "add-label", onClick: async () => {
        adding = true; redraw(); el.querySelector("input")?.focus();
        try {
          known = (await api("GET", "/admin/labels")).map((x) => x.name);
          const mine = ((store.models || []).find((x) => x.name === getName())?.labels) || [];
          // fill the suggestions in place: replacing the field would blur it, and a blurred empty field closes itself
          el.querySelector("datalist")?.replaceChildren(...known.filter((l) => !mine.includes(l)).map((l) => h("option", { value: l })));
        } catch (_) { /* no suggestions: typing a name still works */ }
      } }, icon("plus", 12), "label"));
  };
  function commit(raw) {
    const name = cleanLabel(raw), model = getName();
    adding = false;
    redraw();
    if (name) setModelLabel(model, name, true);
  }
  el.update = () => { if (!adding) redraw(); };
  el.update();
  return el;
}
