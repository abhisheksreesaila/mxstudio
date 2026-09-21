"""Validate the deliberately simple Nucleus EnvironmentFile without evaluating it."""
from __future__ import annotations

import argparse
import re
import stat
import time
from pathlib import Path
from urllib import error, request


def read_environment(path: Path) -> dict[str, str]:
    if stat.S_IMODE(path.stat().st_mode) & 0o007:
        raise ValueError("environment file must not be accessible to other users")
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError("expected one KEY=value assignment per line")
        value = value.strip()
        if value.startswith(('"', "'")) and value.endswith(value[0]):
            value = value[1:-1]
        if any(char.isspace() or char in "\\\"'" for char in value):
            raise ValueError("use single-line values without whitespace or escapes; URL-encode database passwords")
        if key in values:
            raise ValueError(f"duplicate environment setting: {key}")
        values[key] = value
    return values


def validate(values: dict[str, str], gateway: bool = False) -> None:
    required = ["MXSTUDIO_NUCLEUS_API_KEY", "NUCLEUS_MODEL_ID"]
    if gateway:
        required += ["DATABASE_URL", "LITELLM_MASTER_KEY"]
    for key in required:
        if not values.get(key):
            raise ValueError(f"missing populated {key}")
    if gateway and not values["DATABASE_URL"].startswith(("postgresql://", "postgres://")):
        raise ValueError("DATABASE_URL must refer to explicitly provisioned PostgreSQL")


def probe(values: dict[str, str], gateway: bool = False) -> None:
    port, key = (4000, values["LITELLM_MASTER_KEY"]) if gateway else (8080, values["MXSTUDIO_NUCLEUS_API_KEY"])
    req = request.Request(f"http://127.0.0.1:{port}/v1/models", headers={"Authorization": "Bearer " + key})
    opener = request.build_opener(request.ProxyHandler({}))
    for _ in range(30):
        try:
            with opener.open(req, timeout=2) as response:
                if response.status == 200:
                    return
        except (error.URLError, TimeoutError, OSError):
            pass
        time.sleep(1)
    raise RuntimeError("inference API did not become responsive; inspect systemd status locally")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("environment", type=Path)
    parser.add_argument("--gateway", action="store_true")
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()
    try:
        values = read_environment(args.environment)
        validate(values, args.gateway)
        if args.probe:
            probe(values, args.gateway)
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(1, f"Nucleus preflight: {exc}\n")
