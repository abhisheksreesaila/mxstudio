"""`mxc port`: set up a workspace for bringing an architecture MAX does not support into MAX.

Modular publishes agent skills (github.com/modular/skills) for this; we keep a clone in
MXC_HOME/skills/modular, make a workspace with the model's config and a PORT.md naming the
closest supported "donor" architecture, and print the command that starts Claude Code there.
Ported in simplified form from notebooks/mxstudio/architectures.py.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shlex
import shutil
import sys
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from mxconsole import resolve
from mxconsole.store import Model, Store, mxc_home

SKILLS_REPO = "https://github.com/modular/skills"
ENTRY_SKILL = "import-model"
FALLBACK_DONOR = "LlamaForCausalLM"
KEY_FIELDS = ("model_type", "architectures", "hidden_size", "num_hidden_layers", "num_attention_heads",
              "num_key_value_heads", "intermediate_size", "vocab_size", "max_position_embeddings",
              "rope_theta", "rope_scaling", "rms_norm_eps", "hidden_act", "tie_word_embeddings",
              "torch_dtype", "sliding_window", "num_experts", "num_local_experts")

Runner = Callable[..., "tuple[int, str]"]


class PortError(Exception):
    pass


def _run(cmd: list[str], cwd: Path | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)
    return p.returncode, (p.stdout + p.stderr).strip()


def _download_config(repo_id: str, revision: str) -> Path:
    from huggingface_hub import hf_hub_download
    return Path(hf_hub_download(repo_id, "config.json", revision=revision,
                                token=os.environ.get("HF_TOKEN") or None))


# ---- skills --------------------------------------------------------------

def list_skills(root: Path) -> list[str]:
    return sorted(d.name for d in root.iterdir() if (d / "SKILL.md").is_file()) if root.is_dir() else []


def ensure_skills(*, runner: Runner | None = None) -> Path:
    """Clone Modular's skills into MXC_HOME/skills/modular, or fast-forward an existing clone."""
    runner = runner or _run
    dest = mxc_home() / "skills" / "modular"
    if (dest / ".git").exists():
        code, out = runner(["git", "pull", "--ff-only"], cwd=dest)
        if code != 0 and not list_skills(dest):
            raise PortError(f"could not update Modular's skills in {dest}: {out}")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    code, out = runner(["git", "clone", "--depth", "1", SKILLS_REPO, str(dest)])
    if code != 0:
        raise PortError(f"could not install Modular's skills from {SKILLS_REPO}: {out}")
    return dest


# ---- donor ---------------------------------------------------------------

@dataclass(frozen=True)
class Donor:
    architecture: str
    source_dir: Path | None
    reason: str
    v3_dir: Path | None = None

    @property
    def slug(self) -> str | None:
        return self.source_dir.name if self.source_dir else None

    @property
    def v3_slug(self) -> str | None:
        return self.v3_dir.name if self.v3_dir else None


def max_architectures_dir() -> Path | None:
    spec = importlib.util.find_spec("max")
    for base in (spec.submodule_search_locations or []) if spec else []:
        d = Path(base) / "pipelines" / "architectures"
        if d.is_dir():
            return d
    return None


def _norm(name: str) -> str:
    name = re.sub(r"For[A-Z]\w*$", "", name)
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _common_prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def _source_dir(arch: str, arch_dir: Path | None) -> Path | None:
    if not arch_dir or not arch_dir.is_dir():
        return None
    want = _norm(arch)
    dirs = [d for d in arch_dir.iterdir() if d.is_dir() and not d.name.endswith("_modulev3")
            and not d.name.startswith("__")]
    hits = [d for d in dirs if _norm(d.name).startswith(want)]
    return min(hits, key=lambda d: len(d.name)) if hits else None


def _v3_twin(src: Path | None) -> Path | None:
    twin = src.with_name(src.name + "_modulev3") if src else None
    return twin if twin and twin.is_dir() else None


