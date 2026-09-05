# dnhacks26

Clone:

```sh
git clone https://github.com/yav-fi/dnhacks26.git
```

Yavin added `AGENTS.md` and `CLAUDE.md` to keep coding-agent instructions
consistent across tools.

This repo hosts two hackathon workstreams:

- **[Webcam gesture quadcopter control](#webcam-gesture-quadcopter-control)** —
  fly a simulated drone with hand gestures (`src/`).
- **[Local LLM chat + benchmark](#model-setup)** — fast on-device chat
  inference (`chat_client.py`, `scripts/`).

---

# Webcam gesture quadcopter control

Your hand is the transmitter: a webcam tracks it with MediaPipe, the gesture is
mapped to a flight command, and an arcade physics model flies a drone you watch
in a 3D view next to the camera feed.

## Setup

```sh
python -m venv .venv && .venv\Scripts\activate   # optional
pip install -r requirements.txt
```

## Run

```sh
python src/main.py                # live webcam, camera 0
python src/main.py --camera 1     # a different camera
python src/main.py --demo         # no camera: scripted flight, good for a first check
python src/main.py --demo --headless --out flight.png   # render one sample frame
```

On first live run the MediaPipe hand model (`hand_landmarker.task`, ~7.5 MB) is
downloaded into `models/` automatically. `--demo` needs no camera and no model.

Keys while running: `q` quit · `r` reset · `space` force-arm toggle.

## Gestures (right hand, palm to the camera)

| Gesture | Effect |
| --- | --- |
| Move palm left / right | Yaw left / right |
| Move palm up / down | Climb / descend |
| Tilt hand left / right | Roll (bank and slide sideways) |
| Pinch thumb + index | Pitch forward (fly forward) |
| Open palm (4+ fingers) | Arm / take off |
| Closed fist | Disarm / land |

A centered dead zone keeps the drone steady when your hand is roughly neutral.

## Layout

| File | Role |
| --- | --- |
| `src/main.py` | Capture loop, window, keys, `--demo` / `--headless` |
| `src/hand_tracker.py` | MediaPipe Tasks HandLandmarker → `HandState` |
| `src/controls.py` | `HandState` → normalized `ControlInput` with smoothing |
| `src/simulator.py` | Arcade quadcopter physics (world frame: x right, y forward, z up) |
| `src/visualizer.py` | Look-at pinhole camera, 3D drone render, control HUD |
| `src/control_types.py` | Shared dataclasses |

The simulator takes a normalized `ControlInput` (throttle, yaw_rate, roll,
pitch, armed), so swapping the simulator for a real link (Tello, MAVLink RC
override, serial) is a matter of writing one adapter that consumes the same
struct.

---

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

## Benchmark

This project only ships one configuration (the pairing above), so the benchmark measures
whatever's currently running rather than sweeping configs — it's "is this setup fast on
this machine", not a comparison.

It reuses this machine family's shared thermal-gating implementation from
[mlx-bench](https://github.com/kaarelkaarelson/mlx-bench) (public, MIT license) instead of
reimplementing GPU thermal gating again — clone it once:

```sh
git clone https://github.com/kaarelkaarelson/mlx-bench.git ~/mlx-bench
```

Then run:

```sh
./scripts/benchmark.sh
```

Starts the server if needed, runs 1 rep (by default) of a fixed prompt
(`scripts/matched_prompt.txt`, so every run/machine gets byte-identical input) with 512
max tokens, gates the GPU below 40°C first (waiting and boosting fans if needed, printing
a compliance receipt — hard-fails rather than reporting an unverified number if the gate
can't be met), discards one untimed warmup call first, and reports the decode tok/s.
Writes full results (timings + thermal receipt) to `benchmark_result.json` (gitignored —
a local artifact, not shared state). Pass `--reps N` for more than one rep (reports the
median across reps, each individually gated).

Set `MLX_BENCH_DIR` if you cloned `mlx-bench` somewhere other than `~/mlx-bench`, or
`--reps`/`--max-tokens`/`--base-url`/`--out` to override the defaults.
