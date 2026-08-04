#!/usr/bin/env python3
"""Quick smoke test for the MAX serve endpoint."""
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="EMPTY")

completion = client.chat.completions.create(
    model="Qwen/Qwen2.5-3B-Instruct",
    messages=[
        {"role": "user", "content": "What is 2+2? Answer in one word."},
    ],
    max_tokens=20,
)

print(completion.choices[0].message.content)
