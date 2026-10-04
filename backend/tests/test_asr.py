"""ASR boundaries, vendor interfaces, model lifetime, and offline loading."""
from pathlib import Path
from contextlib import nullcontext
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
import soundfile as sf

from backend.app import asr_service as asr
from backend.app.config import settings
from backend.app.ffmpeg_tools import VideoProcessingError


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "exercises_dir", tmp_path)
    monkeypatch.setattr(settings, "asr_models_dir", tmp_path / "models")
    monkeypatch.setattr(settings, "asr_device", "cpu")
    monkeypatch.setattr(settings, "asr_model_source", "modelscope")
    for name in ("_model", "_model_key", "_vad", "_vad_key"):
        monkeypatch.setattr(asr, name, None)


@pytest.fixture
def fake_torch(monkeypatch):
    torch = SimpleNamespace(set_num_threads=Mock(), float32="fp32", float16="fp16",
                            inference_mode=nullcontext,
                            cuda=SimpleNamespace(is_available=lambda: False),
                            backends=SimpleNamespace())
    monkeypatch.setitem(sys.modules, "torch", torch)
    return torch


def test_local_model_loading_never_downloads(tmp_path, monkeypatch):
    downloader = Mock(side_effect=AssertionError("must stay offline"))
    monkeypatch.setitem(sys.modules, "modelscope", SimpleNamespace(snapshot_download=downloader))
    assert asr._resolve_model(str(tmp_path)) == str(tmp_path.resolve())
    downloader.assert_not_called()
    with pytest.raises(VideoProcessingError, match="目录不存在"):
        asr._resolve_model(str(tmp_path / "missing"))


@pytest.mark.parametrize("model,repo", [
    ("iic/SenseVoiceSmall", "FunAudioLLM/SenseVoiceSmall"),
    ("iic/speech_fsmn_vad_zh-cn-16k-common-pytorch", "funasr/fsmn-vad"),
    ("Qwen/Qwen3-ASR-0.6B-hf", "Qwen/Qwen3-ASR-0.6B-hf"),
])
def test_huggingface_uses_correct_vendor_ids_and_persistent_cache(model, repo, monkeypatch):
    monkeypatch.setattr(settings, "asr_model_source", "huggingface")
    downloader = Mock(return_value="/models/local")
    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=downloader))
    assert asr._resolve_model(model) == "/models/local"
    downloader.assert_called_once_with(repo, cache_dir=str(settings.asr_models_dir / "huggingface"))


def test_recognizer_switch_releases_old_model_and_failed_load_can_retry(monkeypatch, fake_torch):
    sense = Mock()
    qwen = Mock()
    qwen.eval.return_value = qwen
    processor = Mock()
    sense_constructor = Mock(return_value=sense)
    qwen_constructor = Mock(side_effect=[ConnectionError("401"), qwen])
    monkeypatch.setattr(asr, "_resolve_model", lambda name: "/models/" + name)
    monkeypatch.setitem(sys.modules, "funasr", SimpleNamespace(AutoModel=sense_constructor))
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(
        Qwen3ASRForConditionalGeneration=SimpleNamespace(from_pretrained=qwen_constructor),
        AutoProcessor=SimpleNamespace(from_pretrained=Mock(return_value=processor)),
    ))
    assert asr._load_model("sensevoice") is sense
    assert asr._load_model("sensevoice") is sense
    assert sense_constructor.call_count == 1
    with pytest.raises(VideoProcessingError, match="QWEN_ASR_MODEL"):
        asr._load_model("qwen3")
    assert asr._model is None and asr._model_key is None
    assert asr._load_model("qwen3").model is qwen
    assert qwen_constructor.call_args.kwargs["dtype"] == fake_torch.float32
    assert qwen_constructor.call_args.kwargs["device_map"] == "cpu"
    assert qwen_constructor.call_args.kwargs["local_files_only"] is True
    assert asr._load_model("sensevoice") is sense
    assert sense_constructor.call_count == 2


def test_vad_is_cached_and_failed_initialization_can_retry(monkeypatch):
    vad = Mock()
    constructor = Mock(side_effect=[ConnectionError("network"), vad])
    monkeypatch.setattr(asr, "_resolve_model", lambda name: "/local/vad")
    monkeypatch.setitem(sys.modules, "funasr", SimpleNamespace(AutoModel=constructor))
    with pytest.raises(VideoProcessingError, match="ASR_VAD_MODEL"):
        asr._load_vad()
    assert asr._vad is None
    assert asr._load_vad() is vad
    assert asr._load_vad() is vad
    assert constructor.call_count == 2


