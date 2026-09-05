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
