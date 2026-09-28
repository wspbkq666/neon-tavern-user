import httpx
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .auth_api import body_or_error
from .bundle_transfer import ImportValidationError, parse_bundle
from .character_api import authentication_error
from .settings_api import active_api_key, effective_values
from .smart_import import analyze_document, normalize_smart_import
from .smart_import_extract import SmartImportInputError, extract_document


@require_POST
def preview(request):
    if error := authentication_error(request):
        return error

    if request.FILES:
        if set(request.FILES) != {"file"} or request.POST:
            return JsonResponse({"error": "请只上传一个 TXT、MD、DOCX 或 DOC 文件"}, status=400)
        try:
            extracted = extract_document(upload=request.FILES["file"])
        except SmartImportInputError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
    else:
        data, error = body_or_error(request)
        if error:
            return error
        if set(data) != {"text"} or not isinstance(data.get("text"), str):
            return JsonResponse({"error": "请粘贴文字或选择一个文件"}, status=400)
        try:
            extracted = extract_document(text=data["text"])
        except SmartImportInputError as exc:
            return JsonResponse({"error": str(exc)}, status=400)

    api_key = active_api_key(request.user)
    if not api_key:
        return JsonResponse({"error": "请先在 AI 设置中配置 API 密钥，再使用智能导入"}, status=400)
    options = effective_values(request.user)

    try:
        result = analyze_document(extracted.text, options=options, api_key=api_key)
    except (RuntimeError, httpx.HTTPError, TimeoutError):
        return JsonResponse({"error": "AI 解析暂时失败，请检查模型设置后重试"}, status=502)
    except (ValueError, TypeError, RecursionError):
        return JsonResponse({"error": "AI 返回内容无法解析，请重试或缩短文档"}, status=400)

    try:
        drafts, payload = normalize_smart_import(result)
        bundle_preview = parse_bundle(payload, request.user)
    except (ImportValidationError, ValueError, TypeError, RecursionError):
        return JsonResponse({"error": "AI 整理结果未通过导入校验，请检查内容后重试"}, status=400)

    warnings = list(dict.fromkeys([
        *extracted.warnings,
        *(warning for draft in drafts for warning in draft["warnings"]),
        *bundle_preview.warnings,
    ]))
    return JsonResponse(
        {
            "drafts": drafts,
            "payload": payload,
            "bundle_preview": bundle_preview.as_dict(),
            "warnings": warnings,
        },
        json_dumps_params={"ensure_ascii": False},
    )
