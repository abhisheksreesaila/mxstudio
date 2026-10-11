// mxconsole, the Security blade: a placeholder until F-025. Only this file and blade-security.css change then.
import { register, placeholder } from "./blades.js";

register("security", (blade) => ({
  title: "Security",
  crumb: "Security",
  sub: blade.opts.id || "",
  icon: "lock", hue: "var(--hue-keys)", side: 600, min: 520,
  mount(body) {
    body.replaceChildren(placeholder("F-025", "Make a model reachable on your network with HTTPS and an API key."));
  },
}));
