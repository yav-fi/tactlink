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

_DEFAULT_MODEL_DIR = Path(__file__).parent / "models" / "qwen3.5-9b-4bit"
_tokenizer = None  # lazily loaded; only needed by chat()/chat_sync()


@dataclass
class CompletionResult:
    text: str
    timings: dict


def _get_tokenizer():
    global _tokenizer
    if _tokenizer is None:
        from transformers import AutoTokenizer

        model_dir = os.environ.get("CHAT_MODEL_DIR", str(_DEFAULT_MODEL_DIR))
        _tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True)
    return _tokenizer


def _render_messages(messages: list, system_message: Optional[str] = None) -> str:
    """Apply the model's own chat template so a raw completion endpoint replies like
    an assistant instead of free-associating a continuation of the bare text."""
    full = []
    if system_message:
        full.append({"role": "system", "content": system_message})
    full.extend(messages)
    return _get_tokenizer().apply_chat_template(
        full, tokenize=False, add_generation_prompt=True, enable_thinking=False
    )


def _render_chat(user_message: str, system_message: Optional[str] = None) -> str:
    return _render_messages([{"role": "user", "content": user_message}], system_message)


class Conversation:
    """Multi-turn chat session: keeps history so each `send()` sees prior turns,
    the way an ollama-style REPL does. Each `send()` is a generator yielding text
    chunks; its return value (via `StopIteration.value`) carries the turn's timings."""

    def __init__(
        self,
        system_message: Optional[str] = None,
        n_predict: int = 256,
        temperature: Optional[float] = None,
        base_url: Optional[str] = None,
    ):
        self.system_message = system_message
        self.n_predict = n_predict
        self.temperature = temperature
        self.base_url = base_url
        self.messages: list = []

    def send(self, user_text: str) -> Iterator[str]:
        self.messages.append({"role": "user", "content": user_text})
        prompt = _render_messages(self.messages, self.system_message)
        gen = complete(
            prompt,
            n_predict=self.n_predict,
            temperature=self.temperature,
            base_url=self.base_url,
        )
        parts = []
        timings: dict = {}
        while True:
            try:
                chunk = next(gen)
            except StopIteration as e:
                timings = e.value or {}
                break
            parts.append(chunk)
            yield chunk
        text = "".join(parts)
        self.messages.append({"role": "assistant", "content": text})
        return timings


def complete(
    prompt: str,
    n_predict: int = 256,
    temperature: Optional[float] = None,
    base_url: Optional[str] = None,
) -> Iterator[str]:
    """Stream a completion for `prompt`, yielding text chunks as they arrive.

    Once the generator is exhausted, its return value (retrievable via
    `StopIteration.value` when driving it manually, e.g. `gen = complete(...)`
    then `next(gen)` in a loop) carries the server's final `timings` dict
    (`prompt_n`, `prompt_ms`, `predicted_n`, `predicted_ms`). `complete_sync`
    collects this for you if you don't need to stream.
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
                yield chunk["content"]
            if chunk.get("stop"):
                timings = chunk.get("timings", {})
                break
    return timings


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


def _print_stream(gen: Iterator[str]) -> dict:
    """Drive a streaming generator to stdout, print a trailing tok/s line to
    stderr (so stdout stays clean text), and return the final timings dict."""
    import sys

    timings: dict = {}
    while True:
        try:
            chunk = next(gen)
        except StopIteration as e:
            timings = e.value or {}
            break
        print(chunk, end="", flush=True)
    print()
    predicted_n = timings.get("predicted_n", 0)
    predicted_ms = timings.get("predicted_ms", 0)
    if predicted_ms:
        tok_s = predicted_n / (predicted_ms / 1000)
        print(f"[{predicted_n} tok in {predicted_ms / 1000:.2f}s -> {tok_s:.1f} tok/s]",
              file=sys.stderr)
    return timings


def _main() -> None:
    import sys

    if len(sys.argv) > 1:
        _print_stream(chat(" ".join(sys.argv[1:])))
        return

    print("Chatting with chad. Ctrl-D or /bye to exit.")
    conv = Conversation()
    while True:
        try:
            user_text = input(">>> ")
        except EOFError:
            print()
            break
        user_text = user_text.strip()
        if user_text in ("/bye", "/exit", "/quit"):
            break
        if not user_text:
            continue
        _print_stream(conv.send(user_text))


if __name__ == "__main__":
    _main()
