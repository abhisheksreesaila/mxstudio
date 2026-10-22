#!/usr/bin/env python3
"""A stand-in for pixi in the first-run tests: records each call to $FAKE_PIXI_CALLS and does the least that
install.sh needs (envs appear, the tray answers /health on MXC_PORT, `python -m mxconsole.autostart` prints)."""
import json
import os
import subprocess
import sys

args = sys.argv[1:]
with open(os.environ["FAKE_PIXI_CALLS"], "a") as f:
    f.write(json.dumps(args) + "\n")

if args == ["--version"]:
    print("pixi 0.0.0-fake")
elif args[:1] == ["exec"]:  # exec -s python=3.12 python <server> <log> <port>
    os.execv(sys.executable, [sys.executable, *args[args.index("python", 3) + 1:]])
elif args[:1] == ["install"]:
    manifest = args[args.index("--manifest-path") + 1]
    env = args[args.index("-e") + 1] if "-e" in args else "default"
    os.makedirs(os.path.join(os.path.dirname(manifest), ".pixi", "envs", env), exist_ok=True)
    if os.environ.get("FAKE_PIXI_FAIL") == env:
        sys.exit(1)
elif args[:1] == ["run"]:
    rest = args[3:]  # after: run --manifest-path <path>
    if rest == ["python", "-m", "mxconsole.autostart"]:
        print("on from the next login; `mxc` in ~/.local/bin")
    elif rest == ["mxc", "tray"]:
        code = ("import http.server,os\n"
                "class H(http.server.BaseHTTPRequestHandler):\n"
                "  def do_GET(s):\n"
                "    b=b'{\"status\":\"ok\",\"service\":\"mst\"}'; s.send_response(200); s.end_headers(); s.wfile.write(b)\n"
                "  def log_message(s,*a): pass\n"
                "srv=http.server.HTTPServer(('127.0.0.1',int(os.environ['MXC_PORT'])),H); srv.timeout=1\n"
                "import time; end=time.time()+float(os.environ.get('FAKE_TRAY_SECONDS','20'))\n"
                "while time.time()<end: srv.handle_request()\n")
        os.execv(sys.executable, [sys.executable, "-c", code])
