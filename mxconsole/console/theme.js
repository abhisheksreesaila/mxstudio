// Runs before the page paints, so a saved theme never flashes. Light is the default look and dark follows the
// system until the top-bar toggle is used; only an explicit choice ("light" or "dark") is remembered.
(() => {
  try {
    const saved = localStorage.getItem("mxc-theme");
    if (saved === "light" || saved === "dark") document.documentElement.dataset.theme = saved;
  } catch (_) { /* storage blocked: follow the system */ }
})();
