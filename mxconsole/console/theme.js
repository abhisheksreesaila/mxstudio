// Runs before the page paints, so a saved theme never flashes. Light is the default look and dark follows the
// system until the top-bar toggle is used; only an explicit choice ("light" or "dark") is remembered.
// It also keeps omxqua's root classes in step (oq-root oq-family-dev oq-night | oq-light): from data-theme when one
// is chosen, else from the system, and again whenever either changes (prefs.js only sets data-theme).
(() => {
  const root = document.documentElement;
  try {
    const saved = localStorage.getItem("mxc-theme");
    if (saved === "light" || saved === "dark") root.dataset.theme = saved;
  } catch (_) { /* storage blocked: follow the system */ }
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  const sync = () => {
    const dark = root.dataset.theme ? root.dataset.theme === "dark" : mq.matches;
    root.classList.add("oq-root", "oq-family-dev");
    root.classList.toggle("oq-night", dark);
    root.classList.toggle("oq-light", !dark);
    root.style.colorScheme = dark ? "dark" : "light";
  };
  sync();
  mq.addEventListener("change", sync);
  new MutationObserver(sync).observe(root, { attributes: true, attributeFilter: ["data-theme"] });
})();
