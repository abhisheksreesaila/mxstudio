// omxconsole: plain pages (ADR 0005). One nav (nav.js), a top bar that says where you are plus the Beacon (topbar.js),
// and one page at a time chosen by the URL hash (routes.js, pages.js). Each page is still a blade-*.js screen,
// registered on import (see blades.js for the contract), until the model page and the library replace them.
import { $, startPolling } from "./core.js";
import { mountNav } from "./nav.js";
import { mountTopbar } from "./topbar.js";
import { startPages } from "./pages.js";
import { startSystemPolling } from "./system-data.js";
import "./blade-home.js";
import "./model-page.js";
import "./blade-add.js";
import "./blade-security.js";
import "./blade-keys.js";
import "./blade-bench.js";
import "./blade-logs.js";
import "./blade-notebook.js";
import "./blade-gateway.js";
import "./blade-settings.js";

mountNav($("nav"));
mountTopbar($("topbar"));
startPolling();
startSystemPolling();
startPages($("journey"));
