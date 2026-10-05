"""Check timeout phases and SDK retries without calling a model service."""
import logging
import json

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
    monkeypatch.setattr(settings, "openai_reasoning_effort", "")
    monkeypatch.setattr(settings, "openai_thinking_type", "")
    monkeypatch.setattr(settings, "openai_reasoning_max_tokens", 16384)
    monkeypatch.setattr(settings, "openai_json_mode", True)

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


def test_glm_53_uses_low_effort_and_json_mode_with_reasoning_budget(mock_llm, monkeypatch, caplog):
    monkeypatch.setattr(settings, "openai_model", "glm-5.3-flash")
    bodies = []
    def handler(request):
        bodies.append(json.loads(request.content))
        value = completion()
        value["choices"][0]["message"]["reasoning_content"] = "思考过程不应打印"
        value["usage"] = {"prompt_tokens": 10, "completion_tokens": 5000, "total_tokens": 5010}
        return httpx.Response(200, json=value)
    mock_llm(handler)
    with caplog.at_level(logging.WARNING):
        result = summary_service._chat("返回 JSON", max_tokens=5000, json_mode=True)
    assert bodies[0]["reasoning_effort"] == "low"
    assert bodies[0]["max_tokens"] == 16384
    assert bodies[0]["response_format"] == {"type": "json_object"}
    assert "thinking" not in bodies[0]
    assert result.finish_reason == "stop" and result.max_tokens == 16384
    assert "completion_tokens=5000" in caplog.text
    assert "reasoning_chars=" in caplog.text
    assert "思考过程不应打印" not in caplog.text


def test_glm_47_can_disable_thinking(mock_llm, monkeypatch):
    monkeypatch.setattr(settings, "openai_model", "glm-4.7-flash")
    monkeypatch.setattr(settings, "openai_thinking_type", "disabled")
    bodies = []
    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=completion())
    mock_llm(handler)
    summary_service._chat("返回 JSON", max_tokens=5000, json_mode=True)
    assert bodies[0]["thinking"] == {"type": "disabled"}
    assert bodies[0]["max_tokens"] == 5000
    assert "reasoning_effort" not in bodies[0]


def test_glm_53_rejects_unsupported_disabled_setting_before_request(mock_llm, monkeypatch):
    monkeypatch.setattr(settings, "openai_model", "glm-5.3-flash")
    monkeypatch.setattr(settings, "openai_thinking_type", "disabled")
    def handler(request):
        pytest.fail("must not send an unsupported setting")
    mock_llm(handler)
    with pytest.raises(VideoProcessingError, match="不支持关闭思考"):
        summary_service._chat("返回 JSON")


def test_reasoning_effort_and_budget_can_be_overridden(mock_llm, monkeypatch):
    monkeypatch.setattr(settings, "openai_model", "glm-5.3-flash")
    monkeypatch.setattr(settings, "openai_reasoning_effort", "high")
    monkeypatch.setattr(settings, "openai_reasoning_max_tokens", 24000)
    bodies = []
    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=completion())
    mock_llm(handler)
    summary_service._chat("返回 JSON", max_tokens=5000)
    assert bodies[0]["reasoning_effort"] == "high"
    assert bodies[0]["max_tokens"] == 24000


def test_unsupported_json_mode_falls_back_once_per_model(mock_llm):
    bodies = []
    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if "response_format" in body:
            return httpx.Response(400, json={"error": {"message": "response_format json_object is unsupported"}})
        return httpx.Response(200, json=completion())
    mock_llm(handler)
    assert summary_service._chat("返回 JSON", json_mode=True) == "答案"
    assert summary_service._chat("返回 JSON", json_mode=True) == "答案"
    assert len(bodies) == 3
    assert "response_format" in bodies[0]
    assert all("response_format" not in body for body in bodies[1:])


def test_json_mode_can_be_disabled_for_provider(mock_llm, monkeypatch):
    monkeypatch.setattr(settings, "openai_json_mode", False)
    def handler(request):
        assert "response_format" not in json.loads(request.content)
        return httpx.Response(200, json=completion())
    mock_llm(handler)
    assert summary_service._chat("返回 JSON", json_mode=True) == "答案"


