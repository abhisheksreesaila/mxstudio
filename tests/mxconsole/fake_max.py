"""Stand-in for `max serve`. Env: FAKE_MAX_DELAY (s before healthy), FAKE_MAX_FAIL (exit 1),
FAKE_MAX_ARGS_FILE (write argv there), FAKE_MAX_ARGS_LOG (append argv as a JSON line), FAKE_MAX_STARTS_FILE (append a line per start).
It also answers POST /v1/{chat/completions,completions,embeddings}, echoing the requested model:
stream=true gives SSE chunks then `data: [DONE]`. FAKE_MAX_CHUNK_DELAY (s between chunks).
FAKE_MAX_GZIP gzips JSON replies when the caller accepts gzip.
Benchmark knobs (streamed replies): FAKE_MAX_TOKENS_OUT (chunks to stream, capped by max_tokens; unset keeps
the three words), FAKE_MAX_TTFT_DELAY (s before the first chunk), FAKE_MAX_MODEL_DELAYS ("a=0.004,b=0.01":
per-model chunk delay), FAKE_MAX_USAGE (end with a usage block when the caller asks for it)."""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

args = sys.argv[1:]
if os.environ.get("FAKE_MAX_ARGS_FILE"):
    open(os.environ["FAKE_MAX_ARGS_FILE"], "w").write(json.dumps(args))
if os.environ.get("FAKE_MAX_ARGS_LOG"):
    open(os.environ["FAKE_MAX_ARGS_LOG"], "a").write(json.dumps(args) + "\n")
if os.environ.get("FAKE_MAX_STARTS_FILE"):
    open(os.environ["FAKE_MAX_STARTS_FILE"], "a").write("start\n")
if os.environ.get("FAKE_MAX_FAIL"):
    print("boom: cannot load model", file=sys.stderr, flush=True)
    sys.exit(1)
if os.environ.get("FAKE_MAX_PID_FILE"):
    open(os.environ["FAKE_MAX_PID_FILE"], "w").write(str(os.getpid()))
if os.environ.get("FAKE_MAX_SPAWN_CHILD"):  # a grandchild in the same process group, like a worker
    import subprocess
    gc = subprocess.Popen(["sleep", "60"])
    open(os.environ["FAKE_MAX_SPAWN_CHILD"], "w").write(str(gc.pid))
port = int(args[args.index("--port") + 1])
served = args[args.index("--served-model-name") + 1] if "--served-model-name" in args else "unnamed"
time.sleep(float(os.environ.get("FAKE_MAX_DELAY", "0")))


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        ok = self.path in ("/health", "/v1/models")
        self.send_response(200 if ok else 404)
        self.end_headers()
        self.wfile.write(json.dumps({"data": [{"id": served}]}).encode() if ok else b"")

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        model = body.get("model")
        if self.path not in ("/v1/chat/completions", "/v1/completions", "/v1/embeddings"):
            self.send_response(404)
            self.end_headers()
            return
        if self.path == "/v1/embeddings":
            return self._json({"object": "list", "model": model, "data": [
                {"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}]})
        chat = self.path == "/v1/chat/completions"
        if not body.get("stream"):
            choice = ({"index": 0, "message": {"role": "assistant", "content": f"hello from {model}"},
                       "finish_reason": "stop"} if chat else
                      {"index": 0, "text": f"hello from {model}", "finish_reason": "stop"})
            return self._json({"id": "cmpl-1", "object": "chat.completion" if chat else "text_completion",
                               "created": 1, "model": model, "choices": [choice],
                               "seen_limits": {k: body.get(k) for k in ("max_tokens", "max_completion_tokens") if k in body},
                               "usage": {"prompt_tokens": 4, "completion_tokens": 12, "total_tokens": 16}})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        delays = dict(p.split("=", 1) for p in os.environ.get("FAKE_MAX_MODEL_DELAYS", "").split(",") if "=" in p)
        delay = float(delays.get(model, os.environ.get("FAKE_MAX_CHUNK_DELAY", "0")))
        out = os.environ.get("FAKE_MAX_TOKENS_OUT")
        words = ["tok"] * min(int(out), body.get("max_tokens") or int(out)) if out else ["hello", " from ", model]
        time.sleep(float(os.environ.get("FAKE_MAX_TTFT_DELAY", "0")))
        for word in words:
            delta = ({"index": 0, "delta": {"content": word}, "finish_reason": None} if chat else
                     {"index": 0, "text": word, "finish_reason": None})
            chunk = {"id": "cmpl-1", "object": "chat.completion.chunk" if chat else "text_completion",
                     "created": 1, "model": model, "choices": [delta]}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.flush()
            time.sleep(delay)
        if os.environ.get("FAKE_MAX_USAGE") and (body.get("stream_options") or {}).get("include_usage"):
            usage = {"prompt_tokens": max(1, len(json.dumps(body.get("messages", ""))) // 4),
                     "completion_tokens": len(words), "total_tokens": 0}
            self.wfile.write(f"data: {json.dumps({'id': 'cmpl-1', 'model': model, 'choices': [], 'usage': usage})}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def _json(self, obj):
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        if os.environ.get("FAKE_MAX_GZIP") and "gzip" in self.headers.get("Accept-Encoding", ""):
            import gzip
            data = gzip.compress(data)
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
