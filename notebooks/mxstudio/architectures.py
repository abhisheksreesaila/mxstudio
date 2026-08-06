"""Custom architecture bring-up wizard for Modular MAX.

Honest scope
------------
mxstudio guides a custom architecture through inspection, donor comparison,
implementation, strict loading, and compilation. Package preparation and local
preflight are helpers, not proof that the model is correct. Numerical parity
still requires reference evidence.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
import json
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

from notebooks.mxstudio import registry
from notebooks.mxstudio import compat

REPO_ROOT = Path(__file__).resolve().parents[2]
PORTS_DIR = REPO_ROOT / "ports"
PLANS_DIR = REPO_ROOT / "results" / "bringup"
SKILLS_CACHE_DIR = REPO_ROOT / ".mxstudio" / "skills"
MODULAR_SKILLS_REPO = "https://github.com/modular/skills.git"

DOCS = {
    "custom_arch": "https://docs.modular.com/develop/serve-custom-model-architectures/",
    "bring_up": "https://docs.modular.com/develop/model-bringup-workflow/",
    "bring_up_skills": "https://docs.modular.com/develop/model-bringup-skills/",
    "logit_comparison": "https://docs.modular.com/develop/logit-comparison/",
    "layer_comparison": "https://docs.modular.com/develop/layer-comparison/",
    "handbook": "https://handbook.modular.com/",
    "models": "https://docs.modular.com/max/models/",
}

WIZARD_STEPS = [
    {
        "id": "inspect",
        "title": "Inspect",
        "blurb": "Read the checkpoint configuration, architecture, weights metadata, hardware fit, and known blockers.",
    },
    {
        "id": "compare",
        "title": "Compare donor",
        "blurb": "Choose the closest MAX architecture and list every semantic delta from the Hugging Face implementation.",
    },
    {
        "id": "plan",
        "title": "Approve plan",
        "blurb": "Approve concrete classes, graph changes, weight mappings, hardware target, and validation depth before code changes.",
    },
    {
        "id": "implement",
        "title": "Implement",
        "blurb": "Create or preserve the package, then track each approved architecture delta to an implementation artifact.",
    },
    {
        "id": "load",
        "title": "Strict load",
        "blurb": "Prove checkpoint coverage: no unexplained missing, unexpected, mismatched, or orphan graph weights.",
    },
    {
        "id": "compile",
        "title": "Compile",
        "blurb": "Cold-compile and serve the MAX graph, preserving the command, logs, timing, and first real response as evidence.",
    },
]

VALIDATION_GATES = [
    {
        "id": "implementation",
        "stage": "implement",
        "title": "Every donor delta implemented",
        "evidence": "Each approved delta points to the class or graph operation that implements it; local preflight checks pass.",
    },
    {
        "id": "strict_load",
        "stage": "load",
        "title": "Checkpoint coverage is explicit",
        "evidence": "Missing, unexpected, mismatched, and orphan graph keys are all zero or individually explained.",
    },
    {
        "id": "compile",
        "stage": "compile",
        "title": "MAX graph compiles and serves",
        "evidence": "The exact command exits startup successfully, the health endpoint passes, and one real request returns tokens.",
    },
    {
        "id": "parity",
        "stage": "compile",
        "title": "Reference comparison recorded",
        "evidence": "HF is sanity-checked first, then greedy tokens or logits are compared at the validation depth approved in the plan.",
    },
]

LIMITS = {
    "can": [
        "Detect whether MAX already supports the HF architecture name",
        "Inspect HF config, checkpoint metadata, access, size, and hardware fit",
        "Compare a donor and persist an approved semantic delta ledger",
        "Prepare ports/<name>/ without overwriting human implementation work",
        "Run local implementation preflight checks",
        "Record strict-load, compile, and reference-comparison evidence",
        "Link Modular's bring-up, logit-comparison, and debugging guidance",
    ],
    "cannot": [
        "Infer a correct custom graph or weight adapter from model names alone",
        "Treat generated files or import checks as a working model",
        "Mark health-only startup as successful generation or parity",
        "Replace review of architecture semantics and numerical evidence",
    ],
    "framing": (
        "This is an evidence-driven bring-up studio based on Modular's workflow. "
        "It removes mechanical work and makes each guard visible, but only the "
        "recorded load, compile, generation, and parity evidence can close a gate."
    ),
}

# Rough nearest-neighbor hints for humans studying an existing MAX pipeline.
_SIMILAR = (
    (("olmoe", "moe"), "Qwen2Moe / other MoE CausalLM pipelines — study expert routing + shared experts"),
    (("qwen3",), "Qwen3ForCausalLM — already in MAX; use as a dense reference"),
    (("qwen2",), "Qwen2ForCausalLM — dense decoder reference"),
    (("llama", "olmo"), "LlamaForCausalLM / Olmo* — dense decoder reference"),
    (("gemma",), "Gemma2/3ForCausalLM — dense decoder reference"),
    (("phi",), "Phi3ForCausalLM — dense decoder reference"),
    (("deepseek",), "DeepseekV2/V3 — MoE-ish; check MAX list for exact support"),
)

_GAP_STEPS = [
    {
        "id": "config",
        "title": "Model config",
        "detail": "Map Hugging Face config.json fields into a MAX ModelConfig subclass.",
        "file": "model_config.py",
        "owner": "human",
    },
    {
        "id": "model",
        "title": "Pipeline model",
        "detail": "Implement the graph / layers (attention, MoE router, norms) as a PipelineModel.",
        "file": "model.py",
        "owner": "human",
    },
    {
        "id": "arch",
        "title": "SupportedArchitecture registration",
        "detail": "name must match config.json architectures[0] exactly.",
        "file": "arch.py",
        "owner": "scaffold",
    },
    {
        "id": "weights",
        "title": "Weight adapters",
        "detail": "Map safetensors keys → MAX weight names for every encoding you ship.",
        "file": "model.py / weight adapters",
        "owner": "human",
    },
    {
        "id": "package",
        "title": "Package export",
        "detail": "Expose ARCHITECTURES = […] from __init__.py so max serve can import it.",
        "file": "__init__.py",
        "owner": "scaffold",
    },
]


def _slug(model_id: str, architecture: str | None) -> str:
    entry = registry.get(model_id)
    if entry and entry.custom_arch:
        return Path(entry.custom_arch).name
    source = architecture or model_id.split("/")[-1]
    base = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", source).lower()
    base = re.sub(r"[^a-z0-9]+", "_", base).strip("_")
    return (base or "custom_arch")[:48]


def package_dir_for_model(model_id: str) -> Path | None:
    """Resolve an existing scaffold without requiring another HF network call."""
    entry = registry.get(model_id)
    if entry and entry.custom_arch:
        candidate = REPO_ROOT / entry.custom_arch
        if candidate.is_dir():
            return candidate
    if not PORTS_DIR.is_dir():
        return None
    for meta_path in PORTS_DIR.glob("*/mxstudio_meta.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if meta.get("model_id") == model_id:
            return meta_path.parent
    return None


def _bringup_manifest(package_dir: Path) -> dict:
    path = package_dir / "mxstudio_bringup.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _audit_manifest(package_dir: Path, manifest: dict) -> list[dict]:
    checks = []
    for delta in manifest.get("deltas", []):
        relative_file = str(delta.get("file") or "")
        symbol = str(delta.get("symbol") or "")
        target = package_dir / relative_file
        text = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""
        checks.append(
            {
                "id": str(delta.get("id") or relative_file),
                "title": str(delta.get("title") or delta.get("id") or relative_file),
                "file": relative_file,
                "symbol": symbol,
                "ok": bool(text and symbol and symbol in text),
            }
        )
    return checks


def custom_arch_relative(model_id: str) -> str | None:
    package_dir = package_dir_for_model(model_id)
    if package_dir is None:
        return None
    return package_dir.relative_to(REPO_ROOT).as_posix()


def open_package_folder(model_id: str) -> dict:
    from notebooks.mxstudio.pathops import reveal_path

    package_dir = package_dir_for_model(model_id)
    if package_dir is None:
        return {
            "ok": False,
            "opened": False,
            "target": None,
            "target_windows": None,
            "opened_with": None,
            "error": "no scaffold exists for this model; run Scaffold first",
        }
    return reveal_path(package_dir, allowed_roots=(PORTS_DIR,))


def _similar_hint(architecture: str) -> str | None:
    lower = (architecture or "").lower()
    for keys, hint in _SIMILAR:
        if any(k in lower for k in keys):
            return hint
    return "Study the closest dense/MoE CausalLM already in `max list` and Modular docs."


def wizard_meta() -> dict:
    return {
        "steps": WIZARD_STEPS,
        "limits": LIMITS,
        "docs": DOCS,
    }


def _plan_path(model_id: str, architecture: str | None = None) -> Path:
    return PLANS_DIR / f"{_slug(model_id, architecture)}.json"


def _read_plan(model_id: str, architecture: str | None = None) -> dict | None:
    path = _plan_path(model_id, architecture)
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return plan if plan.get("model_id") == model_id else None


def _readiness() -> dict:
    skill_roots = (
        SKILLS_CACHE_DIR / "modular",
        REPO_ROOT / ".agents" / "skills",
        Path.home() / ".agents" / "skills",
        Path.home() / ".copilot" / "skills",
    )
    skills = {}
    for name in ("import-model", "debug-model", "eval-model", "profile-model"):
        found = next(
            (root / name / "SKILL.md" for root in skill_roots if (root / name / "SKILL.md").is_file()),
            None,
        )
        skills[name] = {
            "ready": found is not None,
            "path": str(found) if found else None,
        }
    return {
        "pixi": {"ready": shutil.which("pixi") is not None, "command": "pixi run"},
        "max": {"ready": shutil.which("max") is not None, "command": "max"},
        "skills": skills,
    }


def _import_model_skill_dir() -> Path | None:
    readiness = _readiness()["skills"]["import-model"]
    path = readiness.get("path")
    return Path(path).parent if path else None


def ensure_import_model_skill() -> Path:
    """Return the official import-model skill, cloning it into local cache once."""
    existing = _import_model_skill_dir()
    if existing is not None:
        return existing
    destination = SKILLS_CACHE_DIR / "modular"
    destination.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        ["git", "clone", "--depth", "1", MODULAR_SKILLS_REPO, str(destination)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "Could not install Modular's import-model skill: "
            + (completed.stderr.strip() or completed.stdout.strip())
        )
    skill_dir = destination / "import-model"
    if not (skill_dir / "scripts" / "import_model.py").is_file():
        raise RuntimeError("Installed Modular skills repository has no import-model dispatcher")
    return skill_dir


def run_skill_stage(
    model_id: str,
    stage: str,
    *,
    progress: Callable[[str, float | None], None] | None = None,
) -> dict:
    """Run an evidence-producing import-model skill stage and persist its output."""
    analysis = analyze(model_id)
    if not analysis.get("ok"):
        return analysis
    skill_dir = ensure_import_model_skill()
    dispatcher = skill_dir / "scripts" / "import_model.py"
    artifact_dir = PLANS_DIR / "artifacts" / analysis["package_name"]
    artifact_dir.mkdir(parents=True, exist_ok=True)
    commands: dict[str, list[str]] = {
        "inspect": ["inspect", model_id],
        "implement": [
            "gates",
            model_id,
            "--port-dir",
            str(PORTS_DIR / analysis["package_name"]),
            "--json-out",
            str(artifact_dir / "skill_preflight.json"),
        ],
    }
    if stage not in commands:
        return {"ok": False, "error": f"unsupported automated stage: {stage}"}
    command = [sys.executable, str(dispatcher), *commands[stage]]
    if progress:
        progress(f"Running Modular import-model {stage}…", 35)
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    manifest = _bringup_manifest(PORTS_DIR / analysis["package_name"])
    manifest_checks = (
        _audit_manifest(PORTS_DIR / analysis["package_name"], manifest)
        if stage == "implement"
        else []
    )
    stage_ok = completed.returncode == 0 and all(check["ok"] for check in manifest_checks)
    artifact_path = artifact_dir / f"{stage}.json"
    artifact = {
        "tool": "modular/skills/import-model",
        "skill_path": str(skill_dir.relative_to(REPO_ROOT)),
        "stage": stage,
        "command": command,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "manifest": analysis.get("bringup_manifest"),
        "implementation_checks": manifest_checks,
        "created_at": datetime.now(UTC).isoformat(),
    }
    artifact_path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    if stage == "implement" and analysis.get("plan", {}).get("approved"):
        update_validation_gate(
            model_id,
            analysis["architecture"],
            "implementation",
            "passed" if stage_ok and manifest_checks else "failed",
            [
                str(artifact_path.relative_to(REPO_ROOT)),
                *[
                    f"{check['file']}::{check['symbol']}"
                    for check in manifest_checks
                    if check["ok"]
                ],
            ],
            "Official import-model preflight and concrete manifest targets were checked.",
            source="modular_import_model+mxstudio_manifest",
        )
    if progress:
        progress("Skill evidence recorded", 100)
    return {
        "ok": stage_ok,
        "tool": "modular/skills/import-model",
        "stage": stage,
        "artifact": str(artifact_path.relative_to(REPO_ROOT)),
        "command": shlex.join(command),
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "implementation_checks": manifest_checks,
        "analysis": analyze(model_id),
    }


def _persist_plan(analysis: dict) -> dict:
    previous = _read_plan(analysis["model_id"], analysis.get("architecture")) or {}
    now = datetime.now(UTC).isoformat()
    existing_gates = {gate.get("id"): gate for gate in previous.get("validation_gates", [])}
    validation_gates = []
    for gate in VALIDATION_GATES:
        existing = existing_gates.get(gate["id"], {})
        status = existing.get("status", "pending")
        source = existing.get("source")
        if status == "passed" and gate["id"] in {"implementation", "strict_load"} and not source:
            status = "blocked"
        validation_gates.append({
            **gate,
            "status": status,
            "artifacts": existing.get("artifacts", []),
            "notes": (
                "Legacy manual evidence is retained for review but does not pass this automated gate."
                if status == "blocked" and existing.get("status") == "passed" and not source
                else existing.get("notes", "")
            ),
            "source": source,
        })
    plan = {
        "schema_version": 2,
        "model_id": analysis["model_id"],
        "architecture": analysis["architecture"],
        "package_name": analysis["package_name"],
        "status": "approved" if previous.get("approved") else "draft",
        "approved": bool(previous.get("approved")),
        "approved_at": previous.get("approved_at"),
        "created_at": previous.get("created_at") or now,
        "updated_at": now,
        "donor": analysis.get("donor") or analysis.get("similar_hint"),
        "deltas": analysis.get("semantic_deltas") or [
            gap for gap in analysis.get("gaps", []) if gap.get("owner") == "human"
        ],
        "readiness": _readiness(),
        "validation_gates": validation_gates,
        "validation_depth": previous.get("validation_depth", "greedy_tokens"),
        "boundaries": LIMITS["cannot"],
    }
    path = _plan_path(analysis["model_id"], analysis.get("architecture"))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    temporary.replace(path)
    return {**plan, "path": str(path.relative_to(REPO_ROOT))}


def approve_plan(model_id: str) -> dict:
    analysis = analyze(model_id)
    if not analysis.get("ok"):
        return analysis
    plan = analysis["plan"]
    if analysis.get("modular_compatible"):
        return {"ok": False, "error": "MAX already supports this architecture; no custom plan is needed."}
    plan.update(
        {
            "approved": True,
            "status": "approved",
            "approved_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        }
    )
    path = _plan_path(model_id, analysis.get("architecture"))
    stored = {key: value for key, value in plan.items() if key != "path"}
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(stored, indent=2), encoding="utf-8")
    temporary.replace(path)
    return {"ok": True, "plan": plan, "message": "Plan approved. Scaffold is now unlocked."}


def update_validation_gate(
    model_id: str,
    architecture: str,
    gate_id: str,
    status: str,
    artifacts: list[str] | None = None,
    notes: str = "",
    source: str | None = None,
) -> dict:
    if status not in {"pending", "running", "passed", "failed", "blocked"}:
        return {"ok": False, "error": f"unknown validation status: {status}"}
    if gate_id not in {gate["id"] for gate in VALIDATION_GATES}:
        return {"ok": False, "error": f"unknown validation gate: {gate_id}"}
    plan = _read_plan(model_id, architecture)
    if plan is None:
        return {"ok": False, "error": "run Inspect before recording validation evidence"}
    if not plan.get("approved"):
        return {"ok": False, "error": "approve the implementation plan before recording validation evidence"}

    gate = next(
        (item for item in plan.get("validation_gates", []) if item.get("id") == gate_id),
        None,
    )
    if gate is None:
        return {"ok": False, "error": "re-run Inspect to create the current validation gates"}
    gate.update(
        {
            "status": status,
            "artifacts": [str(item)[:1000] for item in (artifacts or [])],
            "notes": notes.strip()[:4000],
            "source": source,
            "updated_at": datetime.now(UTC).isoformat(),
        }
    )
    plan["updated_at"] = gate["updated_at"]
    path = _plan_path(model_id, architecture)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    temporary.replace(path)
    return {
        "ok": True,
        "plan": {**plan, "path": str(path.relative_to(REPO_ROOT))},
        "gate": gate,
    }


def analyze(model_id: str) -> dict:
    """Check HF + MAX support and describe the gap for a custom port."""
    report = compat.check_hf_model(model_id)
    if not report.get("ok"):
        return {
            "ok": False,
            "model_id": model_id,
            "error": report.get("error"),
            "hint": report.get("hint"),
            "report": report,
            "wizard": wizard_meta(),
        }

    supported = bool(report.get("modular_compatible"))
    arch = report.get("architecture") or "UnknownForCausalLM"
    package = _slug(model_id, arch)
    package_dir = PORTS_DIR / package
    exists = package_dir.is_dir() and (package_dir / "__init__.py").exists()
    manifest = _bringup_manifest(package_dir) if exists else {}

    gaps = []
    for step in _GAP_STEPS:
        file_token = step["file"].split()[0]
        file_exists = exists and (package_dir / file_token).exists()
        if supported:
            status = "builtin"
        elif file_exists and step["owner"] == "scaffold":
            status = "scaffolded"
        elif file_exists:
            status = "stub"  # file present but likely NotImplemented
        else:
            status = "missing"
        gaps.append({**step, "status": status})

    entry = registry.get(model_id)
    parked = bool(entry and not entry.serve_enabled)
    similar = _similar_hint(str(arch))
    if supported:
        summary = (
            f"Architecture `{arch}` is already in Modular MAX — you usually do not "
            "need a custom package. Serve from the Models tab."
        )
        next_action = "serve"
        recommended_step = "handoff"
        why = (
            f"Hugging Face config.json reports architectures[0] = `{arch}`. "
            "That class name is present in MAX's built-in architecture list, so "
            "`max serve` can load the model without a ports/ package."
        )
        guidance = [
            "Open the Models tab and Serve this id (or use the Active dropdown).",
            "Skip Scaffold — a custom package is not required for built-in arches.",
            "Use Benchmark once the server pill shows running.",
        ]
        verdict = "supported"
        verdict_title = "Built into Modular MAX"
    elif exists:
        summary = (
            f"`{arch}` is not in the built-in MAX list, but a starter package exists "
            f"at `ports/{package}`. That is a scaffold, not a finished port."
        )
        next_action = "edit_scaffold"
        recommended_step = "implement"
        why = (
            f"MAX only ships a finite set of architecture classes. This model's "
            f"config asks for `{arch}`, which is not on that built-in list, so the "
            "base runtime cannot load it out of the box. A custom "
            "`SupportedArchitecture` package must teach MAX the graph, weight maps, "
            "and encodings — mxstudio can only scaffold the files."
        )
        guidance = [
            "Open the package in Explorer and read the TODOs in model.py / arch.py.",
            f"Study a nearby built-in arch: {similar}",
            "Smoke check verifies files/syntax/import only — not a working serve.",
            "Prefer a MAX-native model (e.g. Qwen2.5 / Qwen3 MoE) if you need chat today.",
        ]
        verdict = "unsupported_scaffold"
        verdict_title = "Not built into MAX — scaffold present"
    else:
        summary = (
            f"`{arch}` is not built into MAX yet. Scaffold creates starter files under "
            f"`ports/{package}`; finishing the port is human/agent work."
        )
        next_action = "scaffold"
        recommended_step = "scaffold"
        why = (
            f"MAX does not include `{arch}` in its built-in architecture registry. "
            "Support is determined by that class name from Hugging Face config.json, "
            "not by model size alone. Until someone implements a custom MAX "
            "architecture (or Modular adds the arch upstream), `max serve` cannot "
            "run this model natively."
        )
        guidance = [
            "Run Scaffold to create a non-overwriting ports/ starter (stubs + docs).",
            "Then Implement the real graph — weight adapters, KV shapes, MoE routing if any.",
            f"Use as a study reference: {similar}",
            "If you need a working MoE on MAX today, switch to the curated Qwen3 MoE target.",
        ]
        verdict = "unsupported"
        verdict_title = "Not built into MAX"

    if parked:
        why += (
            " This curated entry is also marked parked in mxstudio: Serve stays disabled "
            "until a complete adapter exists"
            + (f" ({entry.serve_disabled_reason})" if entry and entry.serve_disabled_reason else ".")
        )
        guidance = [
            entry.serve_disabled_reason
            if entry and entry.serve_disabled_reason
            else "Serve is disabled for this curated entry.",
            *guidance,
        ]
        verdict_title = "Parked / incomplete custom architecture"
        verdict = "parked"

    explain = {
        "what_inspect_means": (
            "Inspect reads the Hugging Face repo metadata and config.json, extracts the "
            "architecture class name, and checks whether that name is already registered "
            "in Modular MAX. It does not download weights and does not compile a graph."
        ),
        "verdict": verdict,
        "verdict_title": verdict_title,
        "why": why,
        "guidance": guidance,
        "architecture_class": arch,
        "similar_hint": similar,
    }

    result = {
        "ok": True,
        "model_id": report["model_id"],
        "architecture": arch,
        "modular_compatible": supported,
        "approx_gb": report.get("approx_gb"),
        "hardware": report.get("hardware"),
        "summary": summary,
        "explain": explain,
        "next_action": next_action,
        "recommended_step": recommended_step,
        "package_name": package,
        "package_path": str(package_dir.relative_to(REPO_ROOT)),
        "scaffold_exists": exists,
        "parked": parked,
        "serve_enabled": False if parked else True,
        "serve_disabled_reason": (entry.serve_disabled_reason if parked and entry else ""),
        "gaps": gaps,
        "similar_hint": similar,
        "donor": manifest.get("donor") or similar,
        "semantic_deltas": manifest.get("deltas") or [],
        "bringup_manifest": (
            str((package_dir / "mxstudio_bringup.json").relative_to(REPO_ROOT))
            if manifest
            else None
        ),
        "hf_url": f"https://huggingface.co/{report['model_id']}",
        "docs": DOCS,
        "limits": LIMITS,
        "wizard": wizard_meta(),
        "report": report,
        "serve_cmd": (
            f"max serve --model {shlex.quote(report['model_id'])} "
            f"--custom-architectures {shlex.quote(f'ports:{package}')}"
        ),
        "request_cmd": (
            "curl -X POST http://localhost:8000/v1/chat/completions "
            "-H 'Content-Type: application/json' "
            f"-d {shlex.quote(json.dumps({'model': report['model_id'], 'messages': [{'role': 'user', 'content': 'Hello!'}], 'max_completion_tokens': 100}))}"
        ),
    }
    result["plan"] = _persist_plan(result)
    return result


def scaffold(model_id: str) -> dict:
    """Write a starter custom-architecture package under ports/."""
    analysis = analyze(model_id)
    if not analysis.get("ok"):
        return analysis
    if analysis.get("modular_compatible"):
        return {
            **analysis,
            "scaffolded": False,
            "message": "Model appears Modular-compatible — scaffold not required.",
        }
    if not analysis.get("plan", {}).get("approved"):
        return {
            **analysis,
            "ok": False,
            "error": "Review and approve the bring-up plan before creating files.",
        }

    package = analysis["package_name"]
    arch_name = analysis["architecture"]
    out = PORTS_DIR / package
    out.mkdir(parents=True, exist_ok=True)

    init_py = f'''"""Custom MAX architecture package for {analysis["model_id"]}.

