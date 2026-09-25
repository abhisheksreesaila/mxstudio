"""Revision-pinned model resolution and local inventory."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from notebooks.mxstudio import compat, downloads, jobs_db, registry
from notebooks.mxstudio.settings import get_hf_token

_REVISION_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_SCHEMA_VERSION = 1
_CURATED_HF_ARCHITECTURES = {
    "Qwen/Qwen2.5-0.5B-Instruct": "Qwen2ForCausalLM",
    "Qwen/Qwen2.5-1.5B-Instruct": "Qwen2ForCausalLM",
    "Qwen/Qwen2.5-3B-Instruct": "Qwen2ForCausalLM",
    "HuggingFaceTB/SmolLM2-1.7B-Instruct": "LlamaForCausalLM",
    "google/gemma-3-1b-it": "Gemma3ForCausalLM",
    "meta-llama/Llama-3.2-1B-Instruct": "LlamaForCausalLM",
    "microsoft/Phi-3.5-mini-instruct": "Phi3ForCausalLM",
    "ibm-granite/granite-3.3-2b-instruct": "GraniteForCausalLM",
    "mistralai/Mistral-7B-Instruct-v0.3": "MistralForCausalLM",
    "allenai/OLMo-2-0425-1B-Instruct": "Olmo2ForCausalLM",
    "Qwen/Qwen3-30B-A3B-Instruct-2507-FP8": "Qwen3MoeForCausalLM",
}


def _connect() -> sqlite3.Connection:
    jobs_db.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(jobs_db.DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS model_inventory (
            model_id TEXT NOT NULL,
            revision TEXT NOT NULL,
            record_json TEXT NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (model_id, revision)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS model_inventory_schema (
            version INTEGER NOT NULL
        )
        """
    )
    row = conn.execute("SELECT version FROM model_inventory_schema LIMIT 1").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO model_inventory_schema (version) VALUES (?)",
            (_SCHEMA_VERSION,),
        )
    elif row["version"] > _SCHEMA_VERSION:
        raise RuntimeError("model inventory database schema is newer than this MX Studio")
    conn.commit()


def _save(record: dict) -> dict:
    model_id, revision = record.get("model_id"), record.get("revision")
    if not model_id or not revision:
        return record
    conn = _connect()
    try:
        _init_db(conn)
        existing = conn.execute(
            "SELECT record_json FROM model_inventory WHERE model_id = ? AND revision = ?",
            (model_id, revision),
        ).fetchone()
        if existing and record.get("state") != "downloaded":
            previous = json.loads(existing["record_json"])
            if previous.get("downloaded") or previous.get("state") == "downloaded":
                record = {
                    **record,
                    "state": "downloaded",
                    "downloaded": True,
                    "snapshot_path": record.get("snapshot_path")
                    or previous.get("snapshot_path"),
                    "manifest": record.get("manifest") or previous.get("manifest"),
                }
        payload = json.dumps(record, sort_keys=True)
        conn.execute(
            """
            INSERT INTO model_inventory (model_id, revision, record_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(model_id, revision) DO UPDATE SET
                record_json = excluded.record_json, updated_at = excluded.updated_at
            """,
            (model_id, revision, payload, time.time()),
        )
        conn.commit()
    finally:
        conn.close()
    return record


def _curated_matches(query: str) -> list[registry.ModelEntry]:
    q = query.casefold().strip()
    if not q:
        return []
    exact = [entry for entry in registry.REGISTRY if entry.id.casefold() == q]
    if exact:
        return exact
    if "/" in q:
        return []
    # Exact curated display-name matching is the primary shortcut. Unique
    # substrings preserve the CLI's established shorthand without hiding ambiguity.
    exact_names = [entry for entry in registry.REGISTRY if entry.name.casefold() == q]
    if exact_names:
        return exact_names
    return [
        entry for entry in registry.REGISTRY
        if q in entry.id.casefold() or q in entry.name.casefold()
    ]


def _curated_data(entry: registry.ModelEntry) -> dict:
    return {
        "name": entry.name,
        "params": entry.params,
        "approx_gb": entry.approx_gb,
        "context": entry.context,
        "license": entry.license,
        "family": entry.family,
        "description": entry.description,
        "custom_arch": entry.custom_arch,
        "serve_enabled": entry.serve_enabled,
        "serve_disabled_reason": entry.serve_disabled_reason,
    }


