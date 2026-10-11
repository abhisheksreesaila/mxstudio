// mxconsole, the Security blade (F-025): put the gateway on your LAN over HTTPS (/admin/expose), see its steps,
// the HTTPS URL, the CA to trust and how, and turn it off again. Machine-wide: it applies to every model.
// Only this file and blade-security.css change for it.
import { h, icon, store, onData, api, refresh, toast, copyButton } from "./core.js";
import { register, push, stack } from "./blades.js";

export const EXPOSE_STEPS = [
  { key: "cert", icon: "shield-check", label: "Issue a local certificate" },
  { key: "key", icon: "key-round", label: "Create the gateway key" },
  { key: "https", icon: "globe", label: "Open HTTPS on your LAN" },
];

/** What the blade shows. info: /admin/info. network: /admin/network (null until it answers). Pure. */
export function securityView(info, network) {
  const url = (info && info.lan_endpoint) || null;
  const caPath = (network && network.ca_path) || "";
  const host = url ? url.replace(/^https?:\/\//, "").replace(/[:/].*$/, "") : (network && network.default_host) || "";
  const cacert = caPath ? `--cacert ${caPath} ` : "";
  return {
    secure: !!url, url, host, caPath, caExists: !!(network && network.ca_exists),
    port: network && network.ports ? network.ports.caddy : null,
    curl: url ? `curl ${cacert}${url}/chat/completions \\\n  -H "Authorization: Bearer $MXC_API_KEY" \\\n  -H "Content-Type: application/json" \\\n  -d '{"model":"<model>","messages":[{"role":"user","content":"hi"}]}'` : "",
  };
}

/** The state of each step while the request runs: `at` is the step in progress; mode "run" | "done" | "fail". */
export function stepStates(at, mode) {
  return EXPOSE_STEPS.map((_, i) => (mode === "done" ? "done" : i < at ? "done" : i === at ? (mode === "fail" ? "fail" : "active") : "todo"));
}

const TRUST = [
  ["Linux", "sudo cp ca.crt /usr/local/share/ca-certificates/mxconsole.crt && sudo update-ca-certificates"],
  ["macOS", "Open ca.crt in Keychain Access, then set it to Always Trust"],
  ["Windows", "Double-click ca.crt, install it into Trusted Root Certification Authorities"],
  ["iPhone, Android", "Send ca.crt to the phone, install it as a profile or CA certificate, then trust it in settings"],
];

register("security", (blade) => ({
  title: "Security", crumb: "Security", sub: "certificate, key and HTTPS on your LAN",
  icon: "lock", hue: "var(--hue-keys)", side: 520, min: 520,
  mount(body) {
    let network = null, phase = "idle", at = 0, error = "", timer = 0, token = 0, shape = "";
    const idx = () => stack.indexOf(blade);

    const statusIcon = h("span", { class: "sq", style: { width: "28px", height: "28px" } });
    const statusTitle = h("b", {}), statusText = h("span", {});
    const actions = h("div", { class: "secact" });
    const steps = h("ul", { class: "secsteps" });
    const errBox = h("div", { class: "attn", role: "alert", hidden: true });
    const box = h("div", { class: "secbox" }, h("div", { class: "hd" }, statusIcon, h("div", { class: "tx" }, statusTitle, statusText)), actions, steps, errBox);
    const after = h("div", { class: "after" });
    const how = h("div", {}, h("div", { class: "h3" }, "What “Make it secure” does"),
      h("dl", { class: "ess" },
        h("dt", {}, "Certificate"), h("dd", {}, "a local CA, “mxconsole local”, signs a certificate for this machine's name"),
        h("dt", {}, "API key"), h("dd", {}, "the gateway has its own key; requests without a valid app key get 401"),
        h("dt", {}, "HTTPS"), h("dd", {}, "Caddy and LiteLLM listen on your LAN; localhost keeps working over HTTP")));
    body.replaceChildren(h("div", { class: "pad" }, box, how, after));

    async function loadNetwork() {
      try { network = await api("GET", "/admin/network"); } catch (_) { /* the view works without it */ }
      draw();
    }
    async function secure() {
      if (phase === "run") return;
      phase = "run"; at = 0; error = ""; const mine = ++token;
      draw();
      timer = setInterval(() => { if (at < EXPOSE_STEPS.length - 1) { at += 1; draw(); } }, 1100);
      try {
        await api("POST", "/admin/expose", {});
        if (mine !== token || !blade.alive) return;
        phase = "done";
      } catch (e) {
        if (mine !== token || !blade.alive) return;
        phase = "fail"; error = e.message;
      }
      clearInterval(timer);
      await refresh();
      await loadNetwork();
      if (phase === "done") toast(`Secure: ${securityView(store.info, network).url || "HTTPS on your LAN"} · $ mxc expose`);
      draw();
    }
    async function stopExposing() {
      try { await api("POST", "/admin/expose/stop"); toast("LAN access is off · $ mxc expose --stop"); } catch (e) { toast(`Could not stop: ${e.message}`); }
      phase = "idle"; await refresh(); await loadNetwork();
    }

    function stepRows(states, v) {
      const meta = { cert: v.host ? `for ${v.host}` : "", key: "mxc gateway key", https: v.port ? `port ${v.port}` : "" };
      return EXPOSE_STEPS.map((st, i) => h("li", { class: states[i] }, h("span", { class: "ring" }, icon(states[i] === "done" ? "check" : states[i] === "fail" ? "x" : st.icon, 11, "currentColor", 2.2)),
        st.label, h("span", { class: "m" }, meta[st.key])));
    }
    function draw() {
      if (!blade.alive) return;
      const v = securityView(store.info, network);
      const running = phase !== "idle";
      const sig = `${v.secure}|${phase}|${v.url}|${v.caPath}|${v.caExists}`;
      statusIcon.style.setProperty("--h", v.secure ? "var(--ok)" : "var(--hue-keys)");
      statusIcon.replaceChildren(icon(v.secure ? "lock" : "unlock", 16, "currentColor", 1.75));
      box.classList.toggle("done", v.secure && phase !== "run");
      statusTitle.textContent = phase === "run" ? "Making it secure…" : v.secure ? "Secure on your network" : "Localhost only";
      statusText.textContent = phase === "run" ? "$ mxc expose" : v.secure ? "HTTPS with a key. Other devices on your Wi-Fi can call it." : "Only this machine can call it. One click adds a certificate, a key and HTTPS.";
      // the steps are not focusable: rebuilt every draw while they run
      steps.hidden = !running;
      if (running) steps.replaceChildren(...stepRows(stepStates(at, phase === "fail" ? "fail" : phase === "done" ? "done" : "run"), v));
      errBox.hidden = phase !== "fail";
      errBox.replaceChildren(...(phase === "fail" ? [icon("triangle-alert", 15), h("span", {}, `Could not make it secure: ${error}`)] : []));
      if (sig === shape) return;  // buttons and the after-panel are focusable: rebuild only when they change
      shape = sig;
      actions.replaceChildren(...(v.secure
        ? [h("button", { class: "btn ghost", type: "button", onClick: () => push("keys", {}, idx()) }, icon("key-round", 14, "currentColor", 1.75), "Manage API keys"),
          h("button", { class: "btn ghost danger", type: "button", onClick: stopExposing }, icon("unlock", 14, "currentColor", 1.75), "Stop exposing")]
        : [h("button", { class: "btn dark", type: "button", disabled: phase === "run", onClick: secure }, icon("lock", 14, "currentColor", 1.75), phase === "fail" ? "Try again" : "Make it secure")]),
      h("div", { class: "cli mono" }, v.secure ? "mxc expose --stop" : "mxc expose"));
      after.replaceChildren(...(v.secure ? [
        h("div", { class: "urlrow secure" }, h("span", { class: "tag" }, "https · lan"), h("span", { class: "u" }, v.url), copyButton(() => v.url, "", "ib")),
        h("div", { class: "h3 gap" }, "Trust the certificate"),
        h("p", { class: "note" }, v.caExists ? "Other devices need to trust the mxconsole CA once, or HTTPS calls fail with a certificate error. This machine already knows it." : "The CA file is created the first time Caddy starts; it is not there yet."),
        h("div", { class: "urlrow" }, h("span", { class: "tag" }, "ca"), h("span", { class: "u" }, v.caPath || "—"), v.caPath ? copyButton(() => v.caPath, "", "ib") : null),
        h("ul", { class: "trust" }, TRUST.map(([os, how]) => h("li", {}, h("b", {}, os), h("span", { class: "mono" }, how)))),
        h("div", { class: "h3 gap" }, "From another device on your Wi-Fi"),
        h("div", { class: "code" }, copyButton(() => v.curl, "copy", "cp"), h("pre", { class: "curl mono" }, v.curl)),
      ] : []));
    }
    blade.redraw = draw;
    blade.onClose = (() => { const off = onData(draw); return () => { off(); clearInterval(timer); token += 1; }; })();
    draw();
    loadNetwork();
    if (blade.opts.run && !(store.info && store.info.lan_endpoint)) setTimeout(() => { if (blade.alive && phase === "idle") secure(); }, 300);
  },
}));
