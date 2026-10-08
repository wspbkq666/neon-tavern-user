import json
import uuid

from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .auth_api import body_or_error
from .character_api import authentication_error
from .bundle_transfer import ImportValidationError, commit_bundle, export_bundle, parse_bundle


@require_POST
def export_bundle_api(request):
    if error := authentication_error(request):
        return error
    data, error = body_or_error(request)
    if error:
        return error
    if set(data) - {"character_ids", "worldbook_ids"}:
        return JsonResponse({"error": "整合包导出选项无效"}, status=400)
    try:
        payload = export_bundle(request.user, data.get("character_ids", []), data.get("worldbook_ids", []))
    except ImportValidationError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    response = JsonResponse(payload, json_dumps_params={"ensure_ascii": False}, content_type="application/json; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="neon-tavern-bundle.json"'
    return response


@require_POST
def import_preview(request):
    if error := authentication_error(request):
        return error
    data, error = body_or_error(request)
    if error:
        return error
    if set(data) != {"payload"}:
        return JsonResponse({"error": "整合包请求内容无效"}, status=400)
    try:
        preview = parse_bundle(data["payload"], request.user)
    except ImportValidationError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse(preview.as_dict(), json_dumps_params={"ensure_ascii": False})


@require_POST
def import_commit(request):
    if error := authentication_error(request):
        return error
    data, error = body_or_error(request)
    if error:
        return error
    if set(data)-{'payload','idempotency_key','task_id'} or 'payload' not in data:
        return JsonResponse({"error": "整合包请求内容无效"}, status=400)
    try:
        from .import_history import commit_import_batch
        result = commit_import_batch(request.user,data['payload'],data.get('idempotency_key') or uuid.uuid4().hex,data.get('task_id'))
    except ImportValidationError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except (ValueError, TypeError, RecursionError, json.JSONDecodeError) as exc:
        return JsonResponse({"error": "整合包内容无效，未导入任何素材"}, status=400)
    return JsonResponse(result, status=201, json_dumps_params={"ensure_ascii": False})