def _verified_cached_record(record: dict) -> dict | None:
    """Return a cached pulled record only while its exact snapshot still verifies."""
    model_id = record.get("model_id")
    revision = record.get("revision")
    manifest = record.get("manifest")
    if (
        not model_id
        or not revision
        or not _REVISION_RE.fullmatch(str(revision))
        or not isinstance(manifest, list)
        or not manifest
    ):
        return None
    canonical_path = (downloads.repo_dir(model_id) / "snapshots" / revision).resolve()
    record_path = record.get("snapshot_path")
    if record_path and Path(record_path).resolve() != canonical_path:
        return None
    expected_files = [item["name"] for item in manifest if item.get("name")]
    expected_sizes = {
        item["name"]: item["size"]
        for item in manifest
        if item.get("name") and isinstance(item.get("size"), int)
    }
    verification = downloads.validate_snapshot(
        canonical_path, expected_files or None, expected_sizes
    )
    if not verification["ok"]:
        return None
    return {
        **record,
        "snapshot_path": str(canonical_path),
        "manifest": verification["manifest"],
    }


def resolve_model(query: str, revision: str | None = None) -> dict:
    """Resolve a curated shortcut or HF ID to a record pinned to an immutable SHA."""
    value = (query or "").strip().strip("/")
    if value.casefold().startswith("catalog:"):
        return _resolve_catalog_reference(value.split(":", 1)[1].strip(), revision)
    matches = _curated_matches(value)
    if len(matches) > 1:
        return {
            "ok": False,
            "state": "ambiguous",
            "model_id": None,
            "query": value,
            "candidates": [{"model_id": entry.id, "name": entry.name} for entry in matches],
            "advisory": "Use an exact Hugging Face ID or a more specific curated model name.",
        }
    curated = matches[0] if matches else None
    model_id = curated.id if curated else value
    if not model_id or "/" not in model_id:
        return {
            "ok": False,
            "state": "missing",
            "model_id": None,
            "query": value,
            "candidates": [],
            "advisory": "Expected a curated model name or exact org/name Hugging Face ID.",
        }

    if revision and _REVISION_RE.fullmatch(revision):
        cached = get_model(model_id, revision.lower())
        if cached and (cached.get("downloaded") or cached.get("state") == "downloaded"):
            verified = _verified_cached_record(cached)
            if verified:
                return verified

    report = compat.check_hf_model(model_id, revision=revision)
    if not report.get("ok"):
        return {
            "ok": False,
            "state": "missing",
            "model_id": model_id,
            "revision": None,
            "source": "curated" if curated else "huggingface",
            "error": "Hugging Face model lookup failed.",
            "advisory": "Check the model ID, HF access, and token/license settings.",
        }

    resolved_sha = report.get("revision")
    if not isinstance(resolved_sha, str) or not _REVISION_RE.fullmatch(resolved_sha):
        return {
            "ok": False,
            "state": "blocked",
            "model_id": model_id,
            "revision": resolved_sha,
            "source": "curated" if curated else "huggingface",
            "error": "Hugging Face did not return an immutable commit SHA.",
            "advisory": "Retry after HF metadata lookup returns a commit revision.",
        }

    architecture = report.get("architecture")
    max_list_available = bool(report.get("max_list_available"))
    compatible = bool(report.get("modular_compatible"))
    architecture_status = "unknown"
    if architecture and not compatible:
        architecture_status = "unsupported"
    elif architecture and max_list_available:
        listed = compat.max_architectures()
        architecture_status = (
            "supported"
            if compat.architecture_listed(str(architecture), listed)
            else "advisory"
        )
    elif architecture and compatible:
        architecture_status = "advisory"

    format_status = report.get("format_status")
    if format_status not in {"supported", "unsupported"}:
        format_status = "supported" if report.get("has_safetensors") else "unsupported"
    encoding_check = report.get("encoding_compatibility") or {}
    encoding_status = encoding_check.get("status", "unknown")
    if format_status == "unsupported":
        support_status = "unsupported_format"
    elif encoding_status == "unsupported":
        support_status = "unsupported_encoding"
    elif architecture_status == "unsupported":
        support_status = "unsupported"
    elif architecture_status == "unknown":
        support_status = "unknown"
    elif architecture_status == "advisory":
        support_status = "advisory"
    elif not compatible:
        support_status = "unsupported"
    else:
        support_status = "supported"
    blocked = support_status in {
        "unsupported", "unknown", "unsupported_format", "unsupported_encoding"
    }
    advisory = []
    if support_status == "unsupported":
        advisory.append(
            f"MAX does not list architecture {architecture!r}; serving is blocked. "
            "Use Learn → Custom architectures to plan a port."
        )
    elif support_status == "unknown":
        advisory.append("Architecture metadata is missing; serving is blocked pending inspection.")
    elif support_status == "unsupported_format":
        advisory.append(
            "Unsupported checkpoint format: this load path currently requires "
            "safetensors; no large download was started."
        )
    elif support_status == "unsupported_encoding":
        advisory.append(
            encoding_check.get("advisory")
            or "MAX does not list support for this checkpoint encoding."
        )
    elif support_status == "advisory":
        advisory.append(
            "Architecture matches a known MAX family heuristic, but support was not "
            "confirmed by the installed catalog."
        )
    for warning in report.get("warnings") or []:
        if warning not in advisory:
            advisory.append(warning)

    snapshot_path = None
    cached = downloads.snapshot_for_revision(model_id, resolved_sha)
    if cached:
        snapshot_path = str(cached.resolve())
    record = {
        "ok": not blocked,
        "state": "blocked" if blocked else "resolved",
        "model_id": model_id,
        "revision": resolved_sha.lower(),
        "snapshot_path": snapshot_path,
        "source": "curated" if curated else "huggingface",
        "name": (curated.name if curated else report.get("name")) or model_id.rsplit("/", 1)[-1],
        "architecture": architecture,
        "support_status": support_status,
        "supported": support_status == "supported",
        "modular_compatible": compatible,
        "max_list_available": max_list_available,
        "format_status": format_status,
        "encoding_status": encoding_status,
        "checkpoint_encoding": report.get("checkpoint_encoding"),
        "encoding_compatibility": encoding_check,
        "blocked": blocked,
        "advisory": advisory,
        "license": report.get("license") or (curated.license if curated else None),
        "gated": bool(report.get("gated")),
        "has_safetensors": bool(report.get("has_safetensors")),
        "files": report.get("files") or [],
        "approx_gb": report.get("approx_gb"),
        "hardware": report.get("hardware"),
        "hf_url": f"https://huggingface.co/{model_id}",
        "manifest": None,
    }
    if blocked and advisory:
        record["error"] = advisory[0]
    if curated:
        record["curated"] = _curated_data(curated)
    return _save(record)


