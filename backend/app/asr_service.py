"""Local Chinese ASR with one resident recognizer and bounded audio segments."""
import gc
from dataclasses import dataclass
from pathlib import Path
import re
import tempfile
import threading

from .config import settings
from .exercise_models import Segment
from .ffmpeg_tools import VideoProcessingError, run_checked


BACKENDS = (
    {"id": "sensevoice", "label": "SenseVoice-Small", "description": "约 2.34 亿参数，中文识别快，适合 CPU"},
    {"id": "qwen3", "label": "Qwen3-ASR-0.6B", "description": "约 6 亿参数，重视中文准确率，CPU 运行较慢"},
    {"id": "whisper", "label": "Whisper（兼容）", "description": "保留原有 faster-whisper 方案"},
)
_lock = threading.Lock()
_model = None
_model_key = None
_vad = None
_vad_key = None


@dataclass
class _QwenRecognizer:
    model: object
    processor: object

    def transcribe(self, samples, sample_rate):
        import torch
        with torch.inference_mode():
            inputs = self.processor.apply_transcription_request(
                audio=samples, language="Chinese", processor_kwargs={"sampling_rate": sample_rate},
            ).to(self.model.device, self.model.dtype)
            output = self.model.generate(**inputs, max_new_tokens=512, do_sample=False)
            generated = output[:, inputs["input_ids"].shape[1]:]
            return self.processor.decode(generated, return_format="transcription_only")[0]


def options():
    return {"default_backend": settings.asr_backend, "backends": list(BACKENDS)}


def validate_backend(backend):
    if backend not in {item["id"] for item in BACKENDS}:
        raise VideoProcessingError("不支持的语音识别模型")
    return backend


def _resolve_model(model):
    """Resolve both vendors to local files, with persistent, separate caches."""
    path = Path(model).expanduser()
    if path.is_dir():
        return str(path.resolve())
    if path.exists() or model.startswith(("/", ".", "~")):
        raise VideoProcessingError(f"模型目录不存在或不是目录：{model}")
    if settings.asr_model_source == "modelscope":
        from modelscope import snapshot_download
        return snapshot_download(model, cache_dir=str(settings.asr_models_dir / "modelscope"))
    from huggingface_hub import snapshot_download
    # ModelScope and Hugging Face publish SenseVoice under different owners.
    repo = {"iic/SenseVoiceSmall": "FunAudioLLM/SenseVoiceSmall",
            "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch": "funasr/fsmn-vad"}.get(model, model)
    return snapshot_download(repo, cache_dir=str(settings.asr_models_dir / "huggingface"))


def _load_model(backend):
    global _model, _model_key
    model_id = {"sensevoice": settings.sensevoice_model, "qwen3": settings.qwen_asr_model, "whisper": settings.whisper_model}[backend]
    key = (backend, model_id, settings.asr_device, settings.asr_model_source,
           str(settings.asr_models_dir), str(settings.whisper_models_dir),
           settings.asr_cpu_threads, settings.whisper_cpu_threads)
    if _model is not None and _model_key == key:
        return _model
    # Release the old recognizer before loading another, including failed loads.
    _model = None
    _model_key = None
    gc.collect()
    try:
        if backend == "whisper":
            from faster_whisper import WhisperModel
            model = WhisperModel(model_id, device="cpu", compute_type="int8",
                                 cpu_threads=settings.whisper_cpu_threads,
                                 download_root=str(settings.whisper_models_dir))
        else:
            import torch
            torch.set_num_threads(settings.asr_cpu_threads)
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                torch.mps.empty_cache()
            local = _resolve_model(model_id)
            if backend == "sensevoice":
                from funasr import AutoModel
                model = AutoModel(model=local, device=settings.asr_device,
                                  ncpu=settings.asr_cpu_threads, disable_update=True,
                                  disable_pbar=True, disable_log=True)
            else:
                from transformers import AutoProcessor, Qwen3ASRForConditionalGeneration
                dtype = torch.float32 if settings.asr_device == "cpu" else torch.float16
                processor = AutoProcessor.from_pretrained(local, local_files_only=True)
                recognizer = Qwen3ASRForConditionalGeneration.from_pretrained(
                    local, dtype=dtype, device_map=settings.asr_device,
                    attn_implementation="eager", local_files_only=True,
                ).eval()
                model = _QwenRecognizer(recognizer, processor)
    except Exception as exc:
        variable = {"sensevoice": "SENSEVOICE_MODEL", "qwen3": "QWEN_ASR_MODEL", "whisper": "WHISPER_MODEL"}[backend]
        source = settings.hf_endpoint if backend == "whisper" or settings.asr_model_source == "huggingface" else "ModelScope"
        raise VideoProcessingError(
            f"语音识别模型加载失败（模型：{model_id}，下载源：{source}）。"
            f"请检查下载源及 HF_ENDPOINT / ASR_MODEL_SOURCE 配置，或通过 {variable} 指定本地模型目录。"
            "若出现 Xet/CAS 401，请设置 HF_HUB_DISABLE_XET=1 后重建容器；"
            f"若提示缺少依赖，请安装 backend/requirements.txt：{exc}"
        ) from exc
    _model, _model_key = model, key
    return model


