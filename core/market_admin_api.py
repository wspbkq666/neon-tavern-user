import hashlib
import uuid
import httpx

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .auth_api import body_or_error
from .character_api import authentication_error
from .market_api import _market_base_url
from .market_signing import (
    create_and_store_site_keypair,
    generate_site_keypair,
    public_key_for_site,
    site_market_configuration,
)
from .models import MarketListing, MarketReport, MarketSiteCredential
from .settings_api import encrypt_key


def _staff_error(request):
    error = authentication_error(request)
    if error:
        return error
    if not request.user.is_staff:
        return JsonResponse({"error": "无权限"}, status=403)
    return None


def _site_summary(site):
    return {
        "id": str(site.id),
        "site_id": str(site.site_id),
        "name": site.name,
        "status": site.status,
        "fingerprint": hashlib.sha256(site.public_key.encode("ascii")).hexdigest()[:20],
        "created_at": site.created_at.isoformat(),
        "approved_at": site.approved_at.isoformat() if site.approved_at else None,
    }


@require_http_methods(["GET", "PUT"])
def market_connection(request):
    if error := _staff_error(request):
        return error
    record = site_market_configuration()
    if request.method == "GET":
        return JsonResponse({
            "market_url": record.public_market_url,
            "site_id": str(record.market_site_id),
            "public_key": public_key_for_site(record),
            "has_private_key": bool(record.encrypted_market_private_key),
            "is_market_host": not bool(record.public_market_url),
        })

    data, error = body_or_error(request)
    if error:
        return error
    if set(data) - {"market_url", "rotate_key"}:
        return JsonResponse({"error": "公共市场设置无效"}, status=400)
    market_url = data["market_url"]
    if not isinstance(market_url, str) or len(market_url) > 500:
        return JsonResponse({"error": "公共市场地址无效"}, status=400)
    rotate_key = data.get("rotate_key", False)
    if not isinstance(rotate_key, bool):
        return JsonResponse({"error": "密钥轮换选项无效"}, status=400)
    previous_url = record.public_market_url
    record.public_market_url = market_url.strip().rstrip("/")
    try:
        base = _market_base_url(record)
    except ValueError as exc:
        record.public_market_url = previous_url
        return JsonResponse({"error": str(exc)}, status=400)
    if base and (not record.encrypted_market_private_key or rotate_key):
        try:
            if rotate_key:
                private_pem, _ = generate_site_keypair()
                record.market_site_id = uuid.uuid4()
                record.encrypted_market_private_key = encrypt_key(private_pem)
                record.save(update_fields=["market_site_id", "encrypted_market_private_key", "updated_at"])
            else:
                create_and_store_site_keypair(record)
        except RuntimeError:
            record.public_market_url = previous_url
            return JsonResponse({"error": "服务器缺少加密配置，无法安全保存站点密钥"}, status=500)
    record.save(update_fields=["public_market_url", "updated_at"])
    if not base:
        return JsonResponse({"market_url": "", "site_status": "本机公共市场"})

    from .market_api import _remote_call
    try:
        remote_status = _remote_call(record, f"/api/market/public/v1/sites/{record.market_site_id}/")
    except Exception:
        try:
            response = httpx.post(
                f"{base}/api/market/public/v1/sites/register/",
                json={
                    "site_id": str(record.market_site_id),
                    "name": request.get_host()[:120],
                    "public_key": public_key_for_site(record),
                },
                timeout=12,
                follow_redirects=False,
            )
            if not response.is_success:
                try:
                    message = response.json().get("error")
                except ValueError:
                    message = None
                return JsonResponse({"error": message or f"登记请求失败（{response.status_code}）"}, status=502)
            remote_status = response.json()
        except httpx.HTTPError:
            return JsonResponse({"error": "公共市场暂时无法连接；本站地址已保存，可稍后重试"}, status=503)
    return JsonResponse({"market_url": base, "site_id": str(record.market_site_id), "site_status": remote_status.get("status", "pending")})


@require_http_methods(["GET", "POST"])
def market_sites(request):
    if error := _staff_error(request):
        return error
    if request.method == "GET":
        return JsonResponse({"sites": [_site_summary(site) for site in MarketSiteCredential.objects.all()]})
    data, error = body_or_error(request)
    if error:
        return error
    site_id, action = data.get("site_id"), data.get("action")
    try:
        site_id = uuid.UUID(str(site_id))
    except (ValueError, TypeError, AttributeError):
        return JsonResponse({"error": "站点编号无效"}, status=400)
    site = get_object_or_404(MarketSiteCredential, site_id=site_id)
    if action == "approve":
        site.status = MarketSiteCredential.APPROVED
        site.approved_at = timezone.now()
    elif action == "revoke":
        site.status = MarketSiteCredential.REVOKED
    else:
        return JsonResponse({"error": "操作无效"}, status=400)
    site.save(update_fields=["status", "approved_at", "updated_at"])
    return JsonResponse(_site_summary(site))


@require_http_methods(["GET", "POST"])
def market_reports(request):
    if error := _staff_error(request):
        return error
    if request.method == "GET":
        rows = MarketReport.objects.select_related("listing", "reporter", "listing__origin_site").order_by("status", "created_at")[:200]
        return JsonResponse({"reports": [{
            "id": row.id,
            "listing_id": str(row.listing_id),
            "title": row.listing.title,
            "scope": row.listing.scope,
            "kind": row.listing.kind,
            "reason": row.reason,
            "reporter": row.reporter.username if row.reporter_id else row.reporter_site or "跨站用户",
            "status": row.status,
            "created_at": row.created_at.isoformat(),
        } for row in rows]})
    data, error = body_or_error(request)
    if error:
        return error
    report = get_object_or_404(MarketReport.objects.select_related("listing"), pk=data.get("report_id"))
    action = data.get("action")
    if action == "hide":
        report.listing.status = MarketListing.HIDDEN
        report.listing.save(update_fields=["status", "updated_at"])
        report.status = MarketReport.REVIEWED
    elif action == "dismiss":
        report.status = MarketReport.DISMISSED
    else:
        return JsonResponse({"error": "操作无效"}, status=400)
    report.handled_at = timezone.now()
    report.save(update_fields=["status", "handled_at"])
    return JsonResponse({"ok": True})


@require_GET
def market_admin_listings(request):
    if error := _staff_error(request):
        return error
    scope = request.GET.get("scope", "all")
    rows = MarketListing.objects.select_related("owner", "origin_site")
    if scope in {MarketListing.LOCAL, MarketListing.PUBLIC}:
        rows = rows.filter(scope=scope)
    return JsonResponse({"listings": [{
        "id": str(row.id), "title": row.title, "kind": row.kind, "scope": row.scope,
        "author_alias": row.author_alias, "status": row.status,
        "origin": row.origin_site.name if row.origin_site_id else "本站",
        "created_at": row.created_at.isoformat(),
    } for row in rows.order_by("-created_at")[:200]]})


@require_POST
def moderate_listing(request, listing_id):
    if error := _staff_error(request):
        return error
    data, error = body_or_error(request)
    if error:
        return error
    listing = get_object_or_404(MarketListing, pk=listing_id)
    action = data.get("action")
    if action == "hide":
        listing.status = MarketListing.HIDDEN
    elif action == "restore":
        listing.status = MarketListing.PUBLISHED
    else:
        return JsonResponse({"error": "操作无效"}, status=400)
    listing.save(update_fields=["status", "updated_at"])
    return JsonResponse({"ok": True, "status": listing.status})
