import base64
import hashlib
import os
import time
from urllib.parse import urlparse

import httpx

from cryptography.fernet import Fernet
from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from .auth_api import body_or_error
from .character_api import authentication_error
from .models import AuditLog, SiteSettings, UserSettings


DEFAULT_VALUES = {
    "provider_id": "deepseek",
    "api_base_url": "https://api.deepseek.com",
    "model": "deepseek-flash",
    "temperature": 0.8,
    "top_p": 0.9,
    "max_tokens": 4096,
    "context_capacity": 65536,
    "language": "中文",
    "world_background": "",
    "global_prompt": "",
    "reply_format": "structured",
    "adult_content_preference": False,
    "adult_content_keywords": [],
    "strict_persona": True,
    "auto_state_extraction": True,
    "welcome_message": "",
    "scene_defaults": {},
    "stream_output": True,
    "save_raw_response": False,
    "memory_threshold": 30,
    "profile_threshold": 10,
}

DOMESTIC_PROVIDERS = [
    {"id": "deepseek", "name": "DeepSeek", "base_url": "https://api.deepseek.com", "models_url": "https://api.deepseek.com/models", "apply_url": "https://platform.deepseek.com/api_keys", "models": ["deepseek-flash", "deepseek-v4-pro"]},
    {"id": "dashscope", "name": "阿里云百炼", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "models_url": "https://dashscope.aliyuncs.com/api/v1/models", "apply_url": "https://bailian.console.aliyun.com/", "models": ["qwen3.8-flash", "qwen3.7-plus", "deepseek-v4-flash", "glm-5.2", "kimi-k3", "MiniMax-M3"]},
    {"id": "zhipu", "name": "智谱 AI", "base_url": "https://open.bigmodel.cn/api/paas/v4", "models_url": "https://open.bigmodel.cn/api/paas/v4/models", "apply_url": "https://open.bigmodel.cn/usercenter/apikeys", "models": ["glm-5.2", "glm-5-flash"]},
    {"id": "moonshot", "name": "月之暗面 Kimi", "base_url": "https://api.moonshot.cn/v1", "models_url": "https://api.moonshot.cn/v1/models", "apply_url": "https://platform.moonshot.cn/console/api-keys", "models": ["kimi-k3", "kimi-k2.6"]},
    {"id": "minimax", "name": "MiniMax", "base_url": "https://api.minimaxi.com/v1", "models_url": "https://api.minimaxi.com/v1/models", "apply_url": "https://platform.minimaxi.com/user-center/basic-information/interface-key", "models": ["MiniMax-M3", "MiniMax-M2.5"]},
    {"id": "siliconflow", "name": "硅基流动", "base_url": "https://api.siliconflow.cn/v1", "models_url": "https://api.siliconflow.cn/v1/models", "apply_url": "https://cloud.siliconflow.cn/account/ak", "models": ["deepseek-ai/DeepSeek-V3.2", "Qwen/Qwen3.5-397B-A17B", "zai-org/GLM-5"]},
    {"id": "custom", "name": "自定义国内兼容接口", "base_url": "", "apply_url": "", "models": ["自定义模型"]},
]


def cipher():
    key = os.environ.get("TAVERN_ENCRYPTION_KEY")
    if not key and settings.DEBUG:
        key = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest()).decode()
    if not key:
        raise RuntimeError("TAVERN_ENCRYPTION_KEY is required")
    return Fernet(key.encode())


def encrypt_key(value):
    return cipher().encrypt(value.encode()).decode()


def decrypt_key(value):
    return cipher().decrypt(value.encode()).decode() if value else ""


def site_settings():
    return SiteSettings.objects.get_or_create(pk=1)[0]


def user_settings(user):
    return UserSettings.objects.get_or_create(user=user)[0]


def effective_values(user):
    site_values = {key: value for key, value in site_settings().values.items() if not key.startswith("_")}
    return {**DEFAULT_VALUES, **site_values, **user_settings(user).overrides}


def active_api_key(user):
    own = user_settings(user).encrypted_api_key
    return decrypt_key(own or site_settings().encrypted_api_key)