def pick_donor(arch: str | None, model_type: str | None, catalog: dict, *,
               arch_dir: Path | None = None) -> Donor:
    """Closest supported architecture: shared name prefix with the target (and its model_type),
    a full-name prefix counting extra (same family, newer version), shorter names winning ties."""
    targets = [t for t in (_norm(arch or ""), _norm(model_type or "")) if t]
    best, best_score = None, 0
    for cand in catalog:
        c = _norm(cand)
        score = 0
        for t in targets:
            shared = _common_prefix(t, c)
            if shared < 3:
                continue
            score = max(score, shared + (10 if t.startswith(c) else 0))
        if score > best_score or (score == best_score and best and score and len(c) < len(_norm(best))):
            best, best_score = cand, score
    if best is None:
        fb = FALLBACK_DONOR if FALLBACK_DONOR in catalog or not catalog else sorted(catalog)[0]
        src = _source_dir(fb, arch_dir)
        return Donor(fb, src,
                     f"fallback: nothing in MAX's catalog shares a name with {arch or model_type}; "
                     f"{fb} is the most common decoder-only starting point",
                     _v3_twin(src))
    src = _source_dir(best, arch_dir)
    return Donor(best, src,
                 f"its name ({_norm(best)}) matches the start of {arch or model_type} "
                 f"({', '.join(targets)}), so it is the same family at a nearby version",
                 _v3_twin(src))


# ---- prepare -------------------------------------------------------------

@dataclass(frozen=True)
class PortResult:
    resolution: resolve.Resolution
    workspace: Path | None = None
    command: str | None = None
    skills: list[str] | None = None
    donor: Donor | None = None
    message: str = ""


def _workspace_name(ref: str, repo_id: str) -> str:
    """The model's name, never all digits: job folders are ports/<id>, and the agent may write its workspace."""
    name = resolve.model_name(ref, repo_id)
    return f"model-{name}" if name.isdigit() else name


def _link_skills(workspace: Path, skills_root: Path, names: list[str]) -> None:
    target = workspace / ".claude" / "skills"
    target.mkdir(parents=True, exist_ok=True)
    for n in names:
        link = target / n
        if link.is_symlink() or link.exists():
            link.unlink() if link.is_symlink() else shutil.rmtree(link)
        link.symlink_to(skills_root / n, target_is_directory=True)


def mst_max_version() -> str | None:
    """The MAX that mxconsole serves with, read from conda-meta (importlib.metadata can't see it here)."""
    for f in sorted(Path(sys.prefix, "conda-meta").glob("max-[0-9]*.json")):
        try:
            meta = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if meta.get("name") == "max" and meta.get("version"):
            return meta["version"]
    return None


def skill_max_range(skills_root: Path) -> str | None:
    try:
        m = re.search(r'^max\s*=\s*"([^"]+)"', (skills_root / ENTRY_SKILL / "pixi.toml").read_text(), re.M)
    except OSError:
        return None
    return m.group(1) if m else None


def scaffold_args(donor: Donor) -> tuple[str, str, str]:
    """(start-from slug, extra flags, lane) that scaffold.py accepts for this donor.

    scaffold.py introspects `<donor_dir>/<slug>.py`; a `_modulev3` dir holds `<family>.py`
    instead, so those need --full-copy.
    """
    d, lane = (donor.v3_dir, "ModuleV3") if donor.v3_dir else (donor.source_dir, "V2")
    if d is None:
        return "<slug>", "", lane
    flags = "" if (d / f"{d.name}.py").is_file() else " --full-copy"
    return d.name, flags, lane


def copy_subfolders_step(donor: Donor) -> str:
    """--full-copy skips subfolders, but the donor's modules import `.layers.*` etc.: copy them by hand."""
    d = donor.v3_dir or donor.source_dir
    subs = sorted(s.name for s in d.iterdir() if s.is_dir() and s.name != "__pycache__") if d else []
    if not subs:
        return ""
    cmds = "\n".join(f"cp -r {shlex.quote(str(d / s))} <output_dir>/<slug>/" for s in subs)
    return (f"\n`--full-copy` copies only the donor's files, not its subfolders, yet the donor's code "
            f"imports from them. Copy them into the port dir (`<slug>` is the folder scaffold prints):\n\n"
            f"```\n{cmds}\n```\n")


