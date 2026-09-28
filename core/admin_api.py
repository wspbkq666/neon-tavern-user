import secrets

from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .auth_api import body_or_error
from .character_api import authentication_error, character_payload
from .conversation_api import conversation_payload
from .models import AuditLog, Conversation
from .site_scope import ensure_user_site, user_belongs_to_request_site, user_site_key
from .worldbook_api import worldbook_payload


def staff_error(request):
    error = authentication_error(request)
    if error:
        return error
    if not request.user.is_staff:
        return JsonResponse({"error": "无权限"}, status=403)
    ensure_user_site(request.user, request)
    return None


def counted_users():
    return get_user_model().objects.annotate(
        character_count=Count("characters", distinct=True),
        conversation_count=Count("conversations", distinct=True),
    )


def visible_users(request):
    queryset = counted_users()
    site_key = request.get_host().strip().casefold()
    return queryset.filter(Q(tavern_profile__site_key=site_key) | Q(tavern_profile__site_key=""))


def scoped_user_or_404(request, user_id):
    user = get_object_or_404(counted_users(), pk=user_id)
    if not user_belongs_to_request_site(user, request):
        from django.http import Http404
        raise Http404
    return user


def admin_user_payload(user):
    return {
        "id": user.pk,
        "username": user.username,
        "is_admin": user.is_staff,
        "role": "site_admin" if user.is_staff else "user",
        "site": user_site_key(user),
        "is_active": user.is_active,
        "date_joined": user.date_joined.isoformat(),
        "last_login": user.last_login.isoformat() if user.last_login else None,
        "character_count": user.character_count,
        "conversation_count": user.conversation_count,
    }


@require_GET
def users(request):
    error = staff_error(request)
    if error:
        return error
    return JsonResponse({"users": [admin_user_payload(user) for user in visible_users(request).order_by("username", "pk")]})


@require_http_methods(["GET", "DELETE"])
def user_detail(request, user_id):
    error = staff_error(request)
    if error:
        return error
    if request.method == "DELETE":
        return delete_user(request, user_id)
    user = scoped_user_or_404(request, user_id)
    AuditLog.objects.create(actor=request.user, target_user=user, action="view_user")
    return JsonResponse(
        {
            "user": admin_user_payload(user),
            "characters": [character_payload(item) for item in user.characters.all()],
            "conversations": [conversation_payload(item) for item in user.conversations.all()],
        }
    )


@require_GET
def user_conversation(request, user_id, conversation_id):
    error = staff_error(request)
    if error:
        return error
    scoped_user_or_404(request, user_id)
    conversation = get_object_or_404(Conversation, pk=conversation_id, owner_id=user_id)
    AuditLog.objects.create(actor=request.user, target_user=conversation.owner, action="view_conversation")
    return JsonResponse(conversation_payload(conversation, include_messages=True))


@require_GET
def user_worldbooks(request, user_id):
    error = staff_error(request)
    if error:
        return error
    user = scoped_user_or_404(request, user_id)
    AuditLog.objects.create(actor=request.user, target_user=user, action="view_worldbooks")
    return JsonResponse({"worldbooks": [worldbook_payload(item, expanded=True) for item in user.worldbooks.all()]})


@require_POST
def reset_password(request, user_id):
    error = staff_error(request)
    if error:
        return error
    user = scoped_user_or_404(request, user_id)
    supplied_password = None
    if request.content_type == "application/json":
        data, error = body_or_error(request)
        if error:
            return error
        supplied_password = data.get("new_password")
        if not isinstance(supplied_password, str):
            return JsonResponse({"error": "新密码格式无效"}, status=400)
        try:
            validate_password(supplied_password, user)
        except ValidationError as exc:
            return JsonResponse({"error": "; ".join(exc.messages)}, status=400)
    temporary_password = supplied_password or secrets.token_urlsafe(32)
    user.set_password(temporary_password)
    user.save(update_fields=["password"])
    AuditLog.objects.create(actor=request.user, target_user=user, action="reset_password")
    if request.user.pk == user.pk:
        update_session_auth_hash(request, user)
    return JsonResponse({"ok": True} if supplied_password else {"temporary_password": temporary_password})


@require_http_methods(["DELETE"])
def delete_user(request, user_id):
    error = staff_error(request)
    if error:
        return error
    user = scoped_user_or_404(request, user_id)
    if user.pk == request.user.pk or user.is_staff:
        return JsonResponse({"error": "不能删除管理员账号"}, status=403)
    data, error = body_or_error(request)
    if error:
        return error
    if data.get("confirm_username") != user.username:
        return JsonResponse({"error": "请输入完全一致的账号名确认删除"}, status=400)
    username = user.username
    with transaction.atomic():
        AuditLog.objects.create(actor=request.user, target_user=user, action="delete_user")
        # Conversations protect their speaker cards, so remove the user's own
        # conversations before removing the cards they own.
        Conversation.objects.filter(owner=user).delete()
        user.characters.all().delete()
        deleted_counts = user.delete()[1]
    return JsonResponse({"ok": True, "username": username, "deleted": deleted_counts})


@require_POST
def change_password(request):
    error = authentication_error(request)
    if error:
        return error
    data, error = body_or_error(request)
    if error:
        return error
    user = request.user
    old_password = data.get("old_password")
    if user.has_usable_password() and (not isinstance(old_password, str) or not user.check_password(old_password)):
        return JsonResponse({"error": "原密码不正确"}, status=400)
    new_password = data.get("new_password")
    if not isinstance(new_password, str):
        return JsonResponse({"error": "新密码格式无效"}, status=400)
    try:
        validate_password(new_password, user)
    except ValidationError as exc:
        return JsonResponse({"error": "; ".join(exc.messages)}, status=400)
    user.set_password(new_password)
    user.save(update_fields=["password"])
    update_session_auth_hash(request, user)
    return JsonResponse({"ok": True})


@require_GET
def audit(request):
    error = staff_error(request)
    if error:
        return error
    entries = AuditLog.objects.select_related("actor", "target_user")[:100]
    return JsonResponse({
        "entries": [
            {
                "id": item.id,
                "actor": item.actor.username if item.actor else None,
                "target_user": item.target_user.username if item.target_user else None,
                "action": item.action,
                "created_at": item.created_at.isoformat(),
            }
            for item in entries
        ]
    })
