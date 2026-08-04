#!/usr/bin/env python3
"""Chat with the local MAX endpoint (OpenAI-compatible API)."""

from __future__ import annotations

import argparse
import sys

from openai import OpenAI


def main() -> int:
    parser = argparse.ArgumentParser(description="Use the local MAX endpoint")
    parser.add_argument(
        "--host", default="localhost", help="Server host (default: localhost)"
    )
    parser.add_argument("--port", type=int, default=8000, help="Server port (default: 8000)")
    parser.add_argument(
        "--model",
        default="Qwen/Qwen2.5-3B-Instruct",
        help="Model id (default: Qwen/Qwen2.5-3B-Instruct)",
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        help="Single prompt (omit for interactive chat)",
    )
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--stream", action="store_true", help="Stream tokens to stdout")
    args = parser.parse_args()

    client = OpenAI(
        base_url=f"http://{args.host}:{args.port}/v1",
        api_key="EMPTY",
    )

    messages: list[dict[str, str]] = [
        {"role": "system", "content": "You are a helpful assistant."},
    ]

    def run_turn(user_text: str) -> None:
        messages.append({"role": "user", "content": user_text})

        if args.stream:
            stream = client.chat.completions.create(
                model=args.model,
                messages=messages,
                max_tokens=args.max_tokens,
                stream=True,
            )
            parts: list[str] = []
            for chunk in stream:
                delta = chunk.choices[0].delta.content or ""
                if delta:
                    parts.append(delta)
                    print(delta, end="", flush=True)
            print()
            reply = "".join(parts)
        else:
            completion = client.chat.completions.create(
                model=args.model,
                messages=messages,
                max_tokens=args.max_tokens,
            )
            reply = completion.choices[0].message.content or ""
            print(reply)

        messages.append({"role": "assistant", "content": reply})

    if args.prompt:
        run_turn(args.prompt)
        return 0

    print(f"MAX chat — {args.model} @ {args.host}:{args.port}")
    print("Type a message and press Enter. /reset clears history. Ctrl-D to exit.\n")

    try:
        while True:
            try:
                user_input = input("> ").strip()
            except EOFError:
                print()
                break

            if not user_input:
                continue
            if user_input == "/reset":
                messages[:] = [{"role": "system", "content": "You are a helpful assistant."}]
                print("(context cleared)")
                continue

            run_turn(user_input)
            print()
    except KeyboardInterrupt:
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
