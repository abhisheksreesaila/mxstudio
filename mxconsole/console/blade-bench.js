// mxconsole, the Benchmark blade: a placeholder until F-027. Only this file and blade-bench.css change then.
import { register, placeholder } from "./blades.js";

register("bench", (blade) => ({
  title: "Benchmark",
  crumb: "Benchmark",
  sub: blade.opts.id || "",
  icon: "gauge", hue: "var(--hue-bench)", side: 600, min: 520,
  mount(body) {
    body.replaceChildren(placeholder("F-027", "Decode tokens per second and first-token time across your models."));
  },
}));
