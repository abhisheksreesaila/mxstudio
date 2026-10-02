"""Stand-in for `max serve`. Env: FAKE_MAX_DELAY (s before healthy), FAKE_MAX_FAIL (exit 1),
FAKE_MAX_ARGS_FILE (write argv there), FAKE_MAX_STARTS_FILE (append a line per start)."""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

args = sys.argv[1:]
if os.environ.get("FAKE_MAX_ARGS_FILE"):
    open(os.environ["FAKE_MAX_ARGS_FILE"], "w").write(json.dumps(args))
if os.environ.get("FAKE_MAX_STARTS_FILE"):
    open(os.environ["FAKE_MAX_STARTS_FILE"], "a").write("start\n")
if os.environ.get("FAKE_MAX_FAIL"):
    print("boom: cannot load model", file=sys.stderr, flush=True)
    sys.exit(1)
if os.environ.get("FAKE_MAX_PID_FILE"):
    open(os.environ["FAKE_MAX_PID_FILE"], "w").write(str(os.getpid()))
port = int(args[args.index("--port") + 1])
served = args[args.index("--served-model-name") + 1] if "--served-model-name" in args else "unnamed"
time.sleep(float(os.environ.get("FAKE_MAX_DELAY", "0")))


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        ok = self.path in ("/health", "/v1/models")
        self.send_response(200 if ok else 404)
        self.end_headers()
        self.wfile.write(json.dumps({"data": [{"id": served}]}).encode() if ok else b"")

    def log_message(self, *a):
        pass


HTTPServer(("127.0.0.1", port), H).serve_forever()
