// mxconsole, the part of /admin/system the shell needs: per-model GPU memory for the hardware chip.
// Polled on its own, slower than the main data; a failure just leaves the old numbers (or none).
import { api, emitData } from "./core.js";

export const system = { crew: null, listeners: null, at: 0 };
const EVERY_MS = 5000;

export async function refreshSystem() {
  try {
    const body = await api("GET", "/admin/system");
    system.crew = Array.isArray(body.crew) ? body.crew : null;
    system.listeners = Array.isArray(body.listeners) ? body.listeners : null;
    system.at = Date.now();
  } catch (_) {
    system.crew = null;
    system.listeners = null;
  }
  emitData();
}
export function startSystemPolling() {
  refreshSystem();
  setInterval(refreshSystem, EVERY_MS);
}