Generated by mxstudio Architectures wizard. Fill in TODOs, then:
  {analysis["serve_cmd"]}

Docs: {DOCS["custom_arch"]}
"""
from .arch import custom_arch

ARCHITECTURES = [custom_arch]
'''

    arch_py = f'''"""SupportedArchitecture registration for {arch_name}."""
from max.graph.weights import WeightsFormat
from max.pipelines.context import TextContext
from max.pipelines.lib import SupportedArchitecture, TextTokenizer
from max.pipelines.modeling.types import PipelineTask

from .model import CustomPipelineModel
from .model_config import CustomModelConfig

# IMPORTANT: `name` must match config.json architectures[0] exactly.
custom_arch = SupportedArchitecture(
    name="{arch_name}",
    task=PipelineTask.TEXT_GENERATION,
    example_repo_ids=["{analysis["model_id"]}"],
    default_weights_format=WeightsFormat.safetensors,
    default_encoding=CustomModelConfig.DEFAULT_ENCODING,
    supported_encodings=CustomModelConfig.SUPPORTED_ENCODINGS,
    pipeline_model=CustomPipelineModel,
    tokenizer=TextTokenizer,
    context_type=TextContext,
    config=CustomModelConfig,
)
'''

    config_py = f'''"""Config mapping for {arch_name} — TODO: map HF fields."""
# See: {DOCS["bring_up"]}
# Primary source: https://huggingface.co/{analysis["model_id"]}
#
# Start from the Hugging Face config.json and map every field your layers need.


class CustomModelConfig:
    """Placeholder — replace with a real MAX ModelConfig subclass."""

    # Keep registration importable; replace these with the encodings the
    # implemented graph and weight adapters actually support.
    DEFAULT_ENCODING = "bfloat16"
    SUPPORTED_ENCODINGS = {{"bfloat16", "float32"}}

    @classmethod
    def from_huggingface(cls, hf_config):  # type: ignore[no-untyped-def]
        raise NotImplementedError(
            "Map HF config → MAX config for {arch_name}. "
            "See Modular model bring-up docs."
        )
'''

    model_py = f'''"""Pipeline model stub for {arch_name}.

