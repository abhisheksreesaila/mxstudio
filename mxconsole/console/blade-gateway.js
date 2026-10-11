// mxconsole, the Gateway blade: a placeholder until F-028. Only this file and blade-gateway.css change then.
import { register, placeholder } from "./blades.js";

register("gateway", (blade) => ({
  title: "Gateway",
  crumb: "Gateway",
  sub: "",
  icon: "route", hue: "var(--hue-gateway)", side: 600, min: 520,
  mount(body) {
    body.replaceChildren(placeholder("F-028", "LiteLLM routes: which model each name goes to, and the gateway's status."));
  },
}));
