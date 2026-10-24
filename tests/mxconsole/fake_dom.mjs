// A tiny DOM for running the console's page code under node (there is no jsdom here): elements, attributes, classes,
// events, focus, and the few selectors the pages use (.class chains, [attr="v"], tag). Import it before the module under test.
export class FakeNode {
  constructor() { this.parentNode = null; this.children = []; this.listeners = {}; }
  get isConnected() { let n = this; while (n.parentNode) n = n.parentNode; return n === document.body; }
}
export class FakeText extends FakeNode {
  constructor(text) { super(); this.data = String(text); }
  get textContent() { return this.data; }
}
class ClassList {
  constructor(el) { this.el = el; }
  list() { return (this.el.attributes.class || "").split(/\s+/).filter(Boolean); }
  contains(c) { return this.list().includes(c); }
  add(...cs) { this.el.attributes.class = [...new Set([...this.list(), ...cs])].join(" "); }
  remove(...cs) { this.el.attributes.class = this.list().filter((c) => !cs.includes(c)).join(" "); }
  toggle(c, force) { const on = force === undefined ? !this.contains(c) : force; on ? this.add(c) : this.remove(c); return on; }
}
export class FakeEl extends FakeNode {
  constructor(tag) {
    super();
    this.tagName = tag.toUpperCase(); this.attributes = {}; this.dataset = {}; this.classList = new ClassList(this);
    this.props = {};
    this.style = { setProperty: (k, v) => { this.props[k] = v; }, set cssText(v) { this.text = v; }, getPropertyValue: (k) => this.props[k] };
    this.hidden = false; this.disabled = false;
  }
  setAttribute(k, v) { this.attributes[k] = String(v); }
  getAttribute(k) { return k in this.attributes ? this.attributes[k] : null; }
  removeAttribute(k) { delete this.attributes[k]; }
  get className() { return this.attributes.class || ""; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] || []).filter((f) => f !== fn); }
  dispatch(type, init = {}) {
    const e = { type, target: this, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, stopPropagation() {}, ...init };
    for (let n = this; n; n = n.parentNode) for (const fn of (n.listeners[type] || []).slice()) { e.currentTarget = n; fn(e); }
    return e;
  }
  click() { return this.dispatch("click"); }
  key(key) { const e = { key, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } }; (this.listeners.keydown || []).slice().forEach((fn) => fn(e)); return e; }
  append(...kids) { kids.forEach((k) => { if (k.parentNode) k.remove(); k.parentNode = this; this.children.push(k); }); }
  replaceChildren(...kids) { this.children.forEach((k) => { k.parentNode = null; }); this.children = []; this.append(...kids); }
  remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter((c) => c !== this); this.parentNode = null; }
  get lastChild() { return this.children[this.children.length - 1]; }
  get textContent() { return this.children.map((c) => c.textContent).join(""); }
  set textContent(v) { this.replaceChildren(new FakeText(v)); }
  focus() { document.activeElement = this; }
  getBoundingClientRect() { return { left: 0, right: 100, top: 0, bottom: 30, width: 100, height: 30 }; }
  get offsetWidth() { return 80; }
  get offsetLeft() { return 0; }
  matches(sel) {
    return sel.split(",").some((one) => {
      const re = /^([a-z0-9]+)?((?:\.[\w-]+|\[[\w-]+(?:="[^"]*")?\])*)$/i.exec(one.trim());
      if (!re) return false;
      if (re[1] && re[1].toUpperCase() !== this.tagName) return false;
      return [...re[2].matchAll(/\.([\w-]+)|\[([\w-]+)(?:="([^"]*)")?\]/g)].every((m) => (m[1] ? this.classList.contains(m[1]) : m[3] === undefined ? m[2] in this.attributes : this.attributes[m[2]] === m[3]));
    });
  }
  querySelectorAll(sel) { const out = []; const walk = (n) => n.children.forEach((c) => { if (c instanceof FakeEl) { if (c.matches(sel)) out.push(c); walk(c); } }); walk(this); return out; }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  closest(sel) { for (let n = this; n instanceof FakeEl; n = n.parentNode) if (n.matches(sel)) return n; return null; }
}
const listeners = {};
globalThis.Node = FakeNode;
globalThis.document = {
  body: new FakeEl("body"), activeElement: null,
  createElement: (t) => new FakeEl(t), createElementNS: (_ns, t) => new FakeEl(t), createTextNode: (t) => new FakeText(t),
  getElementById: () => null, querySelector: () => null,
  addEventListener: (t, fn) => { (listeners[t] ||= []).push(fn); },
  removeEventListener: (t, fn) => { listeners[t] = (listeners[t] || []).filter((f) => f !== fn); },
  /** Press a key at document level (like the browser does for a keydown that nothing stopped). */
  key(key) { const e = { key, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } }; (listeners.keydown || []).slice().forEach((fn) => fn(e)); return e; },
};
const winListeners = {};
globalThis.window = {
  matchMedia: () => ({ matches: false }),
  addEventListener: (t, fn) => { (winListeners[t] ||= []).push(fn); },
  removeEventListener: (t, fn) => { winListeners[t] = (winListeners[t] || []).filter((f) => f !== fn); },
  /** Fire a window resize (the test changes what getBoundingClientRect returns first). */
  resize() { (winListeners.resize || []).slice().forEach((fn) => fn({ type: "resize" })); },
  listening: (t) => (winListeners[t] || []).length,
};
globalThis.requestAnimationFrame = (fn) => { fn(0); return 1; };
globalThis.cancelAnimationFrame = () => {};
globalThis.location = { origin: "http://x", hash: "" };
globalThis.history = { state: null };
