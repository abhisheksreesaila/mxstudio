// Runs before the page paints, so a saved theme never flashes. Themes: contrast (default), light, dark.
(() => {
  try {
    const saved = localStorage.getItem("mxc-theme");
    if (saved === "light" || saved === "dark" || saved === "contrast") document.documentElement.dataset.theme = saved;
  } catch (_) { /* storage blocked: keep the default */ }
})();
