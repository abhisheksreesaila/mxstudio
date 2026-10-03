// Hotfire Mission Control, the things the console can do to a model. The palette, the Models page and the
// insights all call these, so each one has one behaviour and one CLI twin.
import { api, refresh, toast } from "./core.js";

const enc = encodeURIComponent;

export async function loadModel(name) {
  toast(`Loading ${name}… the first load compiles the model.`, 6000);
  try {
    await api("POST", `/admin/models/${enc(name)}/load`);
    toast(`${name} is ready.`);
    return true;
  } catch (e) {
    toast(`Load failed: ${e.message}`, 7000);
    return false;
  } finally {
    await refresh();
  }
}

export async function stopModel(name) {
  try {
    await api("POST", `/admin/models/${enc(name)}/stop`);
    toast(`${name} is unloaded.`);
    return true;
  } catch (e) {
    toast(`Stop failed: ${e.message}`, 7000);
    return false;
  } finally {
    await refresh();
  }
}

export async function setPinned(name, pinned) {
  try {
    await api("PATCH", `/admin/models/${enc(name)}`, { pinned });
    toast(pinned ? `${name} stays loaded.` : `${name} unloads when idle again.`);
    return true;
  } catch (e) {
    toast(`Not saved: ${e.message}`, 7000);
    return false;
  } finally {
    await refresh();
  }
}

export async function saveSettings(name, patch) {
  try {
    await api("PATCH", `/admin/models/${enc(name)}`, patch);
    return null;
  } catch (e) {
    return e.message;
  } finally {
    await refresh();
  }
}

/** The CLI twins, in one place so the palette, sheet and insights print the same commands. */
export const cli = {
  serve: (name) => `hotfire serve ${name}`,
  stop: (name) => `hotfire stop ${name}`,
  pin: (name) => `hotfire set ${name} --pin`,
  unpin: (name) => `hotfire set ${name} --unpin`,
  keepAlive: (name, text) => `hotfire set ${name} --keep-alive ${text}`,
  logs: (name) => `hotfire logs ${name} -f`,
};