def validate_value(name, value):
    if name == "provider_id":
        return isinstance(value, str) and value in {item["id"] for item in DOMESTIC_PROVIDERS}
    if name == "model":
        return isinstance(value, str) and 1 <= len(value) <= 200 and all(character not in value for character in "\r\n")
    if name == "api_base_url":
        if not isinstance(value, str) or len(value) > 500:
            return False
        parsed = urlparse(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc) and not parsed.username and not parsed.password
    if name in {"temperature", "top_p"}:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= (2 if name == "temperature" else 1)
    if name == "max_tokens":
        return isinstance(value, int) and not isinstance(value, bool) and 128 <= value <= 32768
    if name == 'context_capacity':
        return type(value) is int and 2048<=value<=2000000
    if name in {"memory_threshold", "profile_threshold"}:
        return isinstance(value, int) and not isinstance(value, bool) and 5 <= value <= 500
    if name in {"adult_content_preference", "strict_persona", "auto_state_extraction", "stream_output", "save_raw_response"}:
        return isinstance(value, bool)
    if name == "adult_content_keywords":
        return (
            isinstance(value, list)
            and len(value) <= 32
            and all(isinstance(item, str) and 1 <= len(item.strip()) <= 120 and "\n" not in item and "\r" not in item for item in value)
        )
    if name == "scene_defaults":
        return isinstance(value, dict) and len(value) <= 20 and all(isinstance(key, str) and isinstance(item, str) for key, item in value.items())
    if name == "reply_format":
        return value in {"structured", "natural"}
    if name in {"language", "world_background", "global_prompt", "welcome_message"}:
        return isinstance(value, str) and len(value) <= (20000 if name == "global_prompt" else 5000)
    return False


def update_settings(request, record, attribute):
    data, error = body_or_error(request)
    if error:
        return error
    return apply_settings(data, record, attribute)


def apply_settings(data, record, attribute):
    values = dict(getattr(record, attribute))
    for name, value in data.items():
        if name == "api_key":
            if value is not None and (
                not isinstance(value, str) or len(value) > 512 or
                (value and (not value.isascii() or not value.isprintable()))
            ):
                if isinstance(value, str) and value and not value.isascii():
                    return JsonResponse({"error": "API 密钥只能包含英文、数字和常用符号，请只粘贴密钥本身"}, status=400)
                return JsonResponse({"error": "连接密钥格式无效"}, status=400)
            continue
        if name not in DEFAULT_VALUES or (value is not None and not validate_value(name, value)):
            return JsonResponse({"error": "设置内容无效"}, status=400)
        if value is None:
            values.pop(name, None)
        else:
            if name == "adult_content_keywords":
                value = list(dict.fromkeys(item.strip() for item in value if item.strip()))[:32]
            values[name] = value

    setattr(record, attribute, values)
    if "api_key" in data:
        cleaned_key = data["api_key"].strip() if isinstance(data["api_key"], str) else data["api_key"]
        record.encrypted_api_key = encrypt_key(cleaned_key) if cleaned_key else ""
    record.save()
    return None


@require_http_methods(["GET", "PUT"])
def my_settings(request):
    error = authentication_error(request)
    if error:
        return error
    record = user_settings(request.user)
    if request.method == "PUT":
        error = update_settings(request, record, "overrides")
        if error:
            return error
    site = site_settings()
    source = "user" if record.encrypted_api_key else "site" if site.encrypted_api_key else "none"
    return JsonResponse(
        {
            "effective": effective_values(request.user),
            "overrides": record.overrides,
            "has_api_key": source != "none",
            "api_key_source": source,
        }
    )


@require_http_methods(["GET", "PUT"])
def admin_defaults(request):
    error = authentication_error(request)
    if error:
        return error
    if not request.user.is_staff:
        return JsonResponse({"error": "无权限"}, status=403)
    record = site_settings()
    if request.method == "PUT":
        data, error = body_or_error(request)
        if error:
            return error
        error = apply_settings(data, record, "values")
        if error:
            return error
        AuditLog.objects.create(actor=request.user, action="edit_defaults")
    clean_values = {key: value for key, value in record.values.items() if not key.startswith("_")}
    return JsonResponse(
        {
            "defaults": {**DEFAULT_VALUES, **clean_values},
            "has_api_key": bool(record.encrypted_api_key),
        }
    )


def test_model_connection(user):
    from .generation import call_json_model

    result = call_json_model(
        [
            {"role": "system", "content": "请只返回 JSON 对象：{\"ok\":true}。"},
            {"role": "user", "content": "连接测试。请返回 JSON。"},
        ],
        effective_values(user), active_api_key(user), max_tokens=128,
    )
    return result.get("ok") is True


def connection_error_payload(error):
    message = str(error)
    mappings = [
        ("HTTP 401", "invalid_key", "API 密钥无效、已过期或不属于当前服务商"),
        ("HTTP 403", "permission_denied", "账号无权调用这个模型，请检查模型权限或实名状态"),
        ("HTTP 402", "insufficient_balance", "账户余额或可用额度不足"),
        ("HTTP 404", "model_not_found", "接口地址或模型名称不正确"),
        ("HTTP 429", "rate_limited", "请求过于频繁或已达到服务商限额"),
        ("HTTP 400", "incompatible_request", "模型不支持当前请求格式或 JSON 输出"),
        ("连接失败", "network_error", "无法连接模型服务，请检查接口地址、网络或服务状态"),
        ("超时", "timeout", "模型响应超时，请稍后重试或更换模型"),
        ("有效 JSON", "invalid_response", "已经联通，但模型没有返回酒馆需要的 JSON 格式"),
    ]
    for marker, code, reason in mappings:
        if marker in message:
            return {"ok": False, "reason_code": code, "reason": reason, "error": reason}
    return {"ok": False, "reason_code": "unknown_error", "reason": message or "未知错误", "error": message or "未知错误"}


