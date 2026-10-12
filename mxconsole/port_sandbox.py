"""Confinement for the automatic port: the model's config.json and model card are untrusted input, so
the agent that reads them, and the code it writes, run in a bubblewrap sandbox with a scrubbed environment.

`bwrap_argv` is a pure function (tests assert its argv). The layout, inside the sandbox:
  /            read-only          (so the toolchain under /usr, /etc, pixi and claude installs just works)
  $HOME        empty tmpfs        (no ~/.ssh, ~/.aws, ~/.config ...), then only what is listed is bound back
  MXC_HOME     empty tmpfs        (no mst.db, no API keys, no other models), then the job dirs are bound back
  /tmp         private tmpfs;  /run/user hidden (agent sockets);  new pid/ipc/uts namespaces
  writable     only the paths in `rw` (the workspace, the job dir, the toolchain caches)
  network      kept: claude needs the API, the skill's pixi env needs conda and Hugging Face
"""
from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|COOKIE)", re.I)
ALWAYS_DROP = ("SSH_AUTH_SOCK", "SSH_AGENT_PID", "DBUS_SESSION_BUS_ADDRESS", "GPG_AGENT_INFO", "MXC_API_KEY",
               "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACE_HUB_TOKEN")
# What the claude process itself may keep to authenticate (the OAuth login is copied into its private
# config dir instead; these cover API-key and cloud-provider setups).
# The daemon itself runs under `pixi run`: its activation points at mxstudio's pixi.toml and env, which the
# sandbox can't see, so a `pixi run` inside would fail to open that manifest. The caches stay (set per job).
OUTER_ACTIVATION = re.compile(r"(PIXI_PROJECT_|PIXI_ENVIRONMENT_|PIXI_IN_SHELL$|PIXI_PROMPT$|CONDA_)")
CLAUDE_AUTH = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_BASE_URL",
               "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE", "AWS_REGION",
               "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "GOOGLE_APPLICATION_CREDENTIALS")


def scrub_env(env: dict[str, str], keep: tuple[str, ...] = ()) -> dict[str, str]:
    """A copy of env without secrets: anything named like a key/token/secret and the known agent sockets,
    except the names in `keep`."""
    return {k: v for k, v in env.items()
            if k in keep or not (k in ALWAYS_DROP or SECRET_NAME.search(k) or OUTER_ACTIVATION.match(k))}


def have_bwrap(path: str = "bwrap") -> str | None:
    return shutil.which(path) if sys.platform.startswith("linux") else None


def tool_roots(*exes: str) -> list[Path]:
    """Install prefixes of the given executables (the parent of their bin dir, resolved and not), so a
    toolchain that lives under $HOME (mise, pixi, a pixi env) can be bound back read-only."""
    out: list[Path] = []
    for exe in exes:
        found = shutil.which(exe) if exe else None
        if not found:
            continue
        for p in (Path(found), Path(found).resolve()):
            root = p.parent.parent if p.parent.name == "bin" else p.parent
            if root not in out and root != Path("/"):
                out.append(root)
    return out


def bwrap_argv(cmd: list[str], *, bwrap: str = "bwrap", cwd: Path, home: Path, mxc_home: Path,
               rw: list[Path] = (), ro: list[Path] = (), ro_after: list[Path] = (), binds: list[tuple[Path, Path]] = (),
               ro_binds: list[tuple[Path, Path]] = (), gpu: bool = False) -> list[str]:
    """`cmd` wrapped to run confined. Paths in `rw`/`ro` are bound at the same place when they exist
    (ro-bind-try / bind-try); order matters: tmpfs first, then the binds on top of it, and `ro_after` last
    (read-only files inside a writable directory)."""
    argv = [bwrap, "--die-with-parent", "--new-session", "--unshare-pid", "--unshare-ipc", "--unshare-uts",
            "--ro-bind", "/", "/"]
    argv += ["--dev-bind", "/dev", "/dev"] if gpu else ["--dev", "/dev"]
    argv += ["--proc", "/proc", "--tmpfs", "/tmp", "--tmpfs", "/run/user",
             "--tmpfs", str(home), "--tmpfs", str(mxc_home)]
    for p in ro:
        argv += ["--ro-bind-try", str(p), str(p)]
    for p in rw:
        argv += ["--bind-try", str(p), str(p)]
    for src, dst in binds:  # a file or dir exposed read-write at another path (the Claude login)
        argv += ["--bind-try", str(src), str(dst)]
    for src, dst in ro_binds:  # a dir exposed read-only at another path (the model's weights in a private HF cache)
        argv += ["--ro-bind-try", str(src), str(dst)]
    for p in ro_after:
        argv += ["--ro-bind-try", str(p), str(p)]
    return argv + ["--chdir", str(cwd), "--", *cmd]
