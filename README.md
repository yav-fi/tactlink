# dnhacks26

Start by cloning the repository:

```sh
git clone https://github.com/yav-fi/dnhacks26.git
```

Yavin added `AGENTS.md` and `CLAUDE.md` to keep coding-agent instructions consistent across tools.

## Setup

```sh
pip install -U huggingface_hub mlx-vlm uv chad-code
git clone https://github.com/kaarelkaarelson/mlx-bench.git ~/mlx-bench
./scripts/download_model.sh
./scripts/build_dflash_draft.sh
```

## Commands

```sh
./scripts/start_chat_server.sh   # spin up the inference server
./scripts/ask.sh "question"      # ask it something (no arg = interactive chat)
./scripts/benchmark.sh           # thermally-gated tok/s benchmark
```