def _load_vad():
    global _vad, _vad_key
    key = (settings.asr_vad_model, settings.asr_model_source,
           str(settings.asr_models_dir), settings.asr_cpu_threads, settings.asr_segment_seconds)
    if _vad is None or _vad_key != key:
        _vad = None
        _vad_key = None
        try:
            from funasr import AutoModel
            _vad = AutoModel(model=_resolve_model(settings.asr_vad_model), device="cpu",
                             ncpu=settings.asr_cpu_threads, disable_update=True,
                             disable_pbar=True, disable_log=True,
                             max_single_segment_time=settings.asr_segment_seconds * 1000)
        except Exception as exc:
            raise VideoProcessingError(
                f"语音分段模型加载失败（{settings.asr_vad_model}）。"
                f"请检查 ASR_MODEL_SOURCE 或通过 ASR_VAD_MODEL 指定本地目录：{exc}"
            ) from exc
        _vad_key = key
    return _vad


def _speech_ranges(vad, audio, frames, sample_rate):
    """VAD millisecond positions become bounded sample ranges on the original clock."""
    output = vad.generate(input=str(audio), cache={})
    limit = settings.asr_segment_seconds * sample_rate
    previous_end = 0
    for start_ms, end_ms in (output[0].get("value", []) if output else []):
        start = max(previous_end, int(start_ms * sample_rate / 1000))
        end = min(frames, int(end_ms * sample_rate / 1000))
        while start < end:
            stop = min(end, start + limit)
            yield start, stop
            start = stop
        previous_end = max(previous_end, end)


def _recognize(model, backend, samples, sample_rate):
    if backend == "sensevoice":
        result = model.generate(input=samples, cache={}, language="zh", use_itn=True)
        # Keep the transcript without language/emotion/event tags or emoji.
        return re.sub(r"<\|[^|]*\|>", "", result[0].get("text", "")) if result else ""
    return model.transcribe(samples, sample_rate)


def transcribe(path, backend, progress):
    validate_backend(backend)
    progress("waiting_for_transcription", 0)
    with _lock:
        progress("loading_model", 0)
        model = _load_model(backend)
        try:
            if backend == "whisper":
                progress("transcribing", 1)
                segments, info = model.transcribe(str(path), language="zh", vad_filter=True, beam_size=5)
                result = []
                for segment in segments:
                    if segment.text.strip():
                        result.append(Segment(start=segment.start, end=segment.end, text=segment.text).model_dump())
                    progress("transcribing", min(70, int(70 * segment.end / max(info.duration, 1))))
            else:
                import soundfile as sf
                vad = _load_vad()
                with tempfile.TemporaryDirectory(prefix="asr-", dir=settings.exercises_dir) as directory:
                    audio = Path(directory) / "audio.wav"
                    progress("extracting_audio", 0)
                    run_checked(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(path),
                                 "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000",
                                 "-c:a", "pcm_s16le", str(audio)], timeout=3600)
                    progress("detecting_speech", 1)
                    result = []
                    with sf.SoundFile(audio) as stream:
                        for start, end in _speech_ranges(vad, audio, len(stream), stream.samplerate):
                            stream.seek(start)
                            samples = stream.read(end - start, dtype="float32")
                            text = _recognize(model, backend, samples, stream.samplerate).strip()
                            if text:
                                result.append(Segment(start=start / stream.samplerate,
                                                      end=end / stream.samplerate, text=text).model_dump())
                            progress("transcribing", min(70, int(70 * end / max(len(stream), 1))))
        except Exception as exc:
            raise VideoProcessingError(f"语音识别失败：{exc}") from exc
        if not result:
            raise VideoProcessingError("未识别到有效语音，请换一个有讲解内容的视频。")
        return result
