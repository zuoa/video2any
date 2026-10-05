"""Check timeout phases and SDK retries without calling a model service."""
import logging

import httpx
import pytest

from backend.app import summary_service
from backend.app.config import Settings, settings
from backend.app.ffmpeg_tools import VideoProcessingError


@pytest.fixture
def mock_llm(monkeypatch):
    original = summary_service.OpenAI
    clients = []
    monkeypatch.setattr(summary_service, "_client", None)
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "openai_base_url", "https://llm.example/v1")
    monkeypatch.setattr(settings, "openai_model", "test-model")
    monkeypatch.setattr(settings, "openai_timeout", 240)

    def configure(handler):
        def factory(**kwargs):
            client = original(http_client=httpx.Client(transport=httpx.MockTransport(handler)), **kwargs)
            clients.append(client)
            return client
        monkeypatch.setattr(summary_service, "OpenAI", factory)
    yield configure
    for client in clients:
        client.close()


def completion():
    return {"id": "test", "object": "chat.completion", "created": 0, "model": "test-model",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "答案"}, "finish_reason": "stop"}]}


def test_long_response_wait_preserves_short_connection_timeout_and_logs_elapsed(mock_llm, caplog):
    timeouts = []
    def handler(request):
        timeouts.append(request.extensions["timeout"])
        return httpx.Response(200, json=completion())
    mock_llm(handler)
    with caplog.at_level(logging.WARNING):
        assert summary_service._chat("讲课原文", max_tokens=7000) == "答案"
    assert timeouts == [{"connect": 10, "read": 240, "write": 30, "pool": 10}]
    assert "read_timeout=240s" in caplog.text
    assert "max_retries=2" in caplog.text
    assert "LLM response" in caplog.text and "elapsed=" in caplog.text
    assert "test-key" not in caplog.text and "讲课原文" not in caplog.text


def test_chat_sends_complete_history_without_mutating_it(mock_llm, caplog):
    import json
    bodies = []
    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=completion())
    mock_llm(handler)
    history = [
        {"role": "system", "content": "出题要求"},
        {"role": "user", "content": "生成第一批"},
        {"role": "assistant", "content": '{"questions":[{"stem":"题干","answer":"完整答案","explanation":"完整解析"}]}'},
    ]
    original = [dict(message) for message in history]
    with caplog.at_level(logging.WARNING):
        assert summary_service._chat("继续第二批", history=history) == "答案"
    assert bodies[0]["messages"] == original + [{"role": "user", "content": "继续第二批"}]
    assert history == original
    assert "history_messages=3" in caplog.text
    assert "完整解析" not in caplog.text


@pytest.mark.parametrize("timeout_type, expected_hint", [
    (httpx.ReadTimeout, "等待大模型响应超时（OPENAI_TIMEOUT=240 秒）"),
    (httpx.ConnectTimeout, "连接大模型服务超时"),
])
def test_sdk_retries_timeouts_twice_and_reports_actual_phase(mock_llm, caplog, timeout_type, expected_hint):
    attempts = []
    def handler(request):
        attempts.append(request)
        raise timeout_type("timed out", request=request)
    mock_llm(handler)
    with pytest.raises(VideoProcessingError, match=expected_hint):
        summary_service._chat("prompt")
    assert len(attempts) == 3
    assert "error_type=APITimeoutError" in caplog.text
    assert f"cause={timeout_type.__name__}" in caplog.text


@pytest.mark.parametrize("status, attempts, hint", [
    (429, 3, "限流或额度不足"), (503, 3, "服务暂时异常"), (404, 1, "请检查 OPENAI_BASE_URL"),
])
def test_http_retries_and_failure_messages_distinguish_upstream_errors(mock_llm, status, attempts, hint):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"message": "test error"}}, headers={"retry-after-ms": "1"})
    mock_llm(handler)
    with pytest.raises(VideoProcessingError, match=hint):
        summary_service._chat("prompt")
    assert len(calls) == attempts


def test_read_timeout_default_and_explicit_environment_override(monkeypatch):
    monkeypatch.delenv("OPENAI_TIMEOUT", raising=False)
    assert Settings().openai_timeout == 300
    monkeypatch.setenv("OPENAI_TIMEOUT", "600")
    assert Settings().openai_timeout == 600
