// mxconsole, the Settings blade: a placeholder until F-028. Only this file and blade-settings.css change then.
import { register, placeholder } from "./blades.js";

register("settings", (blade) => ({
  title: "Settings",
  crumb: "Settings",
  sub: "",
  icon: "sliders-horizontal", hue: "var(--hue-settings)", side: 600, min: 520,
  mount(body) {
    body.replaceChildren(placeholder("F-028", "Theme, the idle-unload default and daemon info."));
  },
}));
