from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
# huggingface_hub reads HF_ENDPOINT at import time, before model downloads start.
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
# Mirrors may return Xet metadata whose CAS endpoint still requires Hub auth.
# Use the regular HTTP downloader; this must precede huggingface_hub imports.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")


class Settings:
    def __init__(self) -> None:
        data_dir = os.getenv("DATA_DIR")
        self.data_dir = Path(data_dir) if data_dir else PROJECT_ROOT / "data"
        self.uploads_dir = self.data_dir / "uploads"
        self.downloads_dir = self.data_dir / "downloads"
        self.outputs_dir = self.data_dir / "outputs"
        self.cookies_dir = self.data_dir / "cookies"
        self.fonts_dir = self.data_dir / "fonts"
        self.summaries_dir = self.data_dir / "summaries"
        self.exercises_dir = self.data_dir / "exercises"
        self.whisper_models_dir = Path(os.getenv("WHISPER_MODEL_DIR", str(self.data_dir / "models")))
        self.whisper_model = os.getenv("WHISPER_MODEL", "small")
        self.hf_endpoint = os.environ["HF_ENDPOINT"]
        self.whisper_cpu_threads = max(1, int(os.getenv("WHISPER_CPU_THREADS", "4")))
        self.asr_backend = os.getenv("ASR_BACKEND", "sensevoice")
        self.asr_device = os.getenv("ASR_DEVICE", "cpu")
        self.asr_cpu_threads = max(1, int(os.getenv("ASR_CPU_THREADS", "4")))
        self.asr_models_dir = Path(os.getenv("ASR_MODEL_DIR", str(self.data_dir / "models")))
        self.asr_model_source = os.getenv("ASR_MODEL_SOURCE", "modelscope")
        self.sensevoice_model = os.getenv("SENSEVOICE_MODEL", "iic/SenseVoiceSmall")
        self.qwen_asr_model = os.getenv("QWEN_ASR_MODEL", "Qwen/Qwen3-ASR-0.6B-hf")
        self.asr_vad_model = os.getenv("ASR_VAD_MODEL", "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch")
        self.asr_segment_seconds = min(30, max(5, int(os.getenv("ASR_SEGMENT_SECONDS", "25"))))
        if self.asr_backend not in {"sensevoice", "qwen3", "whisper"}:
            raise ValueError("ASR_BACKEND 必须为 sensevoice、qwen3 或 whisper")
        if self.asr_model_source not in {"modelscope", "huggingface"}:
            raise ValueError("ASR_MODEL_SOURCE 必须为 modelscope 或 huggingface")
        self.exercise_max_input_chars = max(1000, int(os.getenv("EXERCISE_MAX_INPUT_CHARS", "9000")))
        self.bilibili_cookies_file = os.getenv("BILIBILI_COOKIES_FILE") or os.getenv(
            "BILIBILI_COOKIE_FILE"
        )
        self.bilibili_cookies = os.getenv("BILIBILI_COOKIES") or os.getenv(
            "BILIBILI_COOKIE"
        )
        self.bilibili_cookie_header = os.getenv("BILIBILI_COOKIE_HEADER")
        self.frontend_dist = Path(
            os.getenv("FRONTEND_DIST", str(PROJECT_ROOT / "frontend" / "dist"))
        )
        self.cors_origins = [
            item.strip()
            for item in os.getenv("CORS_ORIGINS", "*").split(",")
            if item.strip()
        ]
        self.openai_api_key = os.getenv("OPENAI_API_KEY", "")
        self.openai_base_url = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com")
        self.openai_model = os.getenv("OPENAI_MODEL", "deepseek-chat")
        self.openai_timeout = int(os.getenv("OPENAI_TIMEOUT", "60"))
        self.summary_max_input_chars = int(os.getenv("SUMMARY_MAX_INPUT_CHARS", "9000"))
        self.bili_rate_limit_seconds = float(os.getenv("BILI_RATE_LIMIT_SECONDS", "1.0"))
        self.site_url = (os.getenv("SITE_URL") or "").strip().rstrip("/")
        self.font_file = self._find_font_file()

    def ensure_dirs(self) -> None:
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        self.outputs_dir.mkdir(parents=True, exist_ok=True)
        self.cookies_dir.mkdir(parents=True, exist_ok=True)
        self.fonts_dir.mkdir(parents=True, exist_ok=True)
        self.summaries_dir.mkdir(parents=True, exist_ok=True)
        self.exercises_dir.mkdir(parents=True, exist_ok=True)
        self.whisper_models_dir.mkdir(parents=True, exist_ok=True)
        self.asr_models_dir.mkdir(parents=True, exist_ok=True)
        self._write_bilibili_cookies_from_env()

    def prepare_bilibili_cookies_file(self) -> Path | None:
        if self.bilibili_cookies_file:
            path = Path(self.bilibili_cookies_file).expanduser()
            if not path.exists():
                raise FileNotFoundError(f"BILIBILI_COOKIES_FILE not found: {path}")
            return path

        self._write_bilibili_cookies_from_env()
        default_path = self.cookies_dir / "bilibili.cookies.txt"
        if default_path.exists():
            return default_path
        return None

    def _write_bilibili_cookies_from_env(self) -> None:
        if not self.bilibili_cookies:
            return

        cookie_text = self.bilibili_cookies
        if "\\n" in cookie_text and "\n" not in cookie_text:
            cookie_text = cookie_text.replace("\\n", "\n")
        if not cookie_text.endswith("\n"):
            cookie_text += "\n"

        target = self.cookies_dir / "bilibili.cookies.txt"
        target.write_text(cookie_text, encoding="utf-8")
        target.chmod(0o600)

    def _find_font_file(self) -> str | None:
        configured = os.getenv("FONT_FILE")
        if configured and Path(configured).exists():
            return configured

        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/Library/Fonts/Arial Unicode.ttf",
        ]
        for candidate in candidates:
            if Path(candidate).exists():
                return candidate
        return None


settings = Settings()
