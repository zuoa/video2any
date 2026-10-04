"""Check download configuration before Hugging Face captures its environment."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("endpoint", [None, "https://huggingface.co", "https://models.example.com"])
def test_hf_endpoint_is_applied_before_downloader_import(endpoint):
    env = os.environ.copy()
    env.pop("HF_ENDPOINT", None)
    if endpoint is not None:
        env["HF_ENDPOINT"] = endpoint
    result = subprocess.run(
        [sys.executable, "-c", (
            "import json; from backend.app.config import settings; "
            "import faster_whisper; from huggingface_hub import constants; "
            "print(json.dumps([settings.hf_endpoint, constants.ENDPOINT]))"
        )],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    expected = endpoint or "https://hf-mirror.com"
    assert json.loads(result.stdout) == [expected, expected]
