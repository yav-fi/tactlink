"""Download and verify the planner's local CPU speech model (run once)."""
from pathlib import Path
from faster_whisper import WhisperModel

cache = Path(__file__).resolve().parents[1] / 'models' / 'speech'
WhisperModel('base.en', device='cpu', compute_type='int8', cpu_threads=4, download_root=str(cache))
print(f'Local base.en speech model ready in {cache}')
