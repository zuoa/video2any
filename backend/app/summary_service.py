"""Video summarization over Bilibili subtitles (LLM, map-reduce for long videos).

Unlike the dfb slice summary (which truncates to 12k chars and produces a
2-3 sentence blurb), this splits the full subtitle by token budget on subtitle-
line boundaries, summarizes each chunk, then synthesizes an overall summary —
so a 60-minute video is summarized in full, not truncated.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx

from .bili_subtitle import (
    NoSubtitleError,
    VideoProcessingError,
    fetch_subtitle,
    format_timestamp,
    parse_timestamp_to_seconds,
)
from .config import settings
from . import summary_store

logger = logging.getLogger(__name__)

try:
    from openai import OpenAI, APITimeoutError, APIConnectionError
except ImportError:  # pragma: no cover - dependency declared in requirements
    OpenAI = None  # type: ignore[assignment]
    APITimeoutError = APIConnectionError = None


class SummaryConfigError(VideoProcessingError):
    """Raised when the LLM is not configured (e.g. missing API key)."""


class LLMResponse(str):
    """A string with completion metadata, so callers can reject truncation."""

    def __new__(cls, content: str, finish_reason: str | None, max_tokens: int):
        value = super().__new__(cls, content)
        value.finish_reason = finish_reason
        value.max_tokens = max_tokens
        return value


_client = None
_client_configuration = None
_client_lock = threading.Lock()


def _get_client():
    if OpenAI is None:
        raise SummaryConfigError("openai 包未安装，无法调用大模型。")
    global _client, _client_configuration
    configuration = settings.runtime_snapshot("openai_api_key", "openai_base_url", "openai_timeout")
    with _client_lock:
        if _client is None or configuration != _client_configuration:
            _client = OpenAI(
                api_key=configuration[0], base_url=configuration[1],
                timeout=httpx.Timeout(configuration[2], connect=10, write=30, pool=10), max_retries=2,
            )
            _client_configuration = configuration
        return _client


# --- Prompts ---

_SINGLE_PROMPT = (
    "你是一名专业的视频内容编辑，擅长像「AI 课代表」那样提炼 B 站视频精华。"
    "下面是一支视频当前分P的完整带时间戳字幕，请用中文产出结构化总结。\n\n"
    "要求：\n"
    "【视频总结 overall_summary】\n"
    "- 3-6 句话（可分段），讲清：视频主题是什么、按什么顺序讲了哪些主要内容、核心结论或价值、适合什么人看。\n"
    "- 直接进入内容，不要用“这个视频”“本期视频主要”“以下是对视频的总结”等元信息式开头。\n"
    "- 不要出现“字幕”“视频”“切片”等字眼。\n\n"
    "【关键内容点 key_points】\n"
    "- 挑选 {kp_count} 个最值得标注的内容点，按时间先后排序，尽量分散在不同时间段。\n"
    "- time 必须是字幕里实际出现过的 MM:SS 或 HH:MM:SS，不要编造时间点。\n"
    "- title 是 8-16 字的小标题；detail 是 1-2 句话说明这个时间点讲了什么。\n\n"
    "【金句/知识点 quotes】\n"
    "- 提炼 3-8 条值得记住的金句或核心知识点，每条一句话；可为空数组。\n\n"
    "严格输出 JSON，不要 Markdown、不要代码块：\n"
    '{{"overall_summary":"...","key_points":[{{"time":"02:15","title":"...","detail":"..."}}],"quotes":["..."]}}\n\n'
    "当前分P：P{page_no}{page_title}\n"
    "字幕内容：\n{subtitle}"
)

_CHUNK_PROMPT = (
    "你是一名专业的视频内容编辑。下面是一支视频某个时间段（第 {index}/{total} 段）的带时间戳字幕，请用中文提炼这一段。\n\n"
    "要求：\n"
    "- section_summary：2-4 句话概括这一段内容，直接进入内容，不要“这段视频”式开头。\n"
    "- key_points：挑选这段里 {kp_count} 个值得标注的内容点，time 必须是字幕里实际出现过的 MM:SS 或 HH:MM:SS，"
    "title 8-16 字，detail 1-2 句，按时间排序。\n"
    "- quotes：这一段里值得记住的金句/知识点 0-5 条，可为空数组。\n\n"
    "严格输出 JSON，不要 Markdown、不要代码块：\n"
    '{{"section_summary":"...","key_points":[{{"time":"...","title":"...","detail":"..."}}],"quotes":["..."]}}\n\n'
    "字幕内容：\n{subtitle}"
)

_SYNTHESIS_PROMPT = (
    "你是一名专业的视频内容编辑。下面是一支视频按时间段切分后、每段的摘要与关键内容点（已带绝对时间戳）。"
    "请综合成整支视频的结构化总结。\n\n"
    "要求：\n"
    "【视频总结 overall_summary】3-6 句话，讲清主题、主要内容脉络、核心结论、适合人群。"
    "直接进入内容，不要“这个视频”式开头。\n"
    "【关键内容点 key_points】从所有段落里挑选最多 {kp_count} 个最重要、时间上最分散的内容点，按时间排序，去重合并。"
    "time 保持原 MM:SS，title 8-16 字，detail 1-2 句。\n"
    "【金句/知识点 quotes】合并去重后 3-8 条，可为空数组。\n\n"
    "严格输出 JSON，不要 Markdown、不要代码块：\n"
    '{{"overall_summary":"...","key_points":[{{"time":"...","title":"...","detail":"..."}}],"quotes":["..."]}}\n\n'
    "各段摘要与关键点：\n{chunks_brief}"
)

_BOILERPLATE_PATTERNS = (
    re.compile(r"^这个视频[，。,:：\s]*"),
    re.compile(r"^本期视频[主要]*[，。,:：\s]*"),
    re.compile(r"^该视频[主要]*[，。,:：\s]*"),
    re.compile(r"^以下[是对于]*[对这个]*视频的总结[，。,:：\s]*"),
    re.compile(r"^这是一个(直播)?(视频|切片)[，。,:：\s]*"),
    re.compile(r"^主要(记录了|讲了|介绍了)[，。,:：\s]*"),
)

_TS_RE = re.compile(r"\[(\d{1,3}:\d{2}(?::\d{2})?)\]")


# --- Parsing & normalization ---

def _clean_summary(summary: str) -> str:
    summary = (summary or "").strip()
    for pattern in _BOILERPLATE_PATTERNS:
        summary = pattern.sub("", summary).strip()
    return summary


def _extract_json_object(text_value: str) -> dict | None:
    text_value = (text_value or "").strip()
    if not text_value:
        return None
    if text_value.startswith("```"):
        text_value = re.sub(r"^```(?:json)?\s*", "", text_value, flags=re.IGNORECASE)
        text_value = re.sub(r"\s*```$", "", text_value)
    try:
        parsed = json.loads(text_value)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    start = text_value.find("{")
    end = text_value.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(text_value[start:end + 1])
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _parse_summary_response(content: str) -> tuple[str, list, list]:
    """Return (summary_text, raw_key_points, raw_quotes)."""
    parsed = _extract_json_object(content)
    if parsed:
        summary = (
            parsed.get("overall_summary")
            or parsed.get("summary")
            or parsed.get("section_summary")
            or parsed.get("摘要")
            or ""
        )
        points = (
            parsed.get("key_points")
            or parsed.get("highlights")
            or parsed.get("关键内容点")
            or []
        )
        quotes = parsed.get("quotes") or parsed.get("金句") or []
        return _clean_summary(str(summary)), points, quotes

    return _clean_summary(content), [], []


def _bili_video_url(bv: str, page: int, seconds: int | None) -> str:
    url = f"https://www.bilibili.com/video/{bv}"
    params: list[str] = []
    if page and page > 1:
        params.append(f"p={page}")
    if seconds is not None:
        params.append(f"t={max(0, int(seconds))}")
    return f"{url}?{'&'.join(params)}" if params else url


def _normalize_key_points(raw_list, bv: str, page: int, cap: int = 20) -> list[dict]:
    points: list[dict] = []
    seen: set[tuple[int, str]] = set()
    for raw in raw_list or []:
        if not isinstance(raw, dict):
            continue
        seconds = parse_timestamp_to_seconds(
            raw.get("time") or raw.get("timestamp") or raw.get("start") or raw.get("seconds")
        )
        if seconds is None:
            continue
        title = re.sub(r"\s+", " ", str(raw.get("title") or raw.get("heading") or "")).strip()
        detail = re.sub(
            r"\s+", " ",
            str(raw.get("detail") or raw.get("description") or raw.get("content") or ""),
        ).strip()
        if not title and not detail:
            continue
        key = (seconds, title)
        if key in seen:
            continue
        seen.add(key)
        points.append({
            "time": format_timestamp(seconds),
            "seconds": seconds,
            "title": title[:40],
            "detail": detail[:160],
            "url": _bili_video_url(bv, page, seconds),
        })
    points.sort(key=lambda p: p["seconds"])
    return points[:cap]


def _normalize_quotes(raw_quotes, cap: int = 8) -> list[str]:
    quotes: list[str] = []
    seen: set[str] = set()
    for raw in raw_quotes or []:
        text_value = re.sub(r"\s+", " ", str(raw or "")).strip(" -—：「」“”\"'")
        if not text_value or text_value in seen:
            continue
        seen.add(text_value)
        quotes.append(text_value[:120])
        if len(quotes) >= cap:
            break
    return quotes


# --- Chunking & counting ---

def _compute_highlight_count(seconds: int | None, cap: int = 16) -> int:
    if not seconds or seconds <= 0:
        return 4
    minutes = seconds / 60
    return max(3, min(cap, round(minutes / 3) + 2))


def _split_timeline(timeline: str, max_chars: int) -> list[str]:
    lines = timeline.split("\n")
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in lines:
        line_len = len(line) + 1
        if current and current_len + line_len > max_chars:
            chunks.append("\n".join(current))
            current = [line]
            current_len = line_len
        else:
            current.append(line)
            current_len += line_len
    if current:
        chunks.append("\n".join(current))
    return chunks


def _chunk_span_seconds(chunk: str) -> int | None:
    times = _TS_RE.findall(chunk)
    if not times:
        return None
    first = parse_timestamp_to_seconds(times[0])
    last = parse_timestamp_to_seconds(times[-1])
    if first is None or last is None:
        return None
    return max(1, last - first)


# --- LLM calls ---

def _chat(prompt: str, max_tokens: int = 1200, *, history: list[dict[str, str]] | None = None, json_mode: bool = False) -> str:
    messages = [dict(message) for message in (history or [])]
    messages.append({"role": "user", "content": prompt})
    client = _get_client()
    # GLM-5.3 cannot disable thinking. Its default effort is max; use low for
    # these extraction/generation tasks and reserve tokens for reasoning too.
    glm_reasoning = settings.openai_model.lower().startswith("glm-5.3")
    if glm_reasoning and settings.openai_thinking_type == "disabled":
        raise SummaryConfigError("GLM-5.3 不支持关闭思考。请清空 OPENAI_THINKING_TYPE 并使用 OPENAI_REASONING_EFFORT=low，或换用支持非思考模式的模型。")
    reasoning_effort = settings.openai_reasoning_effort or ("low" if glm_reasoning else "")
    if glm_reasoning:
        max_tokens = max(max_tokens, settings.openai_reasoning_max_tokens)
    options = {}
    if reasoning_effort:
        options["extra_body"] = {"reasoning_effort": reasoning_effort}
    if settings.openai_thinking_type:
        options.setdefault("extra_body", {})["thinking"] = {"type": settings.openai_thinking_type}
    text_only_models = getattr(client, "_video2any_text_only_models", set())
    if json_mode and settings.openai_json_mode and settings.openai_model not in text_only_models:
        options["response_format"] = {"type": "json_object"}
    # The OpenAI SDK appends "/chat/completions" to base_url; log the full URL
    # so a 404 (wrong OPENAI_BASE_URL / OPENAI_MODEL) is easy to diagnose.
    endpoint = str(client.base_url).rstrip("/") + "/chat/completions"
    request_id = uuid.uuid4().hex[:8]
    started = time.monotonic()
    logger.warning(
        "LLM request [%s]: POST %s | model=%s | max_tokens=%d | prompt_chars=%d | history_messages=%d | read_timeout=%ss | max_retries=%s | reasoning_effort=%s | thinking=%s | json_mode=%s",
        request_id, endpoint, settings.openai_model, max_tokens,
        sum(len(message["content"]) for message in messages), len(messages) - 1,
        settings.openai_timeout, client.max_retries, reasoning_effort or "default",
        settings.openai_thinking_type or "default", "response_format" in options,
    )
    try:
        def complete():
            return client.chat.completions.create(
                model=settings.openai_model, messages=messages,
                max_tokens=max_tokens, temperature=0.3, **options,
            )
        try:
            resp = complete()
        except Exception as exc:
            message = str(exc).lower()
            unsupported_format = (
                getattr(exc, "status_code", None) in {400, 422}
                and "response_format" in options
                and ("response_format" in message or "json_object" in message)
                and any(word in message for word in ("unsupported", "not support", "unknown", "unrecognized", "不支持"))
            )
            if not unsupported_format:
                raise
            logger.warning("LLM [%s]: JSON mode unsupported for model=%s; falling back to prompted JSON", request_id, settings.openai_model)
            options.pop("response_format")
            text_only_models.add(settings.openai_model)
            client._video2any_text_only_models = text_only_models
            resp = complete()
        content = resp.choices[0].message.content or ""
        reasoning = getattr(resp.choices[0].message, "reasoning_content", None) or ""
        logger.warning(
            "LLM response [%s]: model=%s | elapsed=%.1fs | output_chars=%d | reasoning_chars=%d | completion_tokens=%s | finish_reason=%s",
            request_id, settings.openai_model, time.monotonic() - started, len(content), len(reasoning),
            getattr(resp.usage, "completion_tokens", None), resp.choices[0].finish_reason,
        )
        return LLMResponse(content, resp.choices[0].finish_reason, max_tokens)
    except Exception as exc:  # noqa: BLE001 - surface as a user-facing error
        logger.error(
            "LLM call FAILED [%s]: POST %s | model=%s | elapsed=%.1fs | error_type=%s | cause=%s | status=%s | %s",
            request_id, endpoint, settings.openai_model, time.monotonic() - started,
            type(exc).__name__, type(exc.__cause__).__name__, getattr(exc, "status_code", None), exc,
        )
        if APITimeoutError is not None and isinstance(exc, APITimeoutError):
            if isinstance(exc.__cause__, httpx.ConnectTimeout):
                hint = "连接大模型服务超时，请检查网络、代理及 OPENAI_BASE_URL。"
            elif isinstance(exc.__cause__, (httpx.WriteTimeout, httpx.PoolTimeout)):
                hint = "发送请求或等待连接超时，请检查网络及服务负载。"
            else:
                hint = f"等待大模型响应超时（OPENAI_TIMEOUT={settings.openai_timeout} 秒）。可提高 OPENAI_TIMEOUT 后重启服务再试。"
        elif APIConnectionError is not None and isinstance(exc, APIConnectionError):
            hint = "无法连接大模型服务，请检查网络、代理及 OPENAI_BASE_URL。"
        elif getattr(exc, "status_code", None) == 429:
            hint = "大模型服务限流或额度不足，请检查服务端返回信息及账户额度，稍后重试。"
        elif (getattr(exc, "status_code", None) or 0) >= 500:
            hint = "大模型服务暂时异常，请稍后重试或检查上游服务状态。"
        else:
            hint = "请检查 OPENAI_BASE_URL（OpenAI/中转一般需要以 /v1 结尾，DeepSeek 用 https://api.deepseek.com）与 OPENAI_MODEL。"
        raise VideoProcessingError(
            f"大模型调用失败：POST {endpoint} | model={settings.openai_model} | {exc}。"
            + hint
        ) from exc


def _summarize_single(timeline: str, duration_seconds: int | None, page: int, page_title: str) -> tuple[str, list, list]:
    kp_count = _compute_highlight_count(duration_seconds)
    prompt = _SINGLE_PROMPT.format(
        kp_count=kp_count,
        page_no=page,
        page_title=f" {page_title}" if page_title else "",
        subtitle=timeline,
    )
    content = _chat(prompt, max_tokens=1600)
    return _parse_summary_response(content)


def _summarize_chunk(index: int, total: int, chunk: str) -> dict:
    span = _chunk_span_seconds(chunk)
    kp_count = _compute_highlight_count(span or 0)
    prompt = _CHUNK_PROMPT.format(index=index + 1, total=total, kp_count=kp_count, subtitle=chunk)
    content = _chat(prompt, max_tokens=900)
    summary, points, quotes = _parse_summary_response(content)
    return {"index": index, "summary": summary, "points": points, "quotes": quotes}


def _summarize_chunks(chunks: list[str]) -> list[dict]:
    total = len(chunks)
    with ThreadPoolExecutor(max_workers=min(4, total)) as executor:
        results = list(executor.map(
            lambda idx_chunk: _summarize_chunk(idx_chunk[0], total, idx_chunk[1]),
            enumerate(chunks),
        ))
    results.sort(key=lambda r: r["index"])
    return results


def _build_chunks_brief(chunk_results: list[dict]) -> str:
    parts: list[str] = []
    for r in chunk_results:
        block = [f"【第 {r['index'] + 1} 段】", f"摘要：{r['summary']}"]
        if r["points"]:
            block.append("关键点：")
            for p in r["points"]:
                block.append(
                    f"- [{p.get('time', '')}] {p.get('title', '')} — {p.get('detail', '')}"
                )
        if r["quotes"]:
            block.append("金句：" + " / ".join(str(q) for q in r["quotes"]))
        parts.append("\n".join(block))
    return "\n\n".join(parts)


def _synthesize(chunk_results: list[dict], duration_seconds: int | None) -> tuple[str, list, list]:
    kp_count = _compute_highlight_count(duration_seconds)
    prompt = _SYNTHESIS_PROMPT.format(
        kp_count=kp_count,
        chunks_brief=_build_chunks_brief(chunk_results),
    )
    content = _chat(prompt, max_tokens=1600)
    return _parse_summary_response(content)


# --- Markdown rendering ---

def _render_markdown(
    bv: str,
    page: int,
    title: str,
    up: str,
    duration_str: str | None,
    overall: str,
    points: list[dict],
    quotes: list[str],
) -> str:
    lines = [f"# {title}", ""]
    meta: list[str] = []
    if up:
        meta.append(f"UP 主：{up}")
    if duration_str:
        meta.append(f"时长：{duration_str}")
    meta.append(f"BV：{bv}（P{page}）")
    lines.append("  ".join(meta))
    lines += ["", "## 视频总结", "", overall.strip(), ""]
    if points:
        lines += ["## 关键内容点", ""]
        for p in points:
            lines.append(f"- **[{p['time']}] {p['title']}** — {p['detail']}")
            lines.append(f"  - 跳转：{p['url']}")
        lines.append("")
    if quotes:
        lines += ["## 金句 / 知识点", ""]
        for q in quotes:
            lines.append(f"- {q}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


# --- Cache (SQLite via summary_store) ---

def subtitle_download_url(bv: str, page: int) -> str:
    return f"/api/summary/subtitle/{bv}/{page}"


# One generation in flight per (bv, page): concurrent callers (e.g. two browser
# tabs opening the same shareable result link) block, then return the row the
# first caller stored instead of each running their own LLM map-reduce.
_generation_locks: dict[tuple[str, int], threading.Lock] = {}
_locks_guard = threading.Lock()


def _cached_payload(bv: str, page: int) -> dict | None:
    """Return a stored summary decorated with cached=True + subtitle_url, or None."""
    cached = summary_store.get_summary(bv, page)
    if cached:
        cached["cached"] = True
        cached["subtitle_url"] = subtitle_download_url(bv, page)
    return cached


# --- Public entry point ---

def generate_summary(bv: str, page: int) -> dict:
    if not settings.openai_api_key:
        raise SummaryConfigError("未配置 OPENAI_API_KEY，无法调用大模型生成总结。")

    # Fast path: lock-free cache hit.
    cached = _cached_payload(bv, page)
    if cached:
        return cached

    # Slow path: serialize generation per (bv, page). A concurrent caller that
    # arrived while we held the lock re-checks the cache on release and returns
    # the now-stored row instead of re-running the LLM.
    with _locks_guard:
        lock = _generation_locks.setdefault((bv, page), threading.Lock())
    try:
        with lock:
            cached = _cached_payload(bv, page)
            if cached:
                return cached

            payload = _generate_uncached(bv, page)
            summary_store.save_summary(payload)
        return payload
    finally:
        # Drop the entry so the dict tracks only in-flight keys, not every BV
        # ever requested. Waiters still hold a reference to this Lock object and
        # finish safely; later callers hit the lock-free fast path.
        with _locks_guard:
            _generation_locks.pop((bv, page), None)


def _generate_uncached(bv: str, page: int) -> dict:
    data = fetch_subtitle(bv, page)  # raises NoSubtitleError / VideoProcessingError
    duration = data.duration_seconds
    chunks = _split_timeline(data.timeline, settings.summary_max_input_chars)

    if len(chunks) > 1:
        chunk_results = _summarize_chunks(chunks)
        overall, points, quotes = _synthesize(chunk_results, duration)
    else:
        overall, points, quotes = _summarize_single(
            data.timeline, duration, data.page, data.title
        )

    overall = _clean_summary(overall) or data.title
    points = _normalize_key_points(points, bv, data.page)
    quotes = _normalize_quotes(quotes)
    duration_str = format_timestamp(duration) if duration else None
    markdown = _render_markdown(
        bv, data.page, data.title, data.up, duration_str, overall, points, quotes
    )

    return {
        "bv": bv,
        "page": data.page,
        "cid": data.cid,
        "title": data.title,
        "up": data.up,
        "duration": duration_str,
        "cover_url": data.cover_url,
        "overall_summary": overall,
        "key_points": points,
        "quotes": quotes,
        "markdown": markdown,
        "subtitle_timeline": data.timeline,
        "subtitle_format": "txt",
        "subtitle_url": subtitle_download_url(bv, data.page),
        "cached": False,
    }
