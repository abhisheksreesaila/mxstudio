// omxconsole, Remove (F-062): the confirm and the call, shared by the tick bar on My models and a model's own page.
// Remove is `mxc rm`: the model stops, leaves My models and its compiled graphs are deleted; the downloaded weights stay.
import { h, api, refresh, toast } from "./core.js";

/** The confirm's words for one model or several. Pure. */
export function removeCopy(names) {
  const one = names.length === 1;
  return {
    title: one ? `Remove ${names[0]}?` : `Remove ${names.length} models?`,
    body: `${one ? "It stops, leaves" : "They stop, leave"} My models and ${one ? "its" : "their"} compiled graphs are deleted. The downloaded weights stay, so adding ${one ? "it" : "them"} back is quick. Same as mxc rm.`,
    confirm: one ? "Remove" : `Remove ${names.length}`,
  };
}

let seq = 0;
/** A modal confirm: resolves true (the confirm button) or false (Cancel, Esc, a click outside). Focus moves to Cancel, Tab stays
 *  inside, and focus goes back to what had it. Built from text nodes; styled by library.css (.confirm). */
export function confirmDialog({ title, body, confirm }) {
  return new Promise((resolve) => {
    const before = document.activeElement;
    const id = `cf${++seq}`;
    const finish = (answer) => {
      overlay.remove();
      if (before && before.focus) before.focus();
      resolve(answer);
    };
    const cancel = h("button", { class: "fb", type: "button", "data-act": "cancel", onClick: () => finish(false) }, "Cancel");
    const ok = h("button", { class: "fb fill danger2", type: "button", "data-act": "ok", onClick: () => finish(true) }, confirm);
    const box = h("div", { class: "box", role: "dialog", "aria-modal": "true", "aria-labelledby": `${id}-t`, "aria-describedby": `${id}-d` },
      h("h4", { id: `${id}-t` }, title), h("p", { id: `${id}-d` }, body), h("div", { class: "row" }, cancel, ok));
    const overlay = h("div", { class: "confirm", onClick: (e) => { if (e.target === overlay) finish(false); } }, box);
    overlay.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); finish(false); return; }
      if (e.key !== "Tab") return;
      const [first, last] = [cancel, ok];  // the only two stops: wrap at the ends, let the browser move between them
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    });
    document.body.append(overlay);
    cancel.focus();  // the safe choice
  });
}

/** Ask, then remove each model through the daemon's remove route. Returns the names that went (none when cancelled). */
export async function removeModels(names) {
  if (!names.length || !(await confirmDialog(removeCopy(names)))) return [];
  const done = [], failed = [];
  for (const name of names) {
    try {
      await api("DELETE", `/admin/models/${encodeURIComponent(name)}`);
      done.push(name);
    } catch (e) {
      failed.push(`Could not remove ${name}: ${e.message}`);
    }
  }
  if (done.length) toast(`Removed ${done.length === 1 ? done[0] : `${done.length} models`} · $ mxc rm ${done.join(" ")}`);
  if (failed.length) toast(failed.join(" · "));  // a refusal outranks the success note
  await refresh();
  return done;
}
