"""README.md's HTTP API table is the gateway's public API: one row per method and path the app serves outside the
console, its pages and /admin, and the same set the OpenAPI spec at /openapi.json documents. Links resolve: local
ones, the GitHub Pages ones (docs/ is the site root), and the docs pages' own relative links and images."""
import re
import sys
from pathlib import Path

from mxconsole.gateway import create_app, openapi_spec
from mxconsole.store import Store
from mxconsole.supervisor import Supervisor

ROOT = Path(__file__).parents[2]
README = (ROOT / "README.md").read_text()
SITE = ROOT / "docs" / "site"
ROW = re.compile(r"^\| `(GET|POST|PUT|PATCH|DELETE) (/[^`]*)` \|", re.M)
PAGES_URL = re.compile(r"https://abhisheksreesaila\.github\.io/mxstudio/([^)\s\"'#]*)")
NOT_API = {"/console", "/console/{asset}", "/favicon.ico", "/docs", "/openapi.json", "/v1/{path:path}"}


def api_section() -> str:
    m = re.search(r"^## HTTP API$(.*?)(?=^## )", README + "\n## ", re.M | re.S)
    assert m, "README.md has no '## HTTP API' section"
    return m.group(1)


def served_routes() -> set:
    store = Store.open()
    app = create_app(store, Supervisor(store, max_command=(sys.executable, "-c", "pass")))
    return {(m, r.path) for r in app.routes for m in (getattr(r, "methods", None) or ())
            if m != "HEAD" and not r.path.startswith("/admin") and r.path not in NOT_API}


def test_api_table_matches_the_routes_and_the_openapi_spec():
    table = set(ROW.findall(api_section()))
    spec = {(method.upper(), path) for path, ops in openapi_spec([])["paths"].items() for method in ops}
    assert table == served_routes()
    assert table == spec


def test_readme_local_links_and_images_exist():
    targets = re.findall(r"\]\(((?!https?://|#)[^)\s]+)\)", README)
    assert targets, "README.md links nothing local"
    missing = [t for t in targets if not (ROOT / t.split("#")[0]).exists()]
    assert not missing, f"README.md links missing files: {missing}"


def test_github_pages_links_point_at_real_pages():
    sources = [README, *(p.read_text() for p in SITE.glob("*.html"))]
    paths = {m for s in sources for m in PAGES_URL.findall(s)}
    assert paths, "nothing links the docs site"
    missing = [p for p in paths if not (ROOT / "docs" / p / ("index.html" if p.endswith("/") or not p else "")).exists()]
    assert not missing, f"GitHub Pages links with no page under docs/: {missing}"


def test_docs_pages_relative_links_and_images_exist():
    missing = []
    for page in [*SITE.glob("*.html"), SITE / "site.js"]:
        text = page.read_text()
        refs = re.findall(r"""(?:src|href)=["']([^"'#$]+)""", text) + re.findall(r'\["([a-z-]+\.html)", "', text)
        for ref in refs:
            if not re.match(r"[a-z]+:", ref) and not (page.parent / ref).exists():
                missing.append(f"{page.name}: {ref}")
    assert not missing, f"docs pages link missing files: {missing}"
