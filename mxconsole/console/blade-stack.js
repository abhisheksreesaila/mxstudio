// mxconsole, the blade stack rules. Pure: no DOM, so the order of opening and closing is tested under node.

/** Close every blade after the first n (the home blade, index 0, never closes). Newest first, each onClose once.
 *  One blade that throws while closing is reported and skipped, never strands the others. Returns the closed blades. */
export function closeFrom(stack, n, onGone) {
  const gone = stack.splice(Math.max(1, n));
  for (const blade of gone.reverse()) {
    blade.alive = false;
    try { if (blade.onClose) blade.onClose(); } catch (e) { console.error(`blade "${blade.name || blade.n}" failed to close cleanly`, e); }
    if (onGone) onGone(blade);
  }
  return gone;
}

/** Dock `blade` after index `at`: everything to its right closes first. */
export function openAt(stack, blade, at, onGone) {
  closeFrom(stack, at + 1, onGone);
  stack.push(blade);
  return blade;
}

/** Esc closes the newest blade: the count to truncate to, or null when only the home blade is open. */
export function escapeTarget(count) {
  return count > 1 ? count - 1 : null;
}