def test_unrelated_http_400_does_not_trigger_json_fallback(mock_llm):
    attempts = []
    def handler(request):
        attempts.append(request)
        return httpx.Response(400, json={"error": {"message": "max_tokens too large"}})
    mock_llm(handler)
    with pytest.raises(VideoProcessingError):
        summary_service._chat("返回 JSON", json_mode=True)
    assert len(attempts) == 1


@pytest.mark.parametrize("model, expected_limits", [
    ("glm-5.3-flash", [16384, 32768]),
    ("test-model", [5000, 8192]),
])
def test_structured_truncation_retries_with_more_tokens_and_failed_response_history(mock_llm, monkeypatch, model, expected_limits):
    from backend.app import exercise_service
    monkeypatch.setattr(settings, "openai_model", model)
    bodies = []
    def handler(request):
        bodies.append(json.loads(request.content))
        response = completion()
        # Even syntactically valid output must be rejected if completion was cut.
        response["choices"][0]["message"]["content"] = '{"value": 1}' if len(bodies) == 1 else '{"value": 42}'
        response["choices"][0]["finish_reason"] = "length" if len(bodies) == 1 else "stop"
        return httpx.Response(200, json=response)
    mock_llm(handler)
    history = [{"role": "system", "content": exercise_service.RULES}]
    assert exercise_service._structured("返回 JSON", lambda value: value["value"], history=history) == 42
    assert [body["max_tokens"] for body in bodies] == expected_limits
    assert bodies[1]["messages"][-2] == {"role": "assistant", "content": '{"value": 1}'}
    assert "被截断" in bodies[1]["messages"][-1]["content"]
    assert "不要只续写" in bodies[1]["messages"][-1]["content"]
    assert history[-1] == {"role": "assistant", "content": '{"value": 42}'}
    assert len(history) == 3


def test_knowledge_style_call_without_history_repairs_with_failed_response(mock_llm):
    from backend.app import exercise_service
    bodies = []
    def handler(request):
        bodies.append(json.loads(request.content))
        response = completion()
        response["choices"][0]["message"]["content"] = "broken JSON" if len(bodies) == 1 else '{"value": 42}'
        return httpx.Response(200, json=response)
    mock_llm(handler)
    assert exercise_service._structured("返回 JSON", lambda value: value["value"]) == 42
    assert bodies[1]["messages"][-2] == {"role": "assistant", "content": "broken JSON"}
    assert all(body["response_format"] == {"type": "json_object"} for body in bodies)


def test_repeated_truncation_reports_limit_and_stops_after_one_repair(mock_llm, monkeypatch):
    from backend.app import exercise_service
    monkeypatch.setattr(settings, "openai_model", "glm-5.3-flash")
    attempts = []
    def handler(request):
        attempts.append(request)
        response = completion()
        response["choices"][0]["message"]["content"] = '{"value":'
        response["choices"][0]["finish_reason"] = "length"
        return httpx.Response(200, json=response)
    mock_llm(handler)
    with pytest.raises(VideoProcessingError, match="32768 token 上限被截断"):
        exercise_service._structured("返回 JSON", lambda value: value["value"])
    assert len(attempts) == 2


def test_reasoning_configuration_environment_overrides(monkeypatch):
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "low")
    monkeypatch.setenv("OPENAI_REASONING_MAX_TOKENS", "24000")
    monkeypatch.setenv("OPENAI_THINKING_TYPE", "disabled")
    monkeypatch.setenv("OPENAI_JSON_MODE", "false")
    configured = Settings()
    assert configured.openai_reasoning_effort == "low"
    assert configured.openai_reasoning_max_tokens == 24000
    assert configured.openai_thinking_type == "disabled"
    assert configured.openai_json_mode is False
    monkeypatch.setenv("OPENAI_THINKING_TYPE", "off")
    with pytest.raises(ValueError, match="OPENAI_THINKING_TYPE"):
        Settings()


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
