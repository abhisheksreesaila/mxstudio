// mxconsole, the Logs blade: a placeholder until F-027. Only this file and blade-logs.css change then.
import { register, placeholder } from "./blades.js";
import { onData } from "./core.js";

register("logs", (blade) => ({
  title: "Logs",
  crumb: "Logs",
  sub: blade.opts.id || "",
  icon: "scroll-text", hue: "var(--hue-logs)", side: 600, min: 520,
  mount(body) {
    body.replaceChildren(placeholder("F-027", "A model's log: follow, pause and filter."));
    // the pattern for anything mount starts (a subscription, a timer, a fetch loop): tear it down in onClose
    const off = onData(() => { /* redraw from the polled data here */ });
    blade.onClose = off;
  },
}));
