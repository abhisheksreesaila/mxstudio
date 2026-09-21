"""Nucleus deployment profile: authenticated, OpenAI-compatible inference ingress.

This module deliberately owns no model process.  ``servers.MANAGER`` remains the
single MAX lifecycle owner; Nucleus adds admission, bounded readiness and leases.
"""
from __future__ import annotations

import asyncio
import hmac
import math
import os
import threading
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

import anyio
import httpx

from notebooks.mxstudio import architectures, downloads, registry
from notebooks.mxstudio.servers import MANAGER as SERVERS, MODEL_HOST, MODEL_PORT

DEFAULT_MODEL_ENV = "NUCLEUS_MODEL_ID"
DEFAULT_READY_TIMEOUT = 30.0
DEFAULT_START_TIMEOUT = 600.0


def api_key() -> str:
    return os.environ.get("MXSTUDIO_NUCLEUS_API_KEY", "").strip()


def configured() -> bool:
    return bool(api_key())


def model_id() -> str:
    return os.environ.get(DEFAULT_MODEL_ENV, "").strip()


class NucleusManager:
    """Admission controller around the existing managed MAX server."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._transition = False
        self._generation = 0
        self._leases: dict[str, dict[str, object]] = {}
        self._pending = 0
        self._progress = {"state": "idle", "message": "not prepared", "generation": 0}

    def status(self) -> dict:
        with self._lock:
            server = SERVERS.status()
            return {
                "profile": "nucleus",
                "configured": configured(),
                "desired_model": model_id() or None,
                "server": server,
                "readiness": dict(self._progress),
                "active_leases": len(self._leases),
                "counters_scope": "this process; filesystem reservations enforce cross-process exclusion",
                "pending_preparations": self._pending,
                "transition": self._transition,
            }

    def resolve(self, requested: str | None = None) -> str:
        if requested is not None and (not isinstance(requested, str) or not requested.strip()):
            raise ValueError("model must be a nonempty string")
        wanted = requested.strip() if requested is not None else model_id()
        if wanted in {"nucleus-local", "local"}:
            wanted = model_id()
        if not wanted:
            wanted = str(SERVERS.status().get("model_id") or "")
        if not wanted:
            raise ValueError("NUCLEUS_MODEL_ID is not configured")
        return wanted

    def _entry(self, requested: str | None = None):
        wanted = self.resolve(requested)
        entry = registry.get(wanted)
        if entry is None:
            if not downloads.is_installed(wanted):
                raise RuntimeError(f"model is not downloaded: {wanted}")
            entry = registry.ModelEntry(id=wanted, name=wanted.rsplit("/", 1)[-1], params="?", approx_gb=0, context=0, license="?", description="Custom Hugging Face model", innovation="", custom_arch=architectures.custom_arch_relative(wanted))
        if not entry.serve_enabled:
            raise RuntimeError(entry.serve_disabled_reason or "model serving is disabled")
        if not downloads.is_installed(entry.id):
            raise RuntimeError(f"model is not downloaded: {entry.id}")
        return entry

    @staticmethod
    def _ready(status: dict, wanted: str) -> bool:
        return (status.get("state") == "running" and status.get("model_id") == wanted
                and status.get("host") == MODEL_HOST and SERVERS.model_matches(wanted))

    def _prepare_sync(self, requested: str | None, timeout: float) -> dict:
        deadline = time.monotonic() + timeout
        wanted = self.resolve(requested)
        with self._condition:
            while self._transition:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("model transition is still in progress")
                self._condition.wait(remaining)
            self._transition = True
            self._generation += 1
            generation = self._generation
            self._progress = {"state": "starting", "message": "starting model", "generation": generation}
        try:
            # Fast path can coexist with active inference in any process.
            with SERVERS.reservation(shared=True):
                current = SERVERS.status()
                ready = self._ready(current, wanted)
            if not ready:
                entry = self._entry(wanted)
                with SERVERS.transition():
                    if time.monotonic() >= deadline:
                        raise TimeoutError("model preparation deadline exceeded")
                    SERVERS.start(entry)
                    while time.monotonic() < deadline:
                        current = SERVERS.status()
                        if self._ready(current, wanted):
                            break
                        if current.get("state") == "crashed":
                            raise RuntimeError("MAX exited during preparation; inspect local server logs")
                        time.sleep(min(0.1, max(0, deadline - time.monotonic())))
                    else:
                        raise TimeoutError("model readiness deadline exceeded")
            with self._lock:
                self._progress = {"state": "ready", "message": "model ready", "generation": generation, "model_id": wanted}
            return {"model_id": wanted, "server": current, "readiness": dict(self._progress)}
        except Exception:
            with self._lock:
                self._progress = {"state": "failed", "message": "preparation failed or busy; inspect local diagnostics", "generation": generation}
            raise
        finally:
            with self._condition:
                self._transition = False
                self._condition.notify_all()

    async def prepare(self, requested: str | None = None, timeout: float = DEFAULT_READY_TIMEOUT) -> dict:
        if not math.isfinite(timeout) or not 0.1 <= timeout <= DEFAULT_START_TIMEOUT:
            raise ValueError("timeout must be between 0.1 and 600 seconds")
        with self._lock:
            if self._pending >= 32:
                raise RuntimeError("preparation queue is full; retry later")
            self._pending += 1

        def run():
            try:
                return self._prepare_sync(requested, timeout)
            finally:
                with self._lock:
                    self._pending -= 1

        task = asyncio.create_task(asyncio.to_thread(run))
        # Consume errors even if this caller disconnects; the worker retains its
        # reservation until completion/deadline, independent of that caller.
        task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        return await asyncio.shield(task)

    @asynccontextmanager
    async def lease(self, requested: str | None = None, timeout: float = DEFAULT_READY_TIMEOUT) -> AsyncIterator[dict]:
        wanted = await asyncio.to_thread(self.resolve, requested)
        await self.prepare(wanted, timeout)
        token = uuid.uuid4().hex
        # Re-check after acquiring the reservation: another process could have
        # switched models between prepare returning and this admission.
        with SERVERS.reservation(shared=True):
            current = await asyncio.to_thread(SERVERS.status)
            if not await asyncio.to_thread(self._ready, current, wanted):
                raise RuntimeError("model changed during admission; retry")
            with self._lock:
                self._leases[token] = {"started_at": time.time(), "model": wanted}
            try:
                yield {"model_id": wanted, "server": current}
            finally:
                with self._lock:
                    self._leases.pop(token, None)


MANAGER = NucleusManager()


def authorized(authorization: str | None, x_api_key: str | None) -> bool:
    expected = api_key()
    if not expected:
        return False
    supplied = (x_api_key or "").strip()
    if not supplied and authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer":
            supplied = value.strip()
    return bool(supplied) and hmac.compare_digest(supplied.encode(), expected.encode())


async def close_upstream(response, client) -> None:
    with anyio.CancelScope(shield=True):
        try:
            if response is not None:
                await response.aclose()
        finally:
            await client.aclose()


async def proxy_openai(path: str, body: dict):
    """Open upstream before downstream headers, with bounded network waits."""
    client = httpx.AsyncClient(
        base_url=f"http://127.0.0.1:{MODEL_PORT}",
        timeout=httpx.Timeout(300.0, connect=5.0, pool=5.0),
        trust_env=False,
    )
    response = None
    try:
        request = client.build_request("POST", path, json=body)
        response = await client.send(request, stream=True)
        if body.get("stream") and response.is_success:
            if "text/event-stream" not in response.headers.get("content-type", ""):
                raise RuntimeError("MAX returned an invalid streaming content type")
            return response, client
        await response.aread()
    except BaseException:
        await close_upstream(response, client)
        raise
    await close_upstream(response, client)
    return response
