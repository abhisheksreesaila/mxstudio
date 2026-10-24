# omxconsole HTTP API

The gateway's public endpoints. The same set is documented interactively at `http://127.0.0.1:11435/docs`.
Commands: [CLI reference](CLI.md) · Setup and everyday use: [user guide](USER_GUIDE.md).

## HTTP API

OpenAI-compatible, on `http://127.0.0.1:11435` (or `https://<host>.local:8443` after `mxc expose`). Send
`Authorization: Bearer <key>`; make a key with `mxc keys new <app>`.

| Endpoint | Key | What it does |
|---|---|---|
| `GET /v1/models` | yes | Registered models, each with its state (`cold`, `starting`, `ready`, `crashed`) in an `mxconsole` field. |
| `POST /v1/chat/completions` | yes | Chat. `stream: true` streams server-sent events. A cold model starts first. |
| `POST /v1/completions` | yes | Plain text completion. |
| `POST /v1/embeddings` | yes | Embeddings, from an embedding model such as `all-minilm-l6`. |
| `GET /health` | no | Liveness: `{"status": "ok", "service": "mst"}`. |
| `GET /auth/check` | yes | 200 with the app's name in `X-Mst-App` for a valid key, else 401. Used by Caddy. |

Errors use OpenAI's shape. `404 model_not_found` means the model isn't registered: run `mxc pull <name>`. `503 model_unavailable`
means it failed to start, and the message includes the end of its log.
The interactive reference is at `http://127.0.0.1:11435/docs`. The console and the CLI also use `/admin/*` routes,
which only answer on this machine.
