import httpx
import json
from queue import Empty, Queue
from threading import Thread

from django.db import close_old_connections
from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.http import require_POST

from .auth_api import body_or_error
from .bundle_transfer import ImportValidationError, parse_bundle
from .character_api import authentication_error
from .settings_api import active_api_key, effective_values
from .smart_import import analyze_document, normalize_smart_import
from .smart_import_extract import SmartImportInputError, extract_document


class SmartImportPreviewError(Exception):
    def __init__(self, message, status):
        super().__init__(message)
        self.status = status


def _extract_request_document(request):
    if request.FILES:
        if set(request.FILES) != {"file"} or request.POST:
            return None, JsonResponse({"error": "请只上传一个 TXT、MD、DOCX 或 DOC 文件"}, status=400)
        try:
            return extract_document(upload=request.FILES["file"]), None
        except SmartImportInputError as exc:
            return None, JsonResponse({"error": str(exc)}, status=400)

    data, error = body_or_error(request)
    if error:
        return None, error
    if set(data) != {"text"} or not isinstance(data.get("text"), str):
        return None, JsonResponse({"error": "请粘贴文字或选择一个文件"}, status=400)
    try:
        return extract_document(text=data["text"]), None
    except SmartImportInputError as exc:
        return None, JsonResponse({"error": str(exc)}, status=400)


def _build_preview_data(extracted, user, api_key, options, *, on_progress=None, on_raw_chunk=None):
    try:
        result = analyze_document(
            extracted.text,
            options=options,
            api_key=api_key,
            on_progress=on_progress,
            on_raw_chunk=on_raw_chunk,
        )
    except (RuntimeError, httpx.HTTPError, TimeoutError) as exc:
        raise SmartImportPreviewError("AI 解析暂时失败，请检查模型设置后重试", 502) from exc
    except (ValueError, TypeError, RecursionError) as exc:
        raise SmartImportPreviewError("AI 返回内容无法解析，请重试或缩短文档", 400) from exc

    try:
        drafts, payload = normalize_smart_import(result)
        if payload["characters"] or payload["worldbooks"]:
            bundle_preview = parse_bundle(payload, user)
            bundle_preview_data = bundle_preview.as_dict()
            bundle_warnings = bundle_preview.warnings
        else:
            bundle_preview_data = {
                "format": payload["format"],
                "version": payload["version"],
                "characters": [],
                "worldbooks": [],
                "counts": {"characters": 0, "worldbooks": 0, "worldbook_categories": 0, "worldbook_entries": 0},
                "warnings": [],
            }
            bundle_warnings = []
    except (ImportValidationError, ValueError, TypeError, RecursionError) as exc:
        raise SmartImportPreviewError("AI 整理结果未通过导入校验，请检查内容后重试", 400) from exc

    warnings = list(dict.fromkeys([
        *extracted.warnings,
        *(warning for draft in drafts for warning in draft["warnings"]),
        *bundle_warnings,
    ]))
    return {
        "drafts": drafts,
        "payload": payload,
        "bundle_preview": bundle_preview_data,
        "warnings": warnings,
    }


@require_POST
def preview(request):
    if error := authentication_error(request):
        return error

    extracted, error = _extract_request_document(request)
    if error:
        return error

    user = request.user
    api_key = active_api_key(user)
    if not api_key:
        return JsonResponse({"error": "请先在 AI 设置中配置 API 密钥，再使用智能导入"}, status=400)
    options = effective_values(user)

    try:
        result = _build_preview_data(extracted, user, api_key, options)
    except SmartImportPreviewError as exc:
        return JsonResponse({"error": str(exc)}, status=exc.status)
    return JsonResponse(result, json_dumps_params={"ensure_ascii": False})


def _sse_event(event):
    return f"data: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n\n"


@require_POST
def preview_stream(request):
    if error := authentication_error(request):
        return error

    extracted, error = _extract_request_document(request)
    if error:
        return error

    user = request.user
    api_key = active_api_key(user)
    if not api_key:
        return JsonResponse({"error": "请先在 AI 设置中配置 API 密钥，再使用智能导入"}, status=400)
    options = effective_values(user)
    events = Queue()
    done = object()

    def run_preview():
        close_old_connections()
        try:
            result = _build_preview_data(
                extracted,
                user,
                api_key,
                options,
                on_progress=lambda details: events.put({"type": "progress", **details}),
                on_raw_chunk=lambda text: events.put({"type": "raw_delta", "text": text}),
            )
            events.put({"type": "complete", "result": result})
        except SmartImportPreviewError as exc:
            events.put({"type": "error", "message": str(exc)})
        except Exception:
            events.put({"type": "error", "message": "识别过程发生异常，请查看日志后重试"})
        finally:
            close_old_connections()
            events.put(done)

    def stream_events():
        yield _sse_event({"type": "progress", "stage": "document_ready", "characters": len(extracted.text), "warnings": extracted.warnings})
        worker = Thread(target=run_preview, name="smart-import-preview", daemon=True)
        worker.start()
        while True:
            try:
                event = events.get(timeout=12)
            except Empty:
                yield ": keep-alive\n\n"
                continue
            if event is done:
                break
            yield _sse_event(event)

    response = StreamingHttpResponse(stream_events(), content_type="text/event-stream; charset=utf-8")
    response["Cache-Control"] = "no-cache, no-transform"
    response["X-Accel-Buffering"] = "no"
    return response
