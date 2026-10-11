// mxconsole, the blade layout math. Pure: no DOM, so it is tested under node.
// A "spec" is {wide, side, narrow, min}: the widths a blade asks for. The answer is one mode per blade:
//   full   the newest blade, takes what is left       side    docked beside the newest, at its `side` width
//   narrow the same, in its `narrow` width            rail    folded to a 52px strip, click it to come back
//   hidden not shown at all (phones show only the newest blade)
export const RAIL_W = 52;
export const PHONE_W = 860;
const DEFAULTS = { side: 600, narrow: 0, min: 520 };

export function planLayout(specs, width, phone = PHONE_W) {
  const n = specs.length;
  if (!n) return [];
  if (width <= phone) return specs.map((_, i) => (i === n - 1 ? "full" : "hidden"));
  const spec = (i) => ({ ...DEFAULTS, ...specs[i] });
  const last = spec(n - 1);
  // the newest is full; the one before it sits beside it; older ones (or all of them, for a wide blade) fold
  const modes = specs.map((_, i) => (i === n - 1 ? "full" : (i < n - 2 || last.wide) ? "rail" : "side"));
  const need = () => modes.reduce((sum, mode, i) => sum + (mode === "rail" ? RAIL_W : mode === "side" ? spec(i).side : mode === "narrow" ? spec(i).narrow : spec(i).min), 0);
  if (need() > width) modes.forEach((mode, i) => { if (mode === "side" && spec(i).narrow) modes[i] = "narrow"; });
  if (need() > width) modes.forEach((mode, i) => { if (mode === "side" || mode === "narrow") modes[i] = "rail"; });
  return modes;
}
