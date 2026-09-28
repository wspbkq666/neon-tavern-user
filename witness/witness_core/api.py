import json
import re
import time
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from core.disaster_recovery import Lease, canonical_request_message, lease_to_dict
from .models import LeaseState, RequestNonce
from .signing import sign_lease, verify_node_signature


_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{16,80}$")
_TIMESTAMP = re.compile(r"^[0-9]{1,11}$")


def utcnow():
    return timezone.now().replace(microsecond=0)


def _json_error(code, status):
    return JsonResponse({"error_code": code}, status=status)


def _authenticate_node(request):
    node_id = request.headers.get("X-Witness-Node", "")
    timestamp = request.headers.get("X-Witness-Time", "")
    request_id = request.headers.get("X-Witness-Request-Id", "")
    signature = request.headers.get("X-Witness-Signature", "")
    if not node_id or not _TIMESTAMP.fullmatch(timestamp) or not _REQUEST_ID.fullmatch(request_id):
        return None, _json_error("invalid_signature", 401)
    signed_at = int(timestamp)
    if abs(int(time.time()) - signed_at) > 30:
        return None, _json_error("request_expired", 401)
    message = canonical_request_message(
        node_id,
        request.method,
        request.path,
        timestamp,
        request_id,
        request.body,
    )
    if not verify_node_signature(node_id, signature, message):
        return None, _json_error("invalid_signature", 401)
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, _json_error("invalid_request", 400)
    if not isinstance(payload, dict):
        return None, _json_error("invalid_request", 400)
    return (node_id, request_id, payload), None


def _claim_request_id(node_id, request_id):
    RequestNonce.objects.filter(created_at__lt=utcnow() - timedelta(minutes=10)).delete()
    try:
        with transaction.atomic():
            RequestNonce.objects.create(node_id=node_id, request_id=request_id)
    except IntegrityError:
        return False
    return True


def _current_lease(state):
    if state.epoch < 1:
        return None
    return Lease(
        holder_id=state.holder_id,
        epoch=state.epoch,
        issued_at=state.issued_at,
        expires_at=state.expires_at,
        signature=state.signature,
    )


def _reply_lease(lease, status=200):
    return JsonResponse({"lease": lease_to_dict(lease), "witness_time": int(utcnow().timestamp())}, status=status)


def _cors_status(response, request):
    origin = request.headers.get("Origin", "")
    if request.method == "GET" and origin in settings.WITNESS_PUBLIC_STATUS_ORIGINS:
        response["Access-Control-Allow-Origin"] = origin
        response["Vary"] = "Origin"
    response["Cache-Control"] = "no-store"
    return response


@csrf_exempt
@require_POST
def acquire(request):
    authenticated, error = _authenticate_node(request)
    if error:
        return error
    node_id, request_id, _ = authenticated
    with transaction.atomic():
        try:
            state = LeaseState.objects.select_for_update().get(pk=1)
        except LeaseState.DoesNotExist:
            state = LeaseState.objects.create(pk=1)
        if not _claim_request_id(node_id, request_id):
            return _json_error("request_replayed", 409)
        now = utcnow()
        if state.epoch == 0:
            if node_id != settings.WITNESS_INITIAL_NODE:
                return _json_error("initial_node_required", 409)
        elif state.expires_at is None or now < state.expires_at + timedelta(seconds=settings.WITNESS_CLOCK_SKEW_SECONDS):
            return _json_error("lease_held", 409)
        lease = Lease(
            holder_id=node_id,
            epoch=state.epoch + 1,
            issued_at=now,
            expires_at=now + timedelta(seconds=settings.WITNESS_LEASE_SECONDS),
            signature="pending",
        )
        lease = sign_lease(lease)
        state.holder_id = lease.holder_id
        state.epoch = lease.epoch
        state.issued_at = lease.issued_at
        state.expires_at = lease.expires_at
        state.signature = lease.signature
        state.save()
    return _reply_lease(lease)


@csrf_exempt
@require_POST
def renew(request):
    authenticated, error = _authenticate_node(request)
    if error:
        return error
    node_id, request_id, payload = authenticated
    with transaction.atomic():
        try:
            state = LeaseState.objects.select_for_update().get(pk=1)
        except LeaseState.DoesNotExist:
            return _json_error("lease_missing", 409)
        if not _claim_request_id(node_id, request_id):
            return _json_error("request_replayed", 409)
        now = utcnow()
        if (
            state.holder_id != node_id
            or payload.get("epoch") != state.epoch
            or state.expires_at is None
            or now >= state.expires_at
        ):
            return _json_error("lease_not_renewable", 409)
        lease = Lease(
            holder_id=node_id,
            epoch=state.epoch,
            issued_at=now,
            expires_at=now + timedelta(seconds=settings.WITNESS_LEASE_SECONDS),
            signature="pending",
        )
        lease = sign_lease(lease)
        state.issued_at = lease.issued_at
        state.expires_at = lease.expires_at
        state.signature = lease.signature
        state.save()
    return _reply_lease(lease)


@csrf_exempt
@require_POST
def handoff(request):
    authenticated, error = _authenticate_node(request)
    if error:
        return error
    node_id, request_id, payload = authenticated
    target_id = payload.get("target_node_id")
    try:
        configured_nodes = json.loads(settings.WITNESS_NODE_PUBLIC_KEYS)
    except (TypeError, ValueError):
        configured_nodes = {}
    if not isinstance(target_id, str) or target_id == node_id or target_id not in configured_nodes:
        return _json_error("invalid_handoff_target", 400)
    with transaction.atomic():
        try:
            state = LeaseState.objects.select_for_update().get(pk=1)
        except LeaseState.DoesNotExist:
            return _json_error("lease_missing", 409)
        if not _claim_request_id(node_id, request_id):
            return _json_error("request_replayed", 409)
        now = utcnow()
        if state.holder_id != node_id or state.expires_at is None or now >= state.expires_at:
            return _json_error("lease_not_handoffable", 409)
        lease = sign_lease(Lease(
            holder_id=target_id,
            epoch=state.epoch + 1,
            issued_at=now,
            expires_at=now + timedelta(seconds=settings.WITNESS_LEASE_SECONDS),
            signature="pending",
        ))
        state.holder_id = lease.holder_id
        state.epoch = lease.epoch
        state.issued_at = lease.issued_at
        state.expires_at = lease.expires_at
        state.signature = lease.signature
        state.save()
    return _reply_lease(lease)


@require_GET
def status(request):
    state = LeaseState.objects.filter(pk=1).first()
    response = _reply_lease(_current_lease(state)) if state and state.epoch else JsonResponse({"lease": None, "witness_time": int(utcnow().timestamp())})
    return _cors_status(response, request)


def cors_preflight(request):
    response = JsonResponse({"error_code": "not_allowed"}, status=405)
    origin = request.headers.get("Origin", "")
    if origin in settings.WITNESS_PUBLIC_STATUS_ORIGINS:
        response["Access-Control-Allow-Origin"] = origin
        response["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response["Access-Control-Allow-Headers"] = "Content-Type"
        response["Vary"] = "Origin"
    response["Cache-Control"] = "no-store"
    return response
