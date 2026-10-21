"""omxqua, the design language, is vendored into the console (F-042) and served from a fixed list."""
from test_gateway import client, http, key, store, sup  # noqa: F401  (shared fixtures)

OMXQUA = {
    "src/fonts.css": "text/css", "src/tokens.css": "text/css", "src/components.css": "text/css",
    "src/omxqua.js": "javascript",
    "fonts/geist-variable.woff2": "font/woff2", "fonts/geist-mono-variable.woff2": "font/woff2",
}


async def test_every_omxqua_file_is_served_with_the_console_headers(http):
    for path, kind in OMXQUA.items():
        r = await http.get(f"/console/omxqua/{path}")
        assert r.status_code == 200, path
        assert kind in r.headers["content-type"], (path, r.headers["content-type"])
        assert r.headers["content-security-policy"].startswith("default-src 'self'"), path
        assert r.headers["x-content-type-options"] == "nosniff", path


async def test_nothing_outside_the_list_is_served(http):
    for path in ["VERSION", "src/../VERSION", "..%2Fgateway.py", "src/nope.css", "fonts/LICENSE-geist.txt",
                 "fonts/nunito-variable.woff2", "src", ""]:
        assert (await http.get(f"/console/omxqua/{path}")).status_code == 404, path


async def test_encoded_paths_never_reach_the_filesystem(http):
    for raw in ["%2e%2e/gateway.py", "src%2f..%2f..%2fgateway.py", "src/%2e%2e/VERSION", "%2e%2e%2f%2e%2e%2fgateway.py"]:
        assert (await http.get(f"/console/omxqua/{raw}")).status_code == 404, raw
    from mxconsole.gateway import CONSOLE_OMXQUA
    assert all(".." not in p and not p.startswith("/") for p in CONSOLE_OMXQUA)  # the list itself is plain relative paths


async def test_fonts_never_block_text(http):
    css = (await http.get("/console/omxqua/src/fonts.css")).text
    assert css.count("font-display: swap") == css.count("@font-face") > 0


async def test_omxqua_is_refused_through_a_proxy(http):
    assert (await http.get("/console/omxqua/src/tokens.css", headers={"Host": "evil.example"})).status_code == 403


def test_the_vendored_copy_is_a_pinned_omxqua_release():
    from mxconsole.gateway import CONSOLE_DIR
    version = (CONSOLE_DIR / "omxqua" / "VERSION").read_text()
    assert version.startswith("ref: v0.1.5")
    # the console works under default-src 'self': omxqua must not inject styles
    assert 'createElement("style")' not in (CONSOLE_DIR / "omxqua" / "src" / "omxqua.js").read_text()


async def test_the_console_loads_omxqua_before_its_own_css(http):
    page = (await http.get("/console")).text
    for f in ("fonts.css", "tokens.css", "components.css"):
        assert f'<link rel="stylesheet" href="/console/omxqua/src/{f}">' in page, f
        assert page.index(f"/console/omxqua/src/{f}") < page.index("/console/blades.css"), f
    assert '<script src="/console/omxqua/src/omxqua.js" defer></script>' in page


async def test_the_api_reference_page_shares_the_omxqua_tokens(http):
    page = (await http.get("/docs")).text
    assert page.index("/console/omxqua/src/tokens.css") < page.index("/console/blades.css")


async def test_the_console_uses_omxquas_fonts_not_its_own_copies(http):
    page = (await http.get("/console")).text
    assert '<link rel="stylesheet" href="/console/omxqua/src/fonts.css">' in page
    assert page.index("/console/omxqua/src/fonts.css") < page.index("/console/blades.css")
    assert "@font-face" not in (await http.get("/console/blades.css")).text
    assert (await http.get("/console/geist-variable.woff2")).status_code == 404
