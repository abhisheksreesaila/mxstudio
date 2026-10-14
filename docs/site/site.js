// mxconsole docs pages: the shared top bar and the light/dark toggle. Each page has <header class="top" data-page="…">.
const PAGES = [["index.html", "Overview"], ["console.html", "Console tour"], ["porting.html", "Porting, behind the scenes"]];
const REPO = "https://github.com/abhisheksreesaila/mxstudio";
const bar = document.querySelector("header.top");
if (bar) {
  const here = bar.dataset.page;
  bar.innerHTML = `<a class="brand" href="index.html">mxconsole <small>docs</small></a><nav>${PAGES.map(([href, label]) =>
    `<a href="${href}"${href === here ? ' aria-current="page"' : ""}>${label}</a>`).join("")}
    <a href="${REPO}/blob/main/docs/CLI.md">CLI reference ↗</a><a href="${REPO}">GitHub ↗</a></nav>
    <button class="theme" type="button" aria-label="Switch light or dark">◐</button>`;
  const saved = localStorage.getItem("mxc-docs-theme");
  if (saved) document.documentElement.classList.add(saved);
  bar.querySelector(".theme").addEventListener("click", () => {
    const dark = document.documentElement.classList.contains("dark") ||
      (!document.documentElement.classList.contains("light") && matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.classList.remove("dark", "light");
    document.documentElement.classList.add(dark ? "light" : "dark");
    localStorage.setItem("mxc-docs-theme", dark ? "light" : "dark");
  });
}
