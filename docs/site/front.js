/* The front page's behaviour (F-060, built from docs/design/front-page/claude-a.html).
   Motion happens on scroll, never on idle; prefers-reduced-motion leaves everything static and visible. */
(() => {
  const html = document.documentElement;
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (!reduce) html.classList.add("mo-on");

  addEventListener("DOMContentLoaded", page);

  /* ---------- the page ---------- */
  // Lucide icons (ISC), drawn as symbols once; pages use <svg class="ico"><use href="#i-name"/></svg>
  const ICONS = {
    arrow: '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
    copy: '<rect width="14" height="14" x="8" y="8" rx="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
    github: '<path d="M15 22v-4a4.8 4.8 0 0 0-1-3.5c3 0 6-2 6-5.5.08-1.25-.27-2.48-1-3.5.28-1.15.28-2.35 0-3.5 0 0-1 0-3 1.5-2.64-.5-5.36-.5-8 0C6 2 5 2 5 2c-.3 1.15-.3 2.35 0 3.5A5.4 5.4 0 0 0 4 9c0 3.5 3 5.5 6 5.5-.39.49-.68 1.05-.85 1.65-.17.6-.22 1.23-.15 1.85v4"/><path d="M9 18c-4.51 2-5-2-7-2"/>',
    lock: '<rect width="18" height="11" x="3" y="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    key: '<path d="M2.586 17.414A2 2 0 0 0 2 18.828V21a1 1 0 0 0 1 1h3a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1h1a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1h.172a2 2 0 0 0 1.414-.586l.814-.814a6.5 6.5 0 1 0-4-4z"/><circle cx="16.5" cy="7.5" r=".5"/>',
    laptop: '<path d="M18 5a2 2 0 0 1 2 2v8.526a2 2 0 0 0 .212.897l1.068 2.127a1 1 0 0 1-.9 1.45H3.62a1 1 0 0 1-.9-1.45l1.068-2.127A2 2 0 0 0 4 15.526V7a2 2 0 0 1 2-2z"/><path d="M20.054 15.987H3.946"/>',
    phone: '<rect width="14" height="20" x="5" y="2" rx="2"/><path d="M12 18h.01"/>',
    pin: '<path d="M12 17v5"/><path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z"/>',
    server: '<rect width="20" height="8" x="2" y="2" rx="2"/><rect width="20" height="8" x="2" y="14" rx="2"/><path d="M6 6h.01M6 18h.01"/>',
    branch: '<path d="M6 3v12"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/>',
    cloudoff: '<path d="m2 2 20 20"/><path d="M5.782 5.782A7 7 0 0 0 9 19h8.5a4.5 4.5 0 0 0 1.307-.193"/><path d="M21.532 16.5A4.5 4.5 0 0 0 17.5 10h-1.79A7.008 7.008 0 0 0 10 5.07"/>',
    term: '<path d="m4 17 6-6-6-6"/><path d="M12 19h8"/>',
    gauge: '<path d="m12 14 4-4"/><path d="M3.34 19a10 10 0 1 1 17.32 0"/>',
    book: '<path d="M12 7v14"/><path d="M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z"/>',
    layers: '<path d="M12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z"/><path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65"/><path d="m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    shield: '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/>',
  };
  function sprite() {
    const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("aria-hidden", "true"); s.style.cssText = "position:absolute;width:0;height:0";
    s.innerHTML = Object.entries(ICONS).map(([k, v]) => `<symbol id="i-${k}" viewBox="0 0 24 24">${v}</symbol>`).join("");
    document.body.prepend(s);
  }

  function page() {
    sprite();
    splitWords();
    reveal();
    navState();
    unfold();
    readAlong();
    document.querySelectorAll("[data-rt]").forEach(buildTravel);
    document.querySelectorAll("[data-copy]").forEach(copyBtn);
    const place = () => document.querySelectorAll(".seg2").forEach(s => window.Omxqua && Omxqua.placeThumb(s));
    place(); document.fonts && document.fonts.ready.then(place); addEventListener("resize", place);
  }

  function splitWords() {
    document.querySelectorAll(".w-rise, .L-read").forEach(el => {
      let i = 0;
      const walk = node => [...node.childNodes].forEach(n => {
        if (n.nodeType === 3) {
          const frag = document.createDocumentFragment();
          n.textContent.split(/(\s+)/).forEach(part => {
            if (!part) return;
            if (/^\s+$/.test(part)) { frag.append(part); return; }
            const s = document.createElement("span"); s.className = "w"; s.style.setProperty("--i", i++); s.textContent = part; frag.append(s);
          });
          n.replaceWith(frag);
        } else if (n.nodeType === 1 && !n.matches("br")) walk(n);
      });
      walk(el);
    });
  }

  function reveal() {
    const els = document.querySelectorAll(".rv, .w-rise, [data-count]");
    if (reduce || !("IntersectionObserver" in window)) { els.forEach(e => { e.classList.add("in"); if (e.dataset.count) e.textContent = e.dataset.count; }); return; }
    const io = new IntersectionObserver(es => es.forEach(e => {
      if (!e.isIntersecting) return;
      e.target.classList.add("in"); io.unobserve(e.target);
      if (e.target.dataset.count) countUp(e.target);
    }), { rootMargin: "0px 0px -10% 0px" });
    els.forEach(e => io.observe(e));
  }

  // numbers count up once, keeping their text shape ("7–40", "$2–5" count each number)
  function countUp(el) {
    const final = el.dataset.count, nums = final.match(/\d+/g) || [];
    const t0 = performance.now(), dur = 900;
    const step = now => {
      const k = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - k, 3);
      let j = 0; el.textContent = final.replace(/\d+/g, () => String(Math.round(+nums[j++] * e)));
      if (k < 1) requestAnimationFrame(step); else el.textContent = final;
    };
    requestAnimationFrame(step);
  }

  function navState() {
    const nav = document.querySelector(".L-nav"); if (!nav) return;
    const onScroll = () => nav.classList.toggle("scrolled", scrollY > 4);
    onScroll(); addEventListener("scroll", onScroll, { passive: true });
    const links = [...nav.querySelectorAll(".L-links a")];
    const io = new IntersectionObserver(es => es.forEach(e => {
      if (!e.isIntersecting) return;
      links.forEach(a => a.classList.toggle("on", a.getAttribute("href") === "#" + e.target.id));
    }), { rootMargin: "-40% 0px -55% 0px" });
    links.forEach(a => { const t = document.querySelector(a.getAttribute("href")); if (t) io.observe(t); });
  }

  // the product frame unfolds as it scrolls in: scale .82 → 1, corners 40 → 14
  function unfold() {
    const els = [...document.querySelectorAll(".L-unfold")]; if (!els.length || reduce) return;
    const tick = () => els.forEach(el => {
      const r = el.getBoundingClientRect(), vh = innerHeight;
      const p = Math.max(0, Math.min(1, (vh - r.top) / (vh * .75)));
      el.style.setProperty("--p", p.toFixed(3));
    });
    tick(); addEventListener("scroll", tick, { passive: true }); addEventListener("resize", tick);
  }

  // one read-along paragraph lights up word by word with the scroll
  function readAlong() {
    const el = document.querySelector(".L-read"); if (!el || reduce) return;
    const ws = [...el.querySelectorAll(".w")];
    const tick = () => {
      const r = el.getBoundingClientRect(), vh = innerHeight;
      const p = Math.max(0, Math.min(1, (vh * .85 - r.top) / (r.height + vh * .35)));
      const n = Math.round(p * ws.length);
      ws.forEach((w, i) => w.classList.toggle("lit", i < n));
    };
    tick(); addEventListener("scroll", tick, { passive: true });
  }

  function copyBtn(b) {
    b.addEventListener("click", async () => {
      const src = document.getElementById(b.dataset.copy);
      const text = [...src.querySelectorAll("[data-line]")].map(l => l.textContent.trim()).join("\n");
      const lab = b.querySelector("span") || b;
      try { await navigator.clipboard.writeText(text); lab.textContent = "Copied"; } catch (_) { lab.textContent = "Select and copy"; }
      setTimeout(() => (lab.textContent = "Copy"), 1600);
    });
  }

  /* ---------- How a request travels: pick where the call comes from and cold / loaded, press Play ---------- */
  const HOPS = [
    ["app", "your app", "OpenAI client", "Any SDK with base_url and a key from mxc keys new.", "var(--oq-data-2)"],
    ["caddy", ":8443 · LAN only", "Caddy", "HTTPS with its own certificate authority. Asks the daemon if the key is valid. Forwards only /v1.", "var(--oq-ok)"],
    ["litellm", ":4000 · LAN only", "LiteLLM", "Routes by model name to the daemon with its own internal key. Its admin API never reaches the LAN.", "var(--oq-data-4)"],
    ["daemon", ":11435", "mxc daemon", "Checks the key, starts the model if it's cold, counts tokens and latency for the console.", "var(--oq-data-1)"],
    ["max", "loopback port", "max serve", "One MAX server per model. Stopped after keep_alive idle (default 5 min) unless pinned.", "var(--oq-data-3)"],
  ];
  function buildTravel(root) {
    root.innerHTML = `
      <div class="rt-ctl">
        <div class="seg2" role="group" aria-label="Where the call comes from"><i></i><button type="button" class="on" aria-pressed="true" data-k="from" data-v="local">This machine</button><button type="button" aria-pressed="false" data-k="from" data-v="lan">Another device on the LAN</button></div>
        <div class="seg2" role="group" aria-label="Model state"><i></i><button type="button" class="on" aria-pressed="true" data-k="state" data-v="cold">Model idle (cold)</button><button type="button" aria-pressed="false" data-k="state" data-v="warm">Model loaded</button></div>
        <button type="button" class="btn fill play"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><polygon points="6 3 20 12 6 21 6 3"/></svg>Play</button>
      </div>
      <div class="rt-flow">${HOPS.map(h => `<div class="rt-hop" data-hop="${h[0]}" style="--c:${h[4]}"><small>${h[1]}</small><b>${h[2]}</b><span>${h[3]}</span></div>`).join("")}</div>
      <div class="rt-log" aria-live="polite"><div><span class="t">—</span>press Play</div></div>
      <div class="rt-foot"><span>hops <b class="rt-n">3</b></span><span>path <b class="rt-p">http://127.0.0.1:11435</b></span></div>`;
    const opts = { from: "local", state: "cold" };
    const hops = [...root.querySelectorAll(".rt-hop")], log = root.querySelector(".rt-log");
    let timer = null;
    const paint = () => {
      const lan = opts.from === "lan";
      hops.forEach(h => { h.classList.remove("on"); h.classList.toggle("off", !lan && (h.dataset.hop === "caddy" || h.dataset.hop === "litellm")); });
      root.querySelector(".rt-n").textContent = lan ? 5 : 3;
      root.querySelector(".rt-p").textContent = lan ? "https://box.local:8443" : "http://127.0.0.1:11435";
    };
    const script = () => {
      const lan = opts.from === "lan", cold = opts.state === "cold";
      const s = [["app", lan ? "POST https://box.local:8443/v1/chat/completions (Bearer mst-…)" : "POST http://127.0.0.1:11435/v1/chat/completions (Bearer mst-…)"]];
      if (lan) s.push(["caddy", "TLS ok · forward_auth → /auth/check → <span class=ok>200, X-Mst-App: my-app</span>"],
                      ["litellm", "qwen2.5-0.5b → http://127.0.0.1:11435/v1 with its internal key"]);
      s.push(["daemon", "key ok · qwen2.5-0.5b is " + (cold ? "<span class=warn>cold</span>" : "<span class=ok>ready</span>")]);
      if (cold) s.push(["max", "<span class=warn>starting max serve on a free loopback port …</span>"]);
      s.push(["max", "streaming tokens back"], ["daemon", "<span class=ok>200</span> · first token and tok/s recorded · idle timer restarts"]);
      return s;
    };
    const play = () => {
      clearTimeout(timer); paint(); log.innerHTML = "";
      const steps = script(); let i = 0;
      const next = () => {
        if (i >= steps.length) { hops.forEach(h => h.classList.remove("on")); return; }
        const [hop, text] = steps[i++];
        hops.forEach(h => h.classList.toggle("on", h.dataset.hop === hop));
        log.insertAdjacentHTML("beforeend", `<div class="new"><span class="t">${hop}</span>${text}</div>`);
        timer = setTimeout(next, reduce ? 0 : 850);
      };
      next();
    };
    root.querySelectorAll(".seg2 button").forEach(b => b.addEventListener("click", () => {
      const g = b.parentElement;
      g.querySelectorAll("button").forEach(x => { x.classList.toggle("on", x === b); x.setAttribute("aria-pressed", String(x === b)); });
      opts[b.dataset.k] = b.dataset.v; window.Omxqua && Omxqua.placeThumb(g);
      clearTimeout(timer); paint(); log.innerHTML = '<div><span class="t">—</span>press Play</div>';
    }));
    root.querySelector(".play").addEventListener("click", play);
    paint();
  }
})();