def catalog() -> dict:
    """Return normalized installed MAX architecture entries and truthful HF examples."""
    result = compat.max_catalog()
    curated_examples = []
    if result["available"]:
        for catalog_entry in result["entries"]:
            architecture = catalog_entry["architecture"]
            examples = list(catalog_entry.get("examples") or [])
            for model in registry.REGISTRY:
                hf_architecture = _CURATED_HF_ARCHITECTURES.get(model.id)
                if hf_architecture and compat.architecture_listed(
                    hf_architecture, {architecture: catalog_entry}
                ):
                    examples.append(model.id)
                    curated_examples.append(
                        {
                            "model_id": model.id,
                            "name": model.name,
                            "architecture": architecture,
                            "provenance": "curated registry family match",
                        }
                    )
            catalog_entry["examples"] = sorted(set(examples), key=str.casefold)
    return {**result, "curated_examples": curated_examples}


def _resolve_catalog_reference(architecture: str, revision: str | None) -> dict:
    normalized = catalog()
    if not normalized.get("available"):
        return {
            "ok": False,
            "state": "blocked",
            "model_id": None,
            "revision": None,
            "source": "catalog",
            "catalog_architecture": architecture or None,
            "candidates": [],
            "error": "Installed MAX catalog is unavailable.",
            "advisory": "Use an exact Hugging Face org/name ID or restore `max list --json` availability.",
        }
    if not architecture:
        return {
            "ok": False,
            "state": "missing",
            "model_id": None,
            "revision": None,
            "source": "catalog",
            "candidates": [entry["architecture"] for entry in normalized["entries"]],
            "advisory": "Supply an exact architecture after `catalog:`.",
        }
    entries = [
        entry
        for entry in normalized["entries"]
        if entry["architecture"].casefold() == architecture.casefold()
    ]
    if not entries:
        return {
            "ok": False,
            "state": "missing",
            "model_id": None,
            "revision": None,
            "source": "catalog",
            "catalog_architecture": architecture,
            "candidates": [entry["architecture"] for entry in normalized["entries"]],
            "advisory": "No exact MAX architecture matched; choose a listed architecture or provide a Hugging Face repo ID.",
        }
    entry = entries[0]
    model_ids = sorted(set(entry.get("examples") or []), key=str.casefold)
    if len(model_ids) > 1:
        names = {model.id: model.name for model in registry.REGISTRY}
        return {
            "ok": False,
            "state": "ambiguous",
            "model_id": None,
            "revision": None,
            "source": "catalog",
            "catalog_architecture": entry["architecture"],
            "candidates": [
                {"model_id": model_id, "name": names.get(model_id, model_id.rsplit("/", 1)[-1])}
                for model_id in model_ids
            ],
            "advisory": "This architecture has multiple HF examples; provide one exact org/name ID.",
        }
    if not model_ids:
        return {
            "ok": False,
            "state": "missing",
            "model_id": None,
            "revision": None,
            "source": "catalog",
            "catalog_architecture": entry["architecture"],
            "candidates": [],
            "advisory": "MAX lists this architecture but provides no resolvable HF example; supply an exact Hugging Face repo ID.",
        }
    record = resolve_model(model_ids[0], revision=revision)
    record["source"] = "catalog"
    record["catalog_architecture"] = entry["architecture"]
    record["provenance"] = {
        "source": entry.get("provenance", "max list --json"),
        "architecture": entry["architecture"],
        "example_model_id": model_ids[0],
    }
    return record


