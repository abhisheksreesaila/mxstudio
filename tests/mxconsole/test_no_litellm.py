"""F-063: LiteLLM is gone. Nothing installs, imports, launches or configures it, and the console never mentions it.

The only mentions left are the cleanup of what an older install left behind (expose.LEGACY_*), the hidden,
deprecated `--no-litellm` flag, and three history notes; each is allowlisted by file and exact line below, so a new
mention anywhere fails until someone adds it here on purpose.
"""
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[2]
SCANNED = [ROOT / "mxconsole", ROOT / "scripts"]
SUFFIXES = {".py", ".js", ".mjs", ".html", ".css", ".sh", ".toml", ".yaml", ".yml", ".json", ".in", ".txt"}
# The exact lines (stripped) allowed to say "litellm", per file.
ALLOWED = {
    "mxconsole/expose.py": {
        "(Until F-063 LiteLLM sat between Caddy and the daemon; `stop()` still cleans up after it.)",
        "# Left by installs from before F-063: a LiteLLM process (litellm.pid) and its internal app key.",
        'LEGACY_PROCESSES = ("litellm",)',
        'LEGACY_KEYS = ("_litellm",)',
        '"""Stop Caddy (and a LiteLLM left running by an install from before F-063, revoking its key)."""',
    },
    "mxconsole/cli.py": {
        'no_litellm: bool = typer.Option(False, "--no-litellm", hidden=True,',
        'help="Deprecated no-op: LiteLLM was removed (F-063)."),',
        "if no_litellm:",
        'typer.echo("note: --no-litellm is no longer needed: LiteLLM was removed and Caddy always "',
    },
    "mxconsole/params.py": {
        '"""Drop request fields `max serve` would refuse (F-063; LiteLLM\'s `drop_params` did this before).',
    },
}


def files():
    for top in SCANNED:
        for path in top.rglob("*"):
            if path.is_file() and path.suffix in SUFFIXES and "omxqua" not in path.parts:
                yield path


def test_no_code_imports_launches_or_configures_litellm():
    offending = []
    for path in files():
        for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            if "litellm" in line.lower() and line.strip() not in ALLOWED.get(str(path.relative_to(ROOT)), ()):
                offending.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()}")
    assert offending == [], "\n".join(offending)


def test_the_allowlisted_lines_never_import_or_start_it():
    for path in files():
        text = path.read_text(errors="replace")
        assert not re.search(r"^\s*(import|from)\s+litellm\b", text, re.M), path
        assert "litellm.yaml" not in text and "LITELLM_" not in text and "/admin/litellm" not in text, path
        assert "envs/gateway" not in text, path


def test_pixi_has_no_gateway_environment_and_no_litellm_package():
    manifest = tomllib.loads((ROOT / "pixi.toml").read_text())
    assert "gateway" not in manifest.get("environments", {})
    assert "gateway" not in manifest.get("feature", {})
    assert "litellm" not in (ROOT / "pixi.toml").read_text().lower()
    lock = (ROOT / "pixi.lock").read_text()
    assert not re.search(r"^\s*(name: litellm|- pypi: .*litellm)", lock, re.M | re.I)
    assert "  gateway:" not in lock


def test_the_console_gateway_page_shows_caddy_not_litellm():
    page = (ROOT / "mxconsole" / "console" / "blade-gateway.js").read_text()
    assert "litellm" not in page.lower()
    assert "/admin/caddy/log" in page and "Caddy log" in page
    for name in ("blade-security.js", "system-data.js", "blade-logs.js", "index.html"):
        assert "litellm" not in (ROOT / "mxconsole" / "console" / name).read_text().lower(), name
