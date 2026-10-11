// mxconsole, keyed in-place updates: a polled list keeps the same DOM element per key (a model name), so keyboard
// focus, hover and scroll survive every refresh. No DOM globals: tested under node with a fake parent.

/** What changed between two key lists. */
export function planKeyed(prev, next) {
  const nextSet = new Set(next), prevSet = new Set(prev);
  return { remove: prev.filter((k) => !nextSet.has(k)), add: next.filter((k) => !prevSet.has(k)) };
}

/** Make `parent`'s children match `items` ({name, ...}) in order. `cache` maps name -> element and persists between calls.
 *  New keys are built with create(item); every item (new or not) goes through update(el, item). Nodes in `parent`
 *  that are not in the cache (a trailing "Add" card) stay where they are, after the keyed ones. */
export function syncKeyed(parent, cache, items, create, update) {
  const plan = planKeyed([...cache.keys()], items.map((i) => i.name));
  for (const key of plan.remove) { cache.get(key).remove(); cache.delete(key); }
  items.forEach((item, i) => {
    let el = cache.get(item.name);
    if (!el) { el = create(item); cache.set(item.name, el); }
    update(el, item);
    const at = parent.children[i];
    if (at !== el) parent.insertBefore(el, at || null);
  });
}
