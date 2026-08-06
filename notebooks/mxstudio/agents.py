"""Small, inspectable agent loop for the active MAX model."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from notebooks.mxstudio import hwinfo, registry
from notebooks.mxstudio.bench import MANAGER as BENCH
from notebooks.mxstudio.servers import MANAGER as SERVERS

AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_server_status",
            "description": "Return the state and model id of the local MAX server.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_model_details",
            "description": "Return mxstudio metadata for the model currently served by MAX.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_hardware_summary",
            "description": "Return mxstudio's read-only CPU, GPU, memory, and disk summary.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_benchmark_history",
            "description": "Return recent local MAX benchmark results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                        "description": "Maximum number of recent results to return.",
                    }
                },
            },
        },
    },
]

SYSTEM_PROMPT = """You are the mxstudio local agent. Use the supplied read-only tools when
the user asks about the active MAX server, model, hardware, or benchmark results. Explain
tool evidence plainly and never claim that you changed the system. If no tool is relevant,
answer normally. Keep responses concise."""


def dispatch_tool(name: str, arguments: dict[str, Any]) -> Any:
    """Execute one allowlisted, read-only mxstudio tool."""
    if name == "get_server_status":
        return SERVERS.status()
    if name == "get_model_details":
        status = SERVERS.status()
        model_id = status.get("model_id")
        entry = registry.get(model_id) if model_id else None
        return {
            "server": status,
            "model": entry.to_dict() if entry else None,
        }
    if name == "get_hardware_summary":
        return hwinfo.summary()
    if name == "get_benchmark_history":
        raw_limit = arguments.get("limit", 5)
        limit = max(1, min(int(raw_limit), 20))
        return BENCH.history()[-limit:]
    return {"error": f"unknown or disallowed tool: {name}"}


def _event(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


async def agent_events(
    client: Any,
    *,
    model_id: str,
    messages: list[dict[str, Any]],
    temperature: float,
    top_p: float,
    max_tokens: int,
    max_steps: int,
) -> AsyncIterator[str]:
    """Run a bounded OpenAI tool-calling loop and emit an inspectable SSE trace."""
    conversation = [{"role": "system", "content": SYSTEM_PROMPT}, *messages]
    yield _event({"type": "start", "model_id": model_id, "max_steps": max_steps})

    try:
        for step in range(1, max_steps + 1):
            yield _event({"type": "step", "step": step})
            response = await client.chat.completions.create(
                model=model_id,
                messages=conversation,
                tools=AGENT_TOOLS,
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
            )
            if not response.choices:
                raise RuntimeError("MAX returned no agent response choices")

            message = response.choices[0].message
            tool_calls = message.tool_calls or []
            if not tool_calls:
                content = message.content or ""
                if content:
                    yield _event({"type": "delta", "delta": content})
                yield _event({"type": "finish", "reason": "stop", "steps": step})
                return

            conversation.append(message.model_dump(exclude_none=True))
            for tool_call in tool_calls:
                try:
                    arguments = json.loads(tool_call.function.arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                if not isinstance(arguments, dict):
                    arguments = {}

                name = tool_call.function.name
                yield _event(
                    {
                        "type": "tool-call",
                        "tool_call_id": tool_call.id,
                        "name": name,
                        "arguments": arguments,
                        "step": step,
                    }
                )
                result = dispatch_tool(name, arguments)
                yield _event(
                    {
                        "type": "tool-result",
                        "tool_call_id": tool_call.id,
                        "name": name,
                        "result": result,
                        "step": step,
                    }
                )
                conversation.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(result, default=str),
                    }
                )

        yield _event(
            {
                "type": "finish",
                "reason": "max_steps",
                "steps": max_steps,
                "message": "Agent stopped at the configured safety limit.",
            }
        )
    except Exception as exc:
        yield _event({"type": "error", "error": str(exc)[:500]})
