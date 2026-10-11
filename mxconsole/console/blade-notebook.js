// mxconsole, the Notebook blade: a placeholder until F-028. Only this file and blade-notebook.css change then.
import { register, placeholder } from "./blades.js";

register("notebook", (blade) => ({
  title: "Notebook",
  crumb: "Notebook",
  sub: blade.opts.id || "",
  icon: "book-open", hue: "var(--hue-notebook)", wide: true, min: 900,
  mount(body) {
    body.replaceChildren(placeholder("F-028", "The generated notebook for a model, read-only, with Open in Jupyter."));
  },
}));
