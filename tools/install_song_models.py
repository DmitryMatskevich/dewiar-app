"""Explicit installation of missing Songs models; no settings/voice changes."""
import json
from pathlib import Path
from huggingface_hub import HfApi, snapshot_download

MODELS = {
    'mlx-community/whisper-large-v2-mlx': ['*.json', '*.npz', '*.safetensors'],
    'Qwen/Qwen3-ASR-0.6B': ['*.json', '*.safetensors', '*.txt'],
    'Qwen/Qwen3-ForcedAligner-0.6B': ['*.json', '*.safetensors', '*.txt'],
}

if __name__ == '__main__':
    report = {}
    for repo, patterns in MODELS.items():
        revision = HfApi().model_info(repo).sha
        print('Checking/installing:', repo, revision, flush=True)
        path = snapshot_download(repo, revision=revision, allow_patterns=patterns, max_workers=3)
        report[repo] = {'revision': revision, 'path': path}
        target = Path(__file__).resolve().parents[1]/'work/song-verification/model-install.json'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2)+'\n')
        print('Ready:', repo, flush=True)
