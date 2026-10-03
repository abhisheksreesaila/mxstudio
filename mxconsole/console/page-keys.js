// API keys: the table, New key (shown once), Revoke with confirmation. Internal keys (`_…`) never reach this page.
import { h, icon, api, toast, copyButton, openDialog, terminalBox, ago, dateTime } from "./core.js";

const CHANGED = "mxc:keys-changed";

export function newKeyDialog() {
  const err = h("div", { class: "bad-text", style: "font-size:13px", role: "alert" });
  const input = h("input", { class: "input", placeholder: "my-app", "aria-label": "App name", autocomplete: "off", spellcheck: "false" });
  const body = h("div", { class: "dlg-in" });
  const dlg = h("dialog", { class: "dlg" }, body);
  document.body.append(dlg);
  dlg.addEventListener("close", () => dlg.remove());
  const create = h("button", { class: "btn primary", type: "submit" }, "Create key");
  const form = h("form", { class: "dlg-in", style: "padding:0", onSubmit: async (e) => {
    e.preventDefault();
    err.textContent = "";
    create.disabled = true;
    try {
      const res = await api("POST", "/admin/keys", { app: input.value.trim() });
      showOnce(res);
      window.dispatchEvent(new Event(CHANGED));
    } catch (ex) {
      err.textContent = ex.message;
      create.disabled = false;
    }
  } },
  h("h2", {}, "New API key"),
  h("p", {}, "One key per app. MX Console shows it once and stores only a hash, so a lost key means a new one."),
  h("label", { class: "field" }, "App name", input), err,
  terminalBox(["mxc keys new my-app"], "same from the terminal"),
  h("div", { class: "row" }, h("button", { class: "btn", type: "button", onClick: () => dlg.close() }, "Cancel"), create));
  body.replaceChildren(form);

  function showOnce({ app, key }) {
    body.replaceChildren(
      h("h2", {}, `Key for ${app}`),
      h("div", { class: "warnbox" }, icon("triangle-alert", 16), h("div", {}, h("strong", {}, "This is the only time you will see it. "), "Copy it now and put it in the app's environment, for example ", h("span", { class: "mono" }, "MXC_API_KEY"), ".")),
      h("div", { class: "keybox" }, h("span", { class: "v mono" }, key), copyButton(() => key, "Copy")),
      h("div", { class: "row" }, h("button", { class: "btn primary", type: "button", onClick: () => dlg.close() }, "Done")));
  }
  dlg.showModal();
  input.focus();
  return dlg;
}

function revokeDialog(app) {
  const err = h("div", { class: "bad-text", style: "font-size:13px", role: "alert" });
  const go = h("button", { class: "btn danger", type: "button" }, "Revoke key");
  const { dlg, close } = openDialog(
    h("h2", {}, `Revoke the key for ${app}?`),
    h("p", {}, "Requests from that app start failing with 401 straight away. You can create a new key for it afterwards."),
    terminalBox([`mxc keys revoke ${app}`]), err,
    h("div", { class: "row" }, h("button", { class: "btn", type: "button", onClick: () => dlg.close() }, "Cancel"), go));
  go.addEventListener("click", async () => {
    go.disabled = true;
    try {
      await api("DELETE", `/admin/keys/${encodeURIComponent(app)}`);
      toast(`The key for ${app} is revoked.`);
      window.dispatchEvent(new Event(CHANGED));
      close();
    } catch (e) { err.textContent = e.message; go.disabled = false; }
  });
}

export function keysPage(ctx) {
  ctx.actions.append(h("button", { class: "btn primary", type: "button", onClick: () => newKeyDialog() }, icon("plus", 15), "New key"));
  const tbody = h("tbody", {});
  const host = h("div", {});
  ctx.body.replaceChildren(host);
  let rows = null, failed = null;

  const draw = () => {
    ctx.setSub(rows ? `${rows.filter((r) => r.status === "active").length} active · one key per app, shown once` : "one key per app, shown once");
    if (failed) { host.replaceChildren(h("div", { class: "panel empty" }, h("h2", {}, "Could not load keys"), h("p", {}, failed))); return; }
    if (rows === null) { host.replaceChildren(h("div", { class: "panel empty" }, h("p", {}, "Loading…"))); return; }
    if (!rows.length) {
      host.replaceChildren(h("div", { class: "panel empty" }, h("h2", {}, "No API keys yet"),
        h("p", {}, "Apps need a key to call the endpoint. Create one for each app, so you can revoke it on its own."),
        h("button", { class: "btn primary", type: "button", onClick: () => newKeyDialog() }, icon("plus", 15), "New key")));
      return;
    }
    tbody.replaceChildren(...rows.map((r) => h("tr", {},
      h("td", {}, h("span", { class: "mono", style: "font-weight:500" }, r.app)),
      h("td", {}, h("span", { class: "mono muted" }, `${r.prefix}…`)),
      h("td", { class: "muted" }, dateTime(r.created_at)),
      h("td", { class: "muted" }, r.last_used_at ? ago(r.last_used_at) : "never"),
      h("td", {}, h("span", { class: r.status === "active" ? "pill ok" : "pill" }, r.status === "active" ? "Active" : "Revoked")),
      h("td", { class: "r" }, r.status === "active" ? h("button", { class: "btn danger", type: "button", style: "height:28px", onClick: () => revokeDialog(r.app) }, "Revoke") : null))));
    host.replaceChildren(h("section", { class: "panel keys-panel scroll-x" }, h("table", { class: "grid" },
      h("thead", {}, h("tr", {}, ["App", "Prefix", "Created", "Last used", "Status", ""].map((c) => h("th", {}, c)))), tbody)),
    h("div", { class: "cli-note mono" }, h("span", {}, "$ mxc keys ls"), h("span", {}, "$ mxc keys new <app>"), h("span", {}, "$ mxc keys revoke <app>")));
  };
  const load = async () => {
    try { rows = await api("GET", "/admin/keys"); failed = null; } catch (e) { failed = e.message; }
    draw();
  };
  window.addEventListener(CHANGED, load);
  const timer = setInterval(load, 5000);
  load();
  return { update() {}, destroy() { clearInterval(timer); window.removeEventListener(CHANGED, load); } };
}
