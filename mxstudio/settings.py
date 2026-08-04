"""User settings persisted under ~/.mxstudio (HF token, etc.)."""

from __future__ import annotations

import os
import stat
import threading
from pathlib import Path

from mxstudio import state

SECRETS_FILE = state.HOME / "secrets.json"
_lock = threading.Lock()


def _ensure_home() -> None:
    state.HOME.mkdir(parents=True, exist_ok=True)


def _load_secrets() -> dict:
    _ensure_home()
    try:
        import json

        return json.loads(SECRETS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return {}


def _save_secrets(data: dict) -> None:
    _ensure_home()
    import json

    tmp = SECRETS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(SECRETS_FILE)
    try:
        os.chmod(SECRETS_FILE, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def get_hf_token() -> str:
    """Resolve HF token: process env wins, then saved secrets."""
    env = (
        os.environ.get("HF_TOKEN")
        or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        or ""
    ).strip()
    if env:
        return env
    with _lock:
        return str(_load_secrets().get("hf_token") or "").strip()


def apply_token_to_env(token: str | None = None) -> None:
    """Put the active token into the process env so huggingface_hub sees it."""
    tok = (token if token is not None else get_hf_token()).strip()
    if tok:
        os.environ["HF_TOKEN"] = tok
        os.environ["HUGGING_FACE_HUB_TOKEN"] = tok
    else:
        os.environ.pop("HF_TOKEN", None)
        os.environ.pop("HUGGING_FACE_HUB_TOKEN", None)


def set_hf_token(token: str) -> dict:
    """Save token to ~/.mxstudio/secrets.json and update process env."""
    token = (token or "").strip()
    with _lock:
        data = _load_secrets()
        if token:
            data["hf_token"] = token
        else:
            data.pop("hf_token", None)
        _save_secrets(data)
    apply_token_to_env(token)
    return public_settings()


def clear_hf_token() -> dict:
    return set_hf_token("")


def _mask(token: str) -> str:
    if not token:
        return ""
    if len(token) <= 8:
        return "••••" + token[-2:]
    return token[:4] + "…" + token[-4:]


def public_settings() -> dict:
    """Safe view for the UI — never returns the raw token."""
    token = get_hf_token()
    env_set = bool(
        (
            os.environ.get("HF_TOKEN")
            or os.environ.get("HUGGING_FACE_HUB_TOKEN")
            or ""
        ).strip()
    )
    file_set = False
    with _lock:
        file_set = bool(_load_secrets().get("hf_token"))
    return {
        "hf_token_set": bool(token),
        "hf_token_masked": _mask(token) if token else "",
        "hf_token_source": (
            "environment"
            if env_set and not file_set
            else ("file" if file_set else ("environment" if env_set else "none"))
        ),
        "secrets_path": str(SECRETS_FILE),
        "notes": [
            "Token is stored only in ~/.mxstudio/secrets.json (mode 600) or your shell env.",
            "Environment variables override the saved file when both are set at process start.",
            "Gated models need a token with access accepted on huggingface.co.",
        ],
    }


# Apply on import so downloads/checks see a previously saved token.
apply_token_to_env()
