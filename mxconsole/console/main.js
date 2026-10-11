// mxconsole: the blades console. A dark top bar, a journey of blades docked side by side, My models at home.
// Each blade is its own blade-*.js (registered on import) with its own CSS; see blades.js for the contract.
import { $, startPolling } from "./core.js";
import { mountJourney, push } from "./blades.js";
import { mountTopbar } from "./topbar.js";
import { startSystemPolling } from "./system-data.js";
import "./blade-home.js";
import "./blade-model.js";
import "./blade-add.js";
import "./blade-security.js";
import "./blade-keys.js";
import "./blade-bench.js";
import "./blade-logs.js";
import "./blade-notebook.js";
import "./blade-gateway.js";
import "./blade-settings.js";

mountJourney($("journey"));
mountTopbar($("topbar"), $("hwpop"));
push("home");
startPolling();
startSystemPolling();