def get_model(model_id: str, revision: str | None = None) -> dict | None:
    conn = _connect()
    try:
        _init_db(conn)
        if revision:
            row = conn.execute(
                "SELECT record_json FROM model_inventory WHERE model_id = ? AND revision = ?",
                (model_id, revision),
            ).fetchone()
        else:
            rows = conn.execute(
                """
                SELECT record_json FROM model_inventory
                WHERE model_id = ? ORDER BY updated_at DESC
                """,
                (model_id,),
            ).fetchall()
            records = [json.loads(row["record_json"]) for row in rows]
            pulled = next(
                (
                    record
                    for record in records
                    if record.get("downloaded") or record.get("state") == "downloaded"
                ),
                None,
            )
            return pulled or (records[0] if records else None)
        return json.loads(row["record_json"]) if row else None
    finally:
        conn.close()


def list_models() -> list[dict]:
    conn = _connect()
    try:
        _init_db(conn)
        rows = conn.execute(
            "SELECT record_json FROM model_inventory ORDER BY model_id, updated_at DESC"
        ).fetchall()
        records: dict[tuple[str, str], dict] = {}
        for row in rows:
            record = json.loads(row["record_json"])
            records[(record["model_id"], record["revision"])] = record
        return sorted(
            records.values(),
            key=lambda record: (record["model_id"].casefold(), record["revision"]),
        )
    finally:
        conn.close()


