"""Thin client for the chad chat backend (see scripts/start_chat_server.sh).

Talks to chad's `/completion` SSE endpoint (llama.cpp wire protocol), the only
place in this project that knows chad's request/response shape. Swapping the
backend later means editing this file, not the rest of the app.
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Iterator, Optional


@dataclass
class CompletionResult:
    text: str
    timings: dict


def complete(
    prompt: str,
    n_predict: int = 256,
    temperature: Optional[float] = None,
    base_url: Optional[str] = None,
) -> Iterator[str]:
    """Stream a completion for `prompt`, yielding text chunks as they arrive.

    The final chunk of the underlying stream carries timings but no new text;
    call `complete_sync` instead if you just want the finished string + timings.
    """
    url = (base_url or os.environ.get("CHAT_SERVER_URL", "http://localhost:8081")).rstrip("/")
    body: dict = {"prompt": prompt, "n_predict": n_predict}
    if temperature is not None:
        body["temperature"] = temperature

    req = urllib.request.Request(
        f"{url}/completion",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        for raw_line in resp:
            line = raw_line.decode("utf-8").strip()
            if not line or not line.startswith("data: "):
                continue
            payload = line[len("data: "):]
            if payload == "[DONE]":
                break
            chunk = json.loads(payload)
            if chunk.get("content"):
                yield chunk["content"]
            if chunk.get("stop"):
                break


def complete_sync(
    prompt: str,
    n_predict: int = 256,
    temperature: Optional[float] = None,
    base_url: Optional[str] = None,
) -> CompletionResult:
    """Non-streaming convenience wrapper: collects the full text and final timings."""
    url = (base_url or os.environ.get("CHAT_SERVER_URL", "http://localhost:8081")).rstrip("/")
    body: dict = {"prompt": prompt, "n_predict": n_predict}
    if temperature is not None:
        body["temperature"] = temperature

    req = urllib.request.Request(
        f"{url}/completion",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    text_parts = []
    timings: dict = {}
    with urllib.request.urlopen(req) as resp:
        for raw_line in resp:
            line = raw_line.decode("utf-8").strip()
            if not line or not line.startswith("data: "):
                continue
            payload = line[len("data: "):]
            if payload == "[DONE]":
                break
            chunk = json.loads(payload)
            if chunk.get("content"):
                text_parts.append(chunk["content"])
            if chunk.get("stop"):
                timings = chunk.get("timings", {})
                break
    return CompletionResult(text="".join(text_parts), timings=timings)