def _port_md(res: resolve.Resolution, config: dict, donor: Donor, *, skills_root: Path,
             workspace: Path) -> str:
    fields = "\n".join(f"- `{k}`: `{json.dumps(config[k])}`" for k in KEY_FIELDS if k in config)
    skill = skills_root / ENTRY_SKILL
    v2 = f"`{donor.slug}` at `{donor.source_dir}`" if donor.source_dir else "none found"
    v3 = (f"`{donor.v3_slug}` at `{donor.v3_dir}`" if donor.v3_dir else
          "no `_modulev3` twin; check whether a natively-V3 architecture fits (the skill names `olmo3`)")
    start, flags, lane = scaffold_args(donor)
    lane_note = ("" if donor.v3_dir else
                 "\nNo ModuleV3 donor was found, so this scaffold line is on the **V2 lane**. "
                 "Switch to a natively-V3 donor (for example `olmo3`) if you want ModuleV3.\n")
    if flags:
        lane_note += copy_subfolders_step(donor)
    ours, theirs = mst_max_version() or "unknown", skill_max_range(skills_root) or "unknown"
    return f"""# Port {res.repo_id} to MAX

- Target architecture: `{res.architecture}` (config `model_type`: `{config.get('model_type', '?')}`)
- Hugging Face: https://huggingface.co/{res.repo_id} at revision `{res.revision}`
- The full config is in `config.json` next to this file.
- Source of truth: the `{ENTRY_SKILL}` skill (`{skill}/SKILL.md`). Follow it over this file.

## Key config fields

{fields}

## 1. Choose the lane first

MAX has two model APIs, and the donor must be on the same lane as the port.
- **ModuleV3** (default for single-GPU ports): donor {v3}
- **V2** (only if the model needs distributed machinery ModuleV3 lacks): donor {v2}

Closest supported architecture in MAX's catalog: `{donor.architecture}`. Why: {donor.reason}.

## 2. Scaffold from the donor

From the skill root (`pixi install` there once):

```
cd {shlex.quote(str(skill))}
pixi run python scripts/scaffold.py {res.repo_id} --start-from {start} --output-dir <output_dir>{flags}
```
{lane_note}
The port dir is `<output_dir>/<slug>` (the slug folder holding `arch.py` and an `__init__.py`
that exposes `ARCHITECTURES`). That folder is what `--custom-architectures` takes and what
`mxc pull --port` registers.

## 3. The loop

1. Inspect: read `config.json` and the HF modeling code; list what differs from the donor.
2. Adapt from donor: edit the scaffolded files one layer at a time.
3. Load weights: map checkpoint tensor names onto your graph; loading must be strict.
4. Compile: fix shape and dtype errors.
5. Generate: from the skill root, `pixi run max serve --model-path {res.repo_id} --custom-architectures <port dir>`
   (cold compile takes 5 to 25 minutes), then send a prompt.
6. Compare with the HF reference: compare logits and layer outputs against `transformers`.
   If output is wrong, use the `debug-model` skill.
7. Repeat from 2 until greedy tokens match.

## MAX versions

The skill's pixi env uses `max {theirs}`. `mxc serve` runs the port with the MAX installed for mxconsole
(`{ours}`). A port that works in one can break in the other; re-test after `mxc pull --port`.

## Finished?

`mxc pull {res.repo_id} --port <port dir>` (add `--name <name>` to pick the registered name).
Workspace: `{workspace}`
"""


def prepare(ref: str, *, catalog: dict | None = None, hf=None, runner: Runner | None = None,
            fetch_config: Callable[[str, str], Path] | None = None,
            arch_dir: Path | None = None) -> PortResult:
    hf = hf or resolve.HubClient()
    res = resolve.resolve(ref, catalog=catalog, hf=hf)
    if res.supported:
        return PortResult(res, message=f"{res.repo_id} ({res.architecture}) is already supported by MAX. "
                                       f"Next step: mxc pull {ref}")
    if res.kind != "unsupported":
        raise PortError(res.message)
    catalog = catalog if catalog is not None else resolve.load_catalog()
    skills_root = ensure_skills(runner=runner)
    skills = list_skills(skills_root)
    if ENTRY_SKILL not in skills:
        raise PortError(f"Modular's skills in {skills_root} have no {ENTRY_SKILL} skill")
    workspace, config, donor = make_workspace(ref, res, catalog, fetch_config=fetch_config, arch_dir=arch_dir)
    finish_workspace(ref, res, workspace, config, donor, skills_root)
    command = f'cd {shlex.quote(str(workspace))} && claude {shlex.quote(f"/{ENTRY_SKILL} {res.repo_id}")}'
    return PortResult(res, workspace, command, skills, donor, message=res.message)


