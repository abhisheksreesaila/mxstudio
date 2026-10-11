// mxconsole, the Add a model blade: a placeholder until F-026. Only this file and blade-add.css change then.
import { register, placeholder } from "./blades.js";

register("add", (blade) => ({
  title: "Add a model",
  crumb: "Add a model",
  sub: "",
  icon: "plus", hue: "var(--hue-add)", wide: true, min: 900,
  mount(body) {
    body.replaceChildren(placeholder("F-026", "Search the MAX catalog and Hugging Face, see what fits this GPU, and watch the setup stages."));
  },
}));
