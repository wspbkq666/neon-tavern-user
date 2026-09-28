from django.http import JsonResponse
from django.views.decorators.http import require_http_methods, require_POST

from .auth_api import body_or_error
from .character_api import authentication_error
from .models import UserProfile, UserWarning


@require_http_methods(["GET", "PATCH"])
def profile(request):
    error = authentication_error(request)
    if error:
        return error
    record, _ = UserProfile.objects.get_or_create(user=request.user)
    if request.method == "PATCH":
        data, error = body_or_error(request)
        if error:
            return error
        traits = data.get("inferred_traits")
        if set(data) != {"inferred_traits"} or not isinstance(traits, str) or len(traits) > 10000:
            return JsonResponse({"error": "画像内容无效"}, status=400)
        record.inferred_traits = traits
        record.save(update_fields=["inferred_traits", "updated_at"])
    warnings = list(request.user.admin_warnings.filter(is_read=False).order_by("created_at", "id").values(
        "id", "message", "created_at",
    ))
    for warning in warnings:
        warning["created_at"] = warning["created_at"].isoformat()
    return JsonResponse({
        "inferred_traits": record.inferred_traits,
        "updated_at": record.updated_at.isoformat(),
        "warnings": warnings,
    })


@require_POST
def acknowledge_warnings(request):
    error = authentication_error(request)
    if error:
        return error
    data, error = body_or_error(request)
    if error:
        return error
    ids = data.get("ids") if set(data) == {"ids"} else None
    if not isinstance(ids, list) or len(ids) > 100 or any(type(value) is not int for value in ids):
        return JsonResponse({"error": "通知编号无效"}, status=400)
    updated = UserWarning.objects.filter(user=request.user, pk__in=ids, is_read=False).update(is_read=True)
    return JsonResponse({"ok": True, "read": updated})