def entry_for(record: dict) -> registry.ModelEntry:
    """Build a server entry that keeps the canonical HF identity pinned to disk."""
    model_id = record.get("model_id")
    revision = record.get("revision")
    if not model_id or not revision or not _REVISION_RE.fullmatch(str(revision)):
        raise ValueError("model inventory record has no immutable revision identity")
    if (
        record.get("blocked")
        or not record.get("supported", record.get("support_status") == "supported")
        or record.get("support_status") in {"unsupported", "unknown"}
    ):
        raise RuntimeError(
            (record.get("advisory") or ["Model architecture is blocked for MAX serving."])[0]
        )
    snapshot_path = record.get("snapshot_path")
    if not snapshot_path:
        snapshot = downloads.snapshot_for_revision(model_id, revision)
        snapshot_path = str(snapshot.resolve()) if snapshot else None
    if not snapshot_path:
        raise RuntimeError("pinned model snapshot is not installed; pull the exact revision first")
    manifest = record.get("manifest")
    expected_files = [item.get("name") for item in (manifest or []) if item.get("name")]
    expected_sizes = {
        item["name"]: item["size"]
        for item in (manifest or [])
        if item.get("name") and isinstance(item.get("size"), int)
    }
    snapshot_path = str(Path(snapshot_path).resolve())
    expected_snapshot = (
        downloads.repo_dir(model_id) / "snapshots" / revision
    ).resolve()
    if Path(snapshot_path) != expected_snapshot:
        raise RuntimeError("pinned model path does not match the canonical HF cache snapshot")
    verification = downloads.validate_snapshot(
        snapshot_path, expected_files or None, expected_sizes
    )
    if not verification["ok"]:
        raise RuntimeError(
            "pinned model snapshot is incomplete: " + ", ".join(verification["missing_files"])
        )

    entry = registry.get(model_id)
    if entry:
        if not entry.serve_enabled:
            raise RuntimeError(entry.serve_disabled_reason or "serving is disabled for this model")
        return replace(
            entry,
            load_path=str(Path(snapshot_path).resolve()),
            revision=revision,
            advertised_model_id=record.get("advertised_model_id"),
        )
    curated = record.get("curated") or {}
    if curated.get("serve_enabled") is False:
        raise RuntimeError(curated.get("serve_disabled_reason") or "serving is disabled for this model")
    return registry.ModelEntry(
        id=model_id,
        name=record.get("name") or model_id.rsplit("/", 1)[-1],
        params=curated.get("params", "?"),
        approx_gb=record.get("approx_gb") or curated.get("approx_gb") or 0,
        context=curated.get("context") or 0,
        license=record.get("license") or curated.get("license") or "unknown",
        description=curated.get("description") or f"Hugging Face model {model_id}",
        innovation="",
        custom_arch=curated.get("custom_arch"),
        family=curated.get("family", ""),
        architecture=record.get("architecture") or "",
        load_path=str(Path(snapshot_path).resolve()),
        revision=revision,
        advertised_model_id=record.get("advertised_model_id"),
    )


def pull_model(
    record: dict, progress: Callable[[dict], None] | None = None
) -> dict:
    """Download one resolved SHA, verify its full selected manifest, and persist it."""
    model_id = record.get("model_id")
    revision = record.get("revision")
    if not model_id or not revision or not _REVISION_RE.fullmatch(str(revision)):
        raise ValueError("pull_model requires a canonical model ID and immutable commit SHA")
    unsupported_format = (
        record.get("format_status") == "unsupported"
        or record.get("has_safetensors") is False
    )
    if unsupported_format:
        default_error = "Unsupported checkpoint format: this load path currently requires safetensors."
        error = record.get("error") or (
            record.get("advisory") or [default_error]
        )[0]
        return {
            **record,
            "ok": False,
            "state": "blocked",
            "blocked": True,
            "supported": False,
            "error": error,
        }
    cached = _verified_cached_record(record)
    if cached:
        result = {
            **cached,
            "revision": revision.lower(),
            "downloaded": True,
            "state": "downloaded",
        }
        if progress:
            progress(
                {
                    "stage": "downloaded",
                    "revision": revision,
                    "files": len(result["manifest"]),
                    "cached": True,
                }
            )
        return _save(result)
    file_records = record.get("files") or []
    if not file_records:
        report = compat.check_hf_model(model_id, revision=revision)
        if not report.get("ok"):
            raise RuntimeError(report.get("error", "Unable to inspect the pinned HF revision"))
        file_records = report.get("files") or []
    file_names = [item.get("name") for item in file_records if item.get("name")]
    expected_sizes = {
        item["name"]: item["size"]
        for item in file_records
        if item.get("name") and isinstance(item.get("size"), int)
    }
    token = get_hf_token() or None
    downloaded = downloads.pull_revision(
        model_id,
        revision,
        file_names,
        token=token,
        progress=progress,
        expected_sizes=expected_sizes,
    )
    result = {
        **record,
        "revision": revision.lower(),
        "snapshot_path": downloaded["snapshot_path"],
        "manifest": downloaded["manifest"],
        "downloaded": True,
        "state": "downloaded",
    }
    return _save(result)