@require_http_methods(["GET"])
def model_providers(request):
    error = authentication_error(request)
    if error:
        return error
    return JsonResponse({"providers": DOMESTIC_PROVIDERS})


def fetch_available_models(user):
    options = effective_values(user)
    base_url = options["api_base_url"].rstrip("/")
    provider = next((item for item in DOMESTIC_PROVIDERS if item["id"] == options.get("provider_id")), None)
    models_url = provider.get("models_url") if provider else None
    response = httpx.get(
        models_url or f"{base_url}/models",
        headers={"Authorization": f"Bearer {active_api_key(user)}"},
        timeout=httpx.Timeout(30, connect=10),
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data", payload.get("models", []))
    if isinstance(data, dict):
        data = data.get("models", data.get("data", []))
    return sorted({item["id"] for item in data if isinstance(item, dict) and isinstance(item.get("id"), str)})[:500]


def fetch_provider_usage(user):
    options = effective_values(user)
    provider_id = options.get("provider_id")
    if provider_id != "deepseek":
        return {"supported": False, "balance": None, "currency": None, "used": None, "reason": "当前服务商未提供可由密钥直接读取的统一用量接口"}
    response = httpx.get(
        "https://api.deepseek.com/user/balance",
        headers={"Authorization": f"Bearer {active_api_key(user)}"},
        timeout=httpx.Timeout(20, connect=8),
    )
    response.raise_for_status()
    data = response.json()
    balances = data.get("balance_infos", [])
    first = balances[0] if balances else {}
    return {
        "supported": True,
        "balance": first.get("total_balance"),
        "currency": first.get("currency", "CNY"),
        "used": None,
        "reason": "" if data.get("is_available", True) else "账户余额不可用",
    }


@require_http_methods(["GET"])
def provider_usage(request):
    error = authentication_error(request)
    if error:
        return error
    if not active_api_key(request.user):
        return JsonResponse({"supported": False, "balance": None, "currency": None, "used": None, "reason": "请先保存 API 密钥", "checked_at": int(time.time())})
    options = effective_values(request.user)
    cache_key = f"provider-usage:{request.user.id}:{options.get('provider_id')}"
    result = None if request.GET.get("refresh") == "1" else cache.get(cache_key)
    if result is None:
        try:
            result = fetch_provider_usage(request.user)
        except httpx.HTTPStatusError as exc:
            reason = connection_error_payload(RuntimeError(f"HTTP {exc.response.status_code}"))["reason"]
            result = {"supported": True, "balance": None, "currency": None, "used": None, "reason": reason}
        except (httpx.HTTPError, ValueError, TypeError):
            result = {"supported": True, "balance": None, "currency": None, "used": None, "reason": "用量查询失败，请检查网络或稍后重试"}
        cache.set(cache_key, result, 240)
    return JsonResponse({**result, "checked_at": int(time.time())})


@require_POST
def connection_test(request):
    error = authentication_error(request)
    if error:
        return error
    if not active_api_key(request.user):
        return JsonResponse({"ok": False, "reason_code": "missing_key", "reason": "请先填写并保存 API 密钥", "error": "请先填写并保存 API 密钥"}, status=400)
    started = time.monotonic()
    try:
        ok = test_model_connection(request.user)
    except (RuntimeError, ValueError) as exc:
        return JsonResponse(connection_error_payload(exc), status=502)
    if not ok:
        result = {"ok": False, "reason_code": "invalid_response", "reason": "已经联通，但模型响应格式不符合要求", "error": "模型响应格式无效"}
        return JsonResponse(result, status=502)
    elapsed_ms = round((time.monotonic() - started) * 1000)
    values = effective_values(request.user)
    return JsonResponse({"ok": True, "reason_code": "connected", "reason": f"连接成功，{values['model']} 响应正常（{elapsed_ms} 毫秒）", "model": values["model"], "latency_ms": elapsed_ms, "error": ""})


@require_POST
def available_models(request):
    error = authentication_error(request)
    if error:
        return error
    if not active_api_key(request.user):
        return JsonResponse({"error": "请先配置 API 密钥"}, status=400)
    try:
        models = fetch_available_models(request.user)
    except (httpx.HTTPError, ValueError, TypeError):
        return JsonResponse({"error": "无法读取模型列表，请检查接口地址和密钥"}, status=502)
    return JsonResponse({"models": models})