def test_speech_ranges_bound_long_segments_and_preserve_clock(monkeypatch):
    monkeypatch.setattr(settings, "asr_segment_seconds", 5)
    vad = SimpleNamespace(generate=lambda **kwargs: [{"value": [[2000, 15000], [14000, 19000]]}])
    ranges = list(asr._speech_ranges(vad, Path("audio.wav"), 18000 * 16, 16000))
    assert ranges == [(32000, 112000), (112000, 192000), (192000, 240000), (240000, 288000)]


@pytest.mark.parametrize("backend", ["sensevoice", "qwen3"])
def test_transcription_uses_real_audio_slices_and_cleans_temporary_files(tmp_path, monkeypatch, backend):
    source = tmp_path / "source.wav"
    sf.write(source, np.full(16000 * 4, .1, dtype=np.float32), 16000)
    vad = SimpleNamespace(generate=Mock(return_value=[{"value": [[500, 1500], [2500, 3800]]}]))
    if backend == "sensevoice":
        model = SimpleNamespace(generate=Mock(side_effect=[
            [{"text": "<|zh|><|NEUTRAL|><|Speech|><|withitn|>你好。"}], [{"text": "后半段"}],
        ]))
    else:
        model = SimpleNamespace(transcribe=Mock(side_effect=["你好。", "后半段"]))
    monkeypatch.setattr(asr, "_load_model", lambda name: model)
    monkeypatch.setattr(asr, "_load_vad", lambda: vad)
    progress = Mock()
    result = asr.transcribe(source, backend, progress)
    assert result == [{"start": .5, "end": 1.5, "text": "你好。"}, {"start": 2.5, "end": 3.8, "text": "后半段"}]
    calls = (model.generate if backend == "sensevoice" else model.transcribe).call_args_list
    for call, frames in zip(calls, [16000, 20800]):
        samples = call.kwargs["input"] if backend == "sensevoice" else call.args[0]
        assert len(samples) == frames
        assert samples.dtype == np.float32
        assert samples.mean() == pytest.approx(.1, abs=.001)
    if backend == "qwen3":
        assert calls[0].args[1] == 16000
    else:
        assert calls[0].kwargs["language"] == "zh"
    assert not list(tmp_path.glob("asr-*"))
    assert all(0 <= call.args[1] <= 70 for call in progress.call_args_list)


def test_silent_audio_reports_no_speech_and_cleans_temporary_files(tmp_path, monkeypatch):
    source = tmp_path / "silence.wav"
    sf.write(source, np.zeros(16000, dtype=np.float32), 16000)
    monkeypatch.setattr(asr, "_load_model", lambda name: Mock())
    monkeypatch.setattr(asr, "_load_vad", lambda: SimpleNamespace(generate=lambda **kw: [{"value": []}]))
    with pytest.raises(VideoProcessingError, match="有效语音"):
        asr.transcribe(source, "qwen3", Mock())
    assert not list(tmp_path.glob("asr-*"))


def test_unsupported_model_is_rejected_before_loading(monkeypatch):
    loader = Mock()
    monkeypatch.setattr(asr, "_load_model", loader)
    with pytest.raises(VideoProcessingError, match="不支持"):
        asr.transcribe(Path("video.mp4"), "unknown", Mock())
    loader.assert_not_called()


def test_qwen_native_processor_forces_chinese_and_excludes_prompt_tokens(fake_torch):
    class Inputs(dict):
        def to(self, device, dtype):
            assert device == "cpu" and dtype == "fp32"
            return self
    inputs = Inputs(input_ids=np.array([[10, 11, 12, 13]]))
    processor = SimpleNamespace(apply_transcription_request=Mock(return_value=inputs),
                                decode=Mock(return_value=["识别结果"]))
    model = SimpleNamespace(device="cpu", dtype="fp32",
                            generate=Mock(return_value=np.array([[10, 11, 12, 13, 20, 21]])))
    recognizer = asr._QwenRecognizer(model, processor)
    audio = np.ones(16000, dtype=np.float32)
    assert recognizer.transcribe(audio, 16000) == "识别结果"
    assert processor.apply_transcription_request.call_args.kwargs["language"] == "Chinese"
    assert processor.apply_transcription_request.call_args.kwargs["processor_kwargs"]["sampling_rate"] == 16000
    assert processor.decode.call_args.kwargs["return_format"] == "transcription_only"
    np.testing.assert_array_equal(processor.decode.call_args.args[0], [[20, 21]])
