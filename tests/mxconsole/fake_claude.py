"""Stand-in for `claude -p`. Env: FAKE_CLAUDE_MODE = ok | fail_once | broken | broken_n | regress | parity_once |
hang | crash (broken_n: a different build error each call; regress: call 1 gets to parity, later calls break the build),
FAKE_CLAUDE_LOG (append argv, cwd and prompt as a JSON line), FAKE_CLAUDE_PIDS (hang: write own pid and a
grandchild's). It finds the package folder in the prompt ("Package folder: <path>") and writes a minimal
package there. fail_once / parity_once: the first call writes a bad package, later calls (which must carry
the error text) write a good one, so auto-fix can be tested."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

argv = sys.argv[1:]
prompt = argv[argv.index("-p") + 1]
if os.environ.get("FAKE_CLAUDE_LOG"):
    with open(os.environ["FAKE_CLAUDE_LOG"], "a") as f:
        cfg = os.environ.get("CLAUDE_CONFIG_DIR", "")
        creds = Path(cfg, ".credentials.json")
        f.write(json.dumps({"argv": argv, "cwd": os.getcwd(), "prompt": prompt, "env": sorted(os.environ),
                            "config_dir": cfg, "creds": creds.read_text() if creds.is_file() else None}) + "\n")
if os.environ.get("FAKE_CLAUDE_TAMPER"):  # an agent that escaped and edited a gate script
    with open(os.environ["FAKE_CLAUDE_TAMPER"], "a") as f:
        f.write("\n# pwned\n")
mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
state = Path(os.getcwd()) / ".fake-claude-calls"
calls = int(state.read_text()) + 1 if state.exists() else 1
state.write_text(str(calls))


def emit(obj):
    print(json.dumps(obj), flush=True)


emit({"type": "assistant", "message": {"content": [{"type": "text", "text": f"Porting (call {calls})"}]}})
emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash",
                                                      "input": {"command": "pixi run python scripts/scaffold.py"}}]}})
if mode == "crash":
    print("claude: authentication failed", file=sys.stderr)
    sys.exit(2)
if mode == "hang":
    gc = subprocess.Popen(["sleep", "60"])
    Path(os.environ["FAKE_CLAUDE_PIDS"]).write_text(f"{os.getpid()} {gc.pid}")
    time.sleep(60)
    sys.exit(0)

folder = Path(prompt.split("Package folder: ", 1)[1].split()[0])
folder.mkdir(parents=True, exist_ok=True)
(folder / "__init__.py").write_text("ARCHITECTURES = []\n")
if calls > 1:
    assert "Fix this" in prompt and "attempt" in prompt, "a retry must carry the error text"
marker = "fine"
if mode == "broken" or (mode == "fail_once" and calls == 1) or (mode == "regress" and calls > 1):
    marker = "BROKEN"
elif mode == "broken_n":
    marker = f"BROKEN variant {calls}"
elif mode == "regress" or (mode == "parity_once" and calls == 1):
    marker = "PARITY_BAD"
(folder / "arch.py").write_text(f"# {marker}\n")
emit({"type": "result", "subtype": "success", "is_error": False, "result": "done"})
