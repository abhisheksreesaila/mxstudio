// MX Console: wires the shell, the pages and the palette together.
import { startPolling } from "./core.js";
import { mountShell } from "./shell.js";
import { installPaletteShortcut } from "./palette.js";
import { overviewPage } from "./page-overview.js";
import { modelsPage } from "./page-models.js";
import { metricsPage } from "./page-metrics.js";
import { apiPage } from "./page-api.js";
import { keysPage } from "./page-keys.js";
import { networkPage } from "./page-network.js";
import { activityPage, soonPage } from "./page-activity.js";
import { benchPage } from "./page-bench.js";

mountShell({
  overview: overviewPage, activity: activityPage, models: modelsPage, keys: keysPage, network: networkPage, api: apiPage, metrics: metricsPage,
  pull: soonPage("pull"), ports: soonPage("ports"), benchmarks: benchPage, logs: soonPage("logs"), gateway: soonPage("gateway"), daemon: soonPage("daemon"),
});
installPaletteShortcut();
startPolling();
