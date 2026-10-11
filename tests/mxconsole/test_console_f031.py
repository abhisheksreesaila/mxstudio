"""F-031: the old console pages are gone; what they did lives in a blade or the CLI."""
from test_console_blades import needs_node, node

from mxconsole.gateway import CONSOLE_ASSETS, CONSOLE_DIR

REMOVED = ["console.css", "shell.js", "palette.js", "actions.js", "charts.js", "page-overview.js", "page-activity.js", "page-models.js",
           "page-metrics.js", "page-api.js", "page-keys.js", "page-network.js", "page-ports.js", "page-logs.js", "page-gateway.js",
           "page-daemon.js", "page-bench.js", "page-pull.js"]


def test_the_old_pages_and_their_helpers_are_gone():
    for name in REMOVED:
        assert name not in CONSOLE_ASSETS and not (CONSOLE_DIR / name).exists(), name


def test_no_script_still_imports_a_removed_file():
    import re
    stems = {n.rsplit(".", 1)[0] for n in REMOVED if n.endswith(".js")}
    for path in CONSOLE_DIR.glob("*.js"):
        for target in re.findall(r'from "\./([\w-]+)\.js"|import "\./([\w-]+)\.js"', path.read_text()):
            assert not ({*target} & stems), f"{path.name} imports a removed file"


@needs_node
def test_recent_requests_rows_for_the_gateway_blade():
    rows = node("m.requestRows([{ts: 1, app: 'myapp', model: 'tiny', route: 'chat', status: 200, prompt_tokens: 5, completion_tokens: 12, estimated: true, ttft_ms: 40, duration_ms: 900},"
                "{ts: 2, app: 'x', model: null, route: 'chat', status: 401, completion_tokens: null, ttft_ms: null, duration_ms: 3}], 5)", "blade-gateway.js")
    assert rows[0]["tokens"] == "5 → ~12" and rows[0]["ttft"] == "40 ms" and rows[0]["ok"] is True and rows[0]["app"] == "myapp"
    assert rows[1]["tokens"] == "—" and rows[1]["ttft"] == "—" and rows[1]["model"] == "—" and rows[1]["ok"] is False
    assert node("m.requestRows(null)", "blade-gateway.js") == []
    assert len(node("m.requestRows(Array(30).fill({status: 200}))", "blade-gateway.js")) == 12


def fake_page(system_dark: str, storage: str) -> str:
    return f"""const saved = {{}}; const heard = [];
      globalThis.document = {{ documentElement: {{ dataset: {{}} }} }};
      globalThis.window = {{ {storage},
        matchMedia: () => ({{ matches: {system_dark}, addEventListener() {{}}, removeEventListener() {{}} }}),
        addEventListener: (t, fn) => heard.push([t, fn]), removeEventListener() {{}},
        dispatchEvent: (e) => {{ heard.forEach(([t, fn]) => t === e.type && fn(e)); }} }};"""


WORKING = "localStorage: { getItem: (k) => saved[k] ?? null, setItem: (k, v) => { saved[k] = v; } }"
BLOCKED = "get localStorage() { throw new Error('blocked'); }"


@needs_node
def test_one_setTheme_remembers_applies_and_tells_every_listener():
    body = """let n = 0; m.onTheme(() => n++);
      const before = m.currentTheme(); m.setTheme('light'); m.setTheme('nonsense');
      return [before, m.currentTheme(), document.documentElement.dataset.theme, saved['mxc-theme'], n];"""
    assert node(f"(() => {{ {fake_page('true', WORKING)} {body} }})()", "prefs.js") == ["dark", "light", "light", "light", 1]


@needs_node
def test_a_blocked_storage_still_switches_the_theme_for_this_visit():
    body = "m.setTheme('dark'); return [m.currentTheme(), document.documentElement.dataset.theme];"
    assert node(f"(() => {{ {fake_page('false', BLOCKED)} {body} }})()", "prefs.js") == ["dark", "dark"]