TODO (human — mxstudio will not complete this automatically):
  [ ] Attention / MoE / norm layers matching the paper + HF modeling code
  [ ] Weight adapters for safetensors key names
  [ ] KV-cache shapes compatible with MAX serving
  [ ] Smoke-test: max serve --custom-architectures …

Study nearby MAX arch: {analysis.get("similar_hint")}

Primary sources:
  - Hugging Face: https://huggingface.co/{analysis["model_id"]}
  - Custom arch docs: {DOCS["custom_arch"]}
  - Bring-up workflow: {DOCS["bring_up"]}
"""


class CustomPipelineModel:
    """Placeholder PipelineModel — implement the MAX graph here."""

    def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise NotImplementedError(
            "Implement {arch_name} as a MAX PipelineModel. "
            "Use the HF modeling_*.py and the paper as references."
        )
'''

    scaffold_gaps = [
        {
            **gap,
            "status": "scaffolded" if gap.get("owner") == "scaffold" else "stub",
        }
        for gap in analysis["gaps"]
    ]
    readme = f"""# Custom architecture: `{package}`

Target model: `{analysis["model_id"]}`
HF architecture name: `{arch_name}`

## Honest scope

mxstudio **scaffolded** this package. It does **not** implement a working graph.
You (or an agent + Modular docs) must fill `model_config.py` and `model.py`.

