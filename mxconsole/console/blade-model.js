// mxconsole, the Model blade: a placeholder until F-025. Only this file and blade-model.css change then.
import { register, placeholder } from "./blades.js";

register("model", (blade) => ({
  title: blade.opts.id || "Model",
  crumb: blade.opts.id || "Model",
  sub: "Model",
  icon: "box", hue: "var(--hue-models)", side: 660, narrow: 470, min: 620,
  mount(body) {
    body.replaceChildren(placeholder("F-025", "A model's resource blade: menu, command bar, essentials, live tokens, call it and hardware fit."));
  },
}));
