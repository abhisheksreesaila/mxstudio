// Runs in <head>, before the page paints, so a saved theme never flashes. Puts omxqua's root classes on <html>
// (oq-root oq-family-dev oq-night | oq-light), as the console's theme.js does. Light or dark follows the system
// until the top-bar toggle is used; site.js calls mxcDocsTheme("light" | "dark") to remember a choice.
(() => {
  const root = document.documentElement, mq = matchMedia("(prefers-color-scheme: dark)");
  let saved = null;
  try { saved = localStorage.getItem("mxc-docs-theme"); } catch (_) { /* storage blocked: follow the system */ }
  const sync = () => {
    const dark = saved ? saved === "dark" : mq.matches;
    root.classList.add("oq-root", "oq-family-dev");
    root.classList.toggle("oq-night", dark);
    root.classList.toggle("oq-light", !dark);
    root.style.colorScheme = dark ? "dark" : "light";
  };
  sync();
  mq.addEventListener("change", sync);
  window.mxcDocsTheme = (choice) => {
    saved = choice;
    try { localStorage.setItem("mxc-docs-theme", choice); } catch (_) {}
    sync();
  };
})();