## Gap checklist

{_gap_markdown(scaffold_gaps)}

## Nearest reference

{analysis.get("similar_hint")}

## Next steps

1. Open the Hugging Face repo and paper; cite them in your PR.
2. Fill `model_config.py` and `model.py` (start from HF `modeling_*.py`).
3. Keep `SupportedArchitecture.name` == `config.json` `architectures[0]`.
4. Serve:

```bash
{analysis["serve_cmd"]}
```

5. Benchmark from the mxstudio Benchmark tab once the server is healthy.
6. Upstream to Modular when it works — this is how the ecosystem grows.

Docs: {DOCS["custom_arch"]}
"""

    meta = {
        "model_id": analysis["model_id"],
        "architecture": arch_name,
        "package": package,
        "docs": DOCS,
        "generated_by": "mxstudio.architectures",
    }
    artifacts = {
        "__init__.py": init_py,
        "arch.py": arch_py,
        "model_config.py": config_py,
        "model.py": model_py,
        "README.md": readme,
        "mxstudio_meta.json": json.dumps(meta, indent=2),
    }
    created: list[str] = []
    preserved: list[str] = []
    for name, content in artifacts.items():
        path = out / name
        if path.exists():
            preserved.append(name)
            continue
        path.write_text(content, encoding="utf-8")
        created.append(name)

    refreshed = analyze(model_id)
    return {
        **refreshed,
        "scaffolded": bool(created),
        "message": (
            f"Wrote {len(created)} starter files at ports/{package}"
            if created
            else f"Scaffold already exists at ports/{package}; no files were overwritten"
        ),
        "created_files": created,
        "preserved_files": preserved,
        "files": [
            str((out / name).relative_to(REPO_ROOT))
            for name in artifacts
        ],
    }


def smoke_check(model_id: str) -> dict:
    """Mechanical checks only — does not prove max serve will work."""
    analysis = analyze(model_id)
    if not analysis.get("ok"):
        return analysis
    if analysis.get("modular_compatible"):
        return {
            **analysis,
            "smoke": {
                "ok": True,
                "skipped": True,
                "message": "Built-in MAX architecture — custom smoke not needed.",
                "checks": [],
            },
        }

    package = analysis["package_name"]
    out = PORTS_DIR / package
    checks: list[dict] = []

    required = [
        "__init__.py",
        "arch.py",
        "model_config.py",
        "model.py",
        "README.md",
    ]
    for name in required:
        path = out / name
        checks.append(
            {
                "id": f"file:{name}",
                "ok": path.is_file(),
                "detail": f"{'found' if path.is_file() else 'missing'}: ports/{package}/{name}",
            }
        )

    todos_left: list[str] = []
    for name in ("model.py", "model_config.py", "arch.py", "__init__.py"):
        path = out / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            ast.parse(text)
            checks.append(
                {
                    "id": f"syntax:{name}",
                    "ok": True,
                    "detail": f"Python syntax OK: {name}",
                }
            )
        except SyntaxError as exc:
            checks.append(
                {
                    "id": f"syntax:{name}",
                    "ok": False,
                    "detail": f"Syntax error in {name}: {exc}",
                }
            )
        if "NotImplementedError" in text or re.search(r"(?m)^\s*#\s*TODO\b", text):
            todos_left.append(name)

    if todos_left:
        checks.append(
            {
                "id": "todos",
                "ok": False,
                "detail": (
                    "Human TODOs / NotImplementedError still present in: "
                    + ", ".join(todos_left)
                    + " — expected until you implement the graph."
                ),
            }
        )
    else:
        checks.append(
            {
                "id": "todos",
                "ok": True,
                "detail": "No obvious TODO / NotImplementedError markers left.",
            }
        )

    import_ok = False
    if (out / "__init__.py").is_file():
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import importlib; "
                        f"importlib.import_module('ports.{package}')"
                    ),
                ],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            import_ok = proc.returncode == 0
            detail = (
                f"Package import OK: ports.{package}"
                if import_ok
                else "Package import failed: "
                + ((proc.stderr or proc.stdout or "unknown import error")[-500:].strip())
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            detail = f"Package import failed: {exc}"
        checks.append({"id": "package_import", "ok": import_ok, "detail": detail})

    checks.append(
        {
            "id": "serve_ready",
            "ok": False,
            "detail": (
                "Full max serve compile is NOT verified by this smoke check. "
                "After implementing, run the serve command from Hand-off."
            ),
        }
    )

    ok_files = all(c["ok"] for c in checks if c["id"].startswith("file:"))
    ok_syntax = all(c["ok"] for c in checks if c["id"].startswith("syntax:"))
    implementation_complete = not todos_left
    return {
        **analysis,
        "smoke": {
            "ok": ok_files and ok_syntax and import_ok,
            "skipped": False,
            "message": (
                "Mechanical checks pass. Implementation TODOs remain, so this is not serve-ready."
                if ok_files and ok_syntax and import_ok and not implementation_complete
                else "Mechanical checks pass; run MAX serve to validate graph compilation."
                if ok_files and ok_syntax and import_ok
                else "Smoke found structural problems — fix files before continuing."
            ),
            "checks": checks,
            "todos_in": todos_left,
            "implementation_complete": implementation_complete,
            "serve_ready": False,
        },
    }


def _gap_markdown(gaps: list[dict]) -> str:
    lines = []
    for g in gaps:
        mark = {
            "builtin": "[built-in]",
            "scaffolded": "[scaffold]",
            "stub": "[stub]",
            "done": "[x]",
            "missing": "[ ]",
        }.get(g["status"], "[ ]")
        lines.append(
            f"- {mark} **{g['title']}** (`{g['file']}`, owner={g.get('owner', '?')}) — {g['detail']}"
        )
    return "\n".join(lines)
