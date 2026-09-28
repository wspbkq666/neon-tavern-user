import json
from dataclasses import dataclass
from hashlib import sha256
from threading import Lock
from time import monotonic

from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from .site_scope import ensure_user_site
from .models import UserProfile


MAX_FAILED_LOGINS = 5
LOGIN_WINDOW_SECONDS = 300
MAX_LOGIN_BUCKETS = 10000
LOGIN_ERROR = "账号或密码不正确"
PRIVACY_POLICY_VERSION = "2026-09-28-central-chat-v1"
USAGE_RULES_VERSION = "1.0"


@dataclass
class _LoginBucket:
    failures: int
    in_flight: int
    expires_at: float


_login_lock = Lock()
_login_attempts = {}


def _reserve_login_attempt(username, remote_addr):
    key = (sha256(username.casefold().encode("utf-8")).digest(), remote_addr)
    with _login_lock:
        now = monotonic()
        bucket = _login_attempts.get(key)
        if bucket is not None and bucket.expires_at <= now:
            del _login_attempts[key]
            bucket = None
        if bucket is None:
            if len(_login_attempts) >= MAX_LOGIN_BUCKETS:
                for old_key, old_bucket in list(_login_attempts.items()):
                    if old_bucket.expires_at <= now:
                        del _login_attempts[old_key]
            if len(_login_attempts) >= MAX_LOGIN_BUCKETS:
                return None
            bucket = _LoginBucket(0, 0, now + LOGIN_WINDOW_SECONDS)
            _login_attempts[key] = bucket
        if bucket.failures + bucket.in_flight >= MAX_FAILED_LOGINS:
            return None
        bucket.in_flight += 1
        return key, bucket


def _finish_login_attempt(key, bucket, succeeded):
    with _login_lock:
        if _login_attempts.get(key) is not bucket:
            return
        bucket.in_flight -= 1
        if succeeded:
            del _login_attempts[key]
        else:
            bucket.failures += 1


def body_or_error(request):
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        return None, JsonResponse({"error": "请求内容无效"}, status=400)
    if not isinstance(data, dict):
        return None, JsonResponse({"error": "请求内容无效"}, status=400)
    return data, None


def role_for_user(user):
    if user.is_authenticated and user.is_staff:
        return "site_admin"
    return "user"


def user_payload(user):
    profile = UserProfile.objects.filter(user=user).first()
    consent_required = bool(
        profile is None
        or profile.policy_consent_at is None
        or profile.privacy_policy_version != PRIVACY_POLICY_VERSION
        or profile.usage_rules_version != USAGE_RULES_VERSION
    )
    return {
        "authenticated": True,
        "id": user.pk,
        "username": user.username,
        "is_admin": user.is_staff,
        "role": role_for_user(user),
        "policy_consent_required": consent_required,
    }


@ensure_csrf_cookie
@require_GET
def session_info(request):
    if not request.user.is_authenticated:
        return JsonResponse({"authenticated": False})
    ensure_user_site(request.user, request)
    return JsonResponse(user_payload(request.user))


@require_POST
def register(request):
    data, error = body_or_error(request)
    if error:
        return error
    username = str(data.get("username", "")).strip()
    password = data.get("password", "")
    if (
        data.get("policy_consent") is not True
        or data.get("privacy_policy_version") != PRIVACY_POLICY_VERSION
        or data.get("usage_rules_version") != USAGE_RULES_VERSION
    ):
        return JsonResponse({"error": "请阅读并同意当前版本的隐私协议和用户使用规则"}, status=400)
    if not isinstance(password, str) or not 3 <= len(username) <= 30:
        return JsonResponse({"error": "账号或密码格式无效"}, status=400)
    User = get_user_model()
    first_site_admin = not User.objects.filter(is_staff=True, is_superuser=False).exists()
    user = User(username=username, is_staff=first_site_admin, is_superuser=False)
    try:
        user.full_clean(exclude=["password"])
        validate_password(password, user)
        user.set_password(password)
        user.save()
        UserProfile.objects.update_or_create(
            user=user,
            defaults={
                "policy_consent_at": timezone.now(),
                "privacy_policy_version": PRIVACY_POLICY_VERSION,
                "usage_rules_version": USAGE_RULES_VERSION,
            },
        )
        from .user_directory_sync import queue_user_directory_event
        queue_user_directory_event(user)
    except (ValidationError, IntegrityError) as exc:
        if isinstance(exc, ValidationError):
            message = "; ".join(exc.messages)
        else:
            message = "账号已存在"
        return JsonResponse({"error": message}, status=400)

    login(request, user)
    ensure_user_site(user, request)
    return JsonResponse(user_payload(user), status=201)


@require_POST
def sign_in(request):
    data, error = body_or_error(request)
    if error:
        return error
    username = str(data.get("username", "")).strip()
    password = data.get("password", "")
    reservation = _reserve_login_attempt(username, request.META.get("REMOTE_ADDR", ""))
    if reservation is None:
        return JsonResponse({"error": LOGIN_ERROR}, status=429)
    succeeded = False
    try:
        user = (
            authenticate(request, username=username, password=password)
            if isinstance(password, str)
            else None
        )
        succeeded = user is not None and user.is_active
    finally:
        _finish_login_attempt(*reservation, succeeded=succeeded)
    if not succeeded:
        return JsonResponse({"error": LOGIN_ERROR}, status=401)
    login(request, user)
    if data.get("remember") is False:
        request.session.set_expiry(0)
    ensure_user_site(user, request)
    return JsonResponse(user_payload(user))


@require_POST
def sign_out(request):
    logout(request)
    return JsonResponse({"ok": True})


@require_POST
def accept_current_policy(request):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "请先登录"}, status=401)
    data, error = body_or_error(request)
    if error:
        return error
    if (
        set(data) != {"policy_consent", "privacy_policy_version", "usage_rules_version"}
        or data.get("policy_consent") is not True
        or data.get("privacy_policy_version") != PRIVACY_POLICY_VERSION
        or data.get("usage_rules_version") != USAGE_RULES_VERSION
    ):
        return JsonResponse({"error": "确认内容无效，请刷新页面后重试"}, status=400)
    UserProfile.objects.update_or_create(
        user=request.user,
        defaults={
            "policy_consent_at": timezone.now(),
            "privacy_policy_version": PRIVACY_POLICY_VERSION,
            "usage_rules_version": USAGE_RULES_VERSION,
        },
    )
    from .user_directory_sync import queue_user_directory_event
    queue_user_directory_event(request.user)
    return JsonResponse({"ok": True, "policy_consent_required": False})
