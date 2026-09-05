# dnhacks26

Start by cloning the repository:

```sh
git clone https://github.com/yav-fi/dnhacks26.git
```

Yavin added `AGENTS.md` and `CLAUDE.md` to keep coding-agent instructions consistent across tools.

## Model setup

This project uses `mlx-community/Qwen3.8-27B-4bit` (MLX format, ~15GB). Weights are not committed to the repo — each team member downloads their own local copy:

```sh
pip install -U huggingface_hub mlx-vlm
./scripts/download_model.sh
```

This pulls the model into `models/qwen3.8-27b-4bit/` (gitignored). Run it with:

```sh
python -m mlx_vlm.generate --model models/qwen3.8-27b-4bit --max-tokens 100 --temperature 0.0 --prompt "..."
```

## Chat backend

Fast local chat inference runs through [`chad`](https://pypi.org/project/chad-code/), an
MLX chat server with DFlash2 speculative decoding. It runs as a separate local process —
no chad code lives in this repo — and this project talks to it only through
`chat_client.py`.

This project pins the target/draft pair to `mlx-community/Qwen3.8-27B-4bit` (already in
`models/qwen3.8-27b-4bit`) plus a matching w4:gs64 DFlash2 draft head, **not** chad's own
default bundled model — the benchmarked, corrected number for this exact pairing is
**~65 tok/s decode** (dflash2-mlx's "Cross-runtime comparison" table; ~2.8x over serial),
rising to **~66 tok/s** with the LibraSpec algorithm enabled. Chad's own default model is
a different quantization and isn't used here.

```sh
pip install -U uv huggingface_hub chad-code
./scripts/build_dflash_draft.sh   # one-time: builds models/dflash2-w4gs64
./scripts/start_chat_server.sh
```

If a local checkout of the LibraSpec-enabled chad fork is present at
`~/dflash2-mlx/runtime/chad` (private repo), the start script uses it automatically for
the extra ~1.5% from LibraSpec; otherwise it falls back to the public `chad-code`
package with the same target/draft pair (~65 tok/s, no LibraSpec).

**One command, ollama-`run`-style** (starts the server if it's not already up):

```sh
./scripts/ask.sh "what's 17 * 23?"   # one-shot: streams the answer + a tok/s readout
./scripts/ask.sh                     # interactive multi-turn REPL (/bye or Ctrl-D to exit)
```

Or, once it's serving on `http://localhost:8081`, use it from Python directly:

```python
from chat_client import chat_sync

result = chat_sync("Say hi in five words.")
print(result.text, result.timings)
```

`chat_sync`/`chat` apply the model's own chat template (system/user/assistant turns)
before sending, so replies act like an assistant instead of a raw continuation of your
text. Use `complete`/`complete_sync` instead if you want to send an already-formatted
prompt as-is with no template applied. For multi-turn conversations (history carried
across turns, like the REPL above), use `Conversation`:

```python
from chat_client import Conversation

conv = Conversation()
for chunk in conv.send("my favorite color is teal"):
    print(chunk, end="")
for chunk in conv.send("what is my favorite color?"):
    print(chunk, end="")
```

Set `CHAT_SERVER_URL` if the server runs on a different host/port, or `CHAT_MODEL_DIR` if
the chat template should be loaded from a model dir other than `models/qwen3.8-27b-4bit`.
