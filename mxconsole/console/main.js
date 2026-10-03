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
import { portsPage } from "./page-ports.js";
import { logsPage } from "./page-logs.js";
import { gatewayPage } from "./page-gateway.js";
import { daemonPage } from "./page-daemon.js";

mountShell({
  overview: overviewPage, activity: activityPage, models: modelsPage, keys: keysPage, network: networkPage, api: apiPage, metrics: metricsPage,
  pull: soonPage("pull"), ports: portsPage, benchmarks: soonPage("benchmarks"), logs: logsPage, gateway: gatewayPage, daemon: daemonPage,
});
installPaletteShortcut();
startPolling();
