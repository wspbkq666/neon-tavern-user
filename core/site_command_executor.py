import hashlib
import os
import secrets
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import AuditLog, Conversation, FederationIdentity, SiteCommandReceipt, UserDisposition, UserWarning
from .site_command_signing import canonical_payload, verify_command
from .site_scope import user_site_key


USER_ACTIONS = frozenset({"warn", "suspend", "ban", "unban", "reset_password", "delete_user"})
SIGNED_ACTIONS = USER_ACTIONS | {"grant_site_admin", "revoke_site"}
PAYLOAD_FIELDS = {"command_id", "site_id", "target_source_user_id", "target_username", "action", "reason", "issued_at", "expires_at"}


def execute_user_action(user, action, reason, *, actor=None, remote=False, target_username=""):
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
        raise ValueError("请填写处置理由")
    if action not in USER_ACTIONS:
        raise ValueError("处置操作无效")
    if user.is_superuser or (user.is_staff and actor is not None and not actor.is_superuser):
        raise ValueError("不能处置管理员账号")
    if action == "delete_user" and target_username != user.username:
        raise ValueError("确认账号名不一致")
    result = {}
    if action == "warn":
        UserWarning.objects.create(user=user, message=reason.strip())
    elif action in ("suspend", "ban"):
        status = "suspended" if action == "suspend" else "banned"
        disposition = UserDisposition.objects.filter(user=user).first()
        was_active = disposition.was_active if disposition and disposition.status in ("suspended", "banned") else user.is_active
        UserDisposition.objects.update_or_create(user=user, defaults={"status": status, "was_active": was_active})
        user.is_active = False
        user.save(update_fields=["is_active"])
    elif action == "unban":
        disposition = UserDisposition.objects.filter(user=user).first()
        was_active = disposition.was_active if disposition and disposition.status in ("suspended", "banned") else user.is_active
        UserDisposition.objects.update_or_create(user=user, defaults={"status": "active", "was_active": was_active})
        user.is_active = was_active
        user.save(update_fields=["is_active"])
    elif action == "reset_password":
        if remote:
            raise ValueError("跨站密码重置需要安全的密码交付渠道，请在目标站点本地操作")
        else:
            temporary_password = secrets.token_urlsafe(32)
            user.set_password(temporary_password)
            result["temporary_password"] = temporary_password
        user.save(update_fields=["password"])
    AuditLog.objects.create(actor=actor, target_user=user, action=action)
    if action == "delete_user":
        Conversation.objects.filter(owner=user).delete()
        user.characters.all().delete()
        user.delete()
    return result


def _checked_payload(command, identity):
    if not verify_command(command, identity.central_public_key):
        raise ValueError("命令签名无效")
    payload = command["payload"]
    if set(payload) != PAYLOAD_FIELDS or payload["site_id"] != str(identity.site_id):
        raise ValueError("命令站点或字段无效")
    uuid.UUID(payload["command_id"])
    if payload["action"] not in SIGNED_ACTIONS:
        raise ValueError("命令操作无效")
    if not isinstance(payload["reason"], str) or not payload["reason"].strip() or len(payload["reason"]) > 2000:
        raise ValueError("命令理由无效")
    if not isinstance(payload["target_source_user_id"], str) or len(payload["target_source_user_id"]) > 100:
        raise ValueError("目标账号无效")
    if not isinstance(payload["target_username"], str) or len(payload["target_username"]) > 150:
        raise ValueError("目标账号无效")
    issued = parse_datetime(payload["issued_at"])
    expires = parse_datetime(payload["expires_at"])
    if issued is None or expires is None or timezone.is_naive(issued) or timezone.is_naive(expires) or expires <= issued:
        raise ValueError("命令时间无效")
    if issued > timezone.now() + timezone.timedelta(minutes=5):
        raise ValueError("命令时间无效")
    return payload, expires


def apply_site_command(command: dict, identity=None) -> dict:
    try:
        identity = identity or FederationIdentity.objects.get(pk=1)
        payload, expires = _checked_payload(command, identity)
    except (FederationIdentity.DoesNotExist, ValueError, TypeError, KeyError, OverflowError):
        return {"status": "failed"}
    command_id = uuid.UUID(payload["command_id"])
    digest = hashlib.sha256(canonical_payload(payload)).hexdigest()
    previous = SiteCommandReceipt.objects.filter(command_id=command_id).first()
    if previous:
        return {"status": previous.status} if previous.payload_digest == digest else {"status": "failed"}
    if not identity.active and payload["action"] != "revoke_site":
        return {"status": "failed"}
    if expires <= timezone.now():
        SiteCommandReceipt.objects.get_or_create(command_id=command_id, defaults={"payload_digest": digest, "status": "expired"})
        return {"status": "expired"}
    try:
        with transaction.atomic():
            receipt = SiteCommandReceipt.objects.create(command_id=command_id, payload_digest=digest, status="executed")
            if payload["action"] == "revoke_site":
                FederationIdentity.objects.filter(pk=identity.pk, active=True).update(active=False)
            else:
                site_key = os.environ.get(
                    "TAVERN_FEDERATION_SITE_KEY",
                    getattr(settings, "TAVERN_FEDERATION_SITE_KEY", ""),
                ).strip().casefold()
                if not site_key:
                    raise ValueError("未配置本站范围")
                user = get_user_model().objects.get(pk=payload["target_source_user_id"])
                if user_site_key(user) != site_key:
                    raise ValueError("目标账号不属于本站")
                if payload["action"] == "grant_site_admin":
                    if user.is_superuser:
                        raise ValueError("不能授权超级管理员")
                    user.is_staff = True
                    user.save(update_fields=["is_staff"])
                    AuditLog.objects.create(target_user=user, action="grant_site_admin")
                else:
                    execute_user_action(user, payload["action"], payload["reason"], remote=True, target_username=payload["target_username"])
            receipt.result = {"ok": True}
            receipt.save(update_fields=["result"])
    except (IntegrityError, get_user_model().DoesNotExist, ValueError):
        previous = SiteCommandReceipt.objects.filter(command_id=command_id).first()
        if previous:
            return {"status": previous.status} if previous.payload_digest == digest else {"status": "failed"}
        SiteCommandReceipt.objects.create(command_id=command_id, payload_digest=digest, status="failed")
        return {"status": "failed"}
    return {"status": "executed"}