def make_workspace(ref: str, res: resolve.Resolution, catalog: dict, *,
                   fetch_config: Callable[[str, str], Path] | None = None,
                   arch_dir: Path | None = None) -> tuple[Path, dict, Donor]:
    """The workspace folder with the model's config.json, and the donor chosen for it."""
    workspace = mxc_home() / "ports" / _workspace_name(ref, res.repo_id)
    workspace.mkdir(parents=True, exist_ok=True)
    cfg_path = (fetch_config or _download_config)(res.repo_id, res.revision)
    shutil.copyfile(cfg_path, workspace / "config.json")
    config = json.loads((workspace / "config.json").read_text())
    donor = pick_donor(res.architecture, config.get("model_type"), catalog,
                       arch_dir=arch_dir or max_architectures_dir())
    return workspace, config, donor


def finish_workspace(ref: str, res: resolve.Resolution, workspace: Path, config: dict, donor: Donor,
                     skills_root: Path) -> list[str]:
    """PORT.md and the skill links, once the skills are on disk. Returns the skill names."""
    skills = list_skills(skills_root)
    (workspace / "PORT.md").write_text(
        _port_md(res, config, donor, skills_root=skills_root, workspace=workspace))
    _link_skills(workspace, skills_root, skills)
    return skills


# ---- register ------------------------------------------------------------

def check_port_dir(path: Path) -> None:
    if not path.is_dir():
        raise PortError(f"{path} is not a directory")
    if ":" in str(path) or not path.name.isidentifier():
        raise PortError(f"{path}: MAX imports the port dir by its folder name, so the name must be a "
                        f"valid Python identifier and the path must not contain ':'")
    missing = [f for f in ("arch.py", "__init__.py") if not (path / f).is_file()]
    if missing:
        raise PortError(f"{path} is missing {', '.join(missing)}; the {ENTRY_SKILL} skill produces "
                        f"arch.py and an __init__.py that exposes ARCHITECTURES")
    if "ARCHITECTURES" not in (path / "__init__.py").read_text():
        raise PortError(f"{path}/__init__.py does not define ARCHITECTURES")


def register(ref: str, port_dir: Path, *, store: Store | None = None, catalog: dict | None = None,
             hf=None, name: str | None = None) -> Model:
    """Register a finished port: weights come from the pinned HF snapshot, the architecture from port_dir.

    Raises PortError for a bad port dir or weights; resolve.PullError / ResolveError as `pull` does.
    """
    from mxconsole.pulls import NAME_RE  # the same rule `mxc pull --name` enforces
    if name is not None and not NAME_RE.fullmatch(name):
        raise PortError(f"the name {name!r} must be 1 to 64 letters, digits, '.', '_' or '-', starting with a letter or digit")
    port_dir = Path(port_dir).expanduser().resolve()
    check_port_dir(port_dir)
    hf = hf or resolve.HubClient()
    res = resolve.resolve(ref, catalog=catalog, hf=hf)
    if not (res.supported or res.kind == "unsupported"):
        raise resolve.PullError(res)
    store = store or Store.open()
    name = name or resolve.model_name(ref, res.repo_id)
    resolve.check_name_free(store, name, ref, res)
    try:
        weights = resolve.download_snapshot(res, hf)
    except ValueError as e:
        raise PortError(f"{res.repo_id}: {e}; MAX needs safetensors") from e
    return store.add_model(name=name, repo_id=res.repo_id,
                           revision=res.revision, source="port", architecture=res.architecture or "unknown",
                           weights_path=str(weights), custom_arch_path=str(port_dir), kind=res.model_kind)
