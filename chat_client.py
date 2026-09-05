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
from pathlib import Path
from typing import Iterator, Optional

_DEFAULT_MODEL_DIR = Path(__file__).parent / "models" / "qwen3.8-27b-4bit"
_tokenizer = None  # lazily loaded; only needed by chat()/chat_sync()


@dataclass
class CompletionResult:
    text: str
    timings: dict


def _render_chat(user_message: str, system_message: Optional[str] = None) -> str:
    """Apply the model's own chat template so a raw completion endpoint replies like
    an assistant instead of free-associating a continuation of the bare text."""
    global _tokenizer
    if _tokenizer is None:
        from transformers import AutoTokenizer

        model_dir = os.environ.get("CHAT_MODEL_DIR", str(_DEFAULT_MODEL_DIR))
        _tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True)
    messages = []
    if system_message:
        messages.append({"role": "system", "content": system_message})
    messages.append({"role": "user", "content": user_message})
    return _tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True, enable_thinking=False
    )


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


def chat(
    user_message: str,
    system_message: Optional[str] = None,
    n_predict: int = 256,
    temperature: Optional[float] = None,
    base_url: Optional[str] = None,
) -> Iterator[str]:
    """Like `complete`, but wraps `user_message` in the model's chat template first —
    use this for actual conversation; use `complete`/`complete_sync` for raw
    continuation of an already-formatted prompt."""
    return complete(
        _render_chat(user_message, system_message),
        n_predict=n_predict,
        temperature=temperature,
        base_url=base_url,
    )


def chat_sync(
    user_message: str,
    system_message: Optional[str] = None,
    n_predict: int = 256,
    temperature: Optional[float] = None,
    base_url: Optional[str] = None,
) -> CompletionResult:
    """Non-streaming version of `chat`."""
    return complete_sync(
        _render_chat(user_message, system_message),
        n_predict=n_predict,
        temperature=temperature,
        base_url=base_url,
    )
