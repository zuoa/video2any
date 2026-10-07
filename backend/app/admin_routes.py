"""Authenticated management endpoints, deliberately excluded from OpenAPI."""
from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import admin_auth, bili_auth
from .admin_store import read_config, save_config
from .config import settings

router = APIRouter(prefix="/api/_admin", include_in_schema=False)
Admin = Annotated[dict, Depends(admin_auth.require_admin)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginInput(Input):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class PasswordInput(Input):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


class CredentialsInput(Input):
    cookie_text: str | None = Field(default=None, max_length=100_000)
    refresh_token: str | None = Field(default=None, max_length=4096)


class BilibiliInput(Input):
    enabled: bool
    check_interval_seconds: int = Field(ge=3600, le=604800)
    retry_interval_seconds: int = Field(ge=60, le=86400)
    rate_limit_seconds: float = Field(ge=0.5, le=60, allow_inf_nan=False)


def validate_url(value: str, *, optional=False) -> str:
    value = value.strip().rstrip("/")
    if not value and optional:
        return ""
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError()
        parsed.port
    except ValueError:
        raise ValueError("请输入完整的 HTTP 或 HTTPS 地址，不包含登录信息、查询参数或片段") from None
    return value


class LLMInput(Input):
    base_url: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=200)
    api_key: str | None = Field(default=None, max_length=4096)
    timeout: int = Field(ge=10, le=1800)
    reasoning_effort: Literal["", "none", "minimal", "low", "medium", "high", "max"]
    thinking_type: Literal["", "enabled", "disabled"]
    reasoning_max_tokens: int = Field(ge=1, le=131072)
    json_mode: bool
    summary_max_input_chars: int = Field(ge=1000, le=100000)

    @field_validator("base_url")
    @classmethod
    def url(cls, value):
        return validate_url(value)


class SiteInput(Input):
    site_url: str = Field(max_length=2048)

    @field_validator("site_url")
    @classmethod
    def url(cls, value):
        return validate_url(value, optional=True)


def config_payload() -> dict:
    settings.refresh_runtime()
    return {
        "bilibili": {
            "enabled": settings.bilibili_keepalive_enabled,
            "check_interval_seconds": settings.bilibili_check_interval,
            "retry_interval_seconds": settings.bilibili_retry_interval,
            "rate_limit_seconds": settings.bili_rate_limit_seconds,
        },
        "llm": {
            "base_url": settings.openai_base_url, "model": settings.openai_model,
            "api_key_configured": bool(settings.openai_api_key), "timeout": settings.openai_timeout,
            "reasoning_effort": settings.openai_reasoning_effort, "thinking_type": settings.openai_thinking_type,
            "reasoning_max_tokens": settings.openai_reasoning_max_tokens, "json_mode": settings.openai_json_mode,
            "summary_max_input_chars": settings.summary_max_input_chars,
        },
        "site": {"site_url": settings.site_url},
        "saved_fields": sorted(read_config(settings.data_dir)),
    }


@router.post("/login")
def login(body: LoginInput, request: Request, response: Response) -> dict:
    admin_auth.check_origin(request)
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(415, "请使用管理页面登录")
    token, session = admin_auth.login(body.username, body.password, request.client.host if request.client else "unknown")
    response.set_cookie(admin_auth.COOKIE_NAME, token, max_age=admin_auth.SESSION_SECONDS,
                        httponly=True, secure=request.url.scheme == "https", samesite="strict", path="/api")
    return session


@router.get("/me")
def me(admin: Admin) -> dict:
    return {key: value for key, value in admin.items() if key != "token_hash"}


@router.post("/logout")
def logout(response: Response, admin: Admin) -> dict:
    admin_auth.logout(admin["token_hash"])
    response.delete_cookie(admin_auth.COOKIE_NAME, path="/api", httponly=True, samesite="strict")
    return {"message": "已退出登录"}


@router.post("/password")
def password(body: PasswordInput, response: Response, admin: Admin) -> dict:
    admin_auth.change_password(body.current_password, body.new_password)
    response.delete_cookie(admin_auth.COOKIE_NAME, path="/api", httponly=True, samesite="strict")
    return {"message": "密码已更新，请重新登录"}


@router.get("/settings")
def get_settings(admin: Admin) -> dict:
    return config_payload()


@router.get("/bilibili")
def bilibili_status(admin: Admin) -> dict:
    return bili_auth.session_status()


@router.put("/bilibili/credentials")
def credentials(body: CredentialsInput, admin: Admin) -> dict:
    if body.cookie_text is None and body.refresh_token is None:
        raise HTTPException(400, "请填写 Cookie 或刷新令牌")
    try:
        bili_auth.import_credentials(body.cookie_text, body.refresh_token)
    except ValueError:
        raise HTTPException(400, "凭据格式不正确；请导入包含 SESSDATA 的 Cookie，自动续期还需 bili_jct 和同次登录的刷新令牌") from None
    return bili_auth.session_status()


@router.delete("/bilibili/credentials")
def clear_credentials(admin: Admin) -> dict:
    bili_auth.import_credentials(clear=True)
    return bili_auth.session_status()


@router.post("/bilibili/check")
def check_bilibili(admin: Admin) -> dict:
    return bili_auth.ensure_session(force=True)


@router.put("/settings/bilibili")
def bilibili_settings(body: BilibiliInput, admin: Admin) -> dict:
    save_config(settings.data_dir, {
        "bilibili_keepalive_enabled": body.enabled, "bilibili_check_interval": body.check_interval_seconds,
        "bilibili_retry_interval": body.retry_interval_seconds, "bili_rate_limit_seconds": body.rate_limit_seconds,
    })
    settings.refresh_runtime()
    bili_auth.reset_check_schedule()
    return config_payload()["bilibili"]


@router.put("/settings/llm")
def llm_settings(body: LLMInput, admin: Admin) -> dict:
    values = {
        "openai_base_url": body.base_url, "openai_model": body.model.strip(), "openai_timeout": body.timeout,
        "openai_reasoning_effort": body.reasoning_effort, "openai_thinking_type": body.thinking_type,
        "openai_reasoning_max_tokens": body.reasoning_max_tokens, "openai_json_mode": body.json_mode,
        "summary_max_input_chars": body.summary_max_input_chars,
    }
    if not values["openai_model"]:
        raise HTTPException(400, "请填写模型名称")
    if body.api_key is not None:
        values["openai_api_key"] = body.api_key.strip()
    save_config(settings.data_dir, values)
    return config_payload()["llm"]


@router.put("/settings/site")
def site_settings(body: SiteInput, admin: Admin) -> dict:
    save_config(settings.data_dir, {"site_url": body.site_url})
    return config_payload()["site"]
