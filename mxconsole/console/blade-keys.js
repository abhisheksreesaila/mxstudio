// mxconsole, the API keys blade: a placeholder until F-025. Only this file and blade-keys.css change then.
import { register, placeholder } from "./blades.js";

register("keys", (blade) => ({
  title: "API keys",
  crumb: "API keys",
  sub: "",
  icon: "key-round", hue: "var(--hue-keys)", side: 600, min: 520,
  mount(body) {
    body.replaceChildren(placeholder("F-025", "List, create, rotate and revoke the keys apps use to call your models."));
  },
}));
