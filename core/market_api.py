import json
import uuid
from datetime import timedelta
from urllib.parse import urlencode, urlsplit

import httpx
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .auth_api import body_or_error
from .bundle_transfer import commit_bundle, export_bundle, parse_bundle
from .character_api import _ensure_category_path, _native_character, _parse_character_payload, authentication_error
from .character_lore import EXTENDED_FIELDS
from .market_signing import (
    create_and_store_site_keypair,
    generate_site_keypair,
    public_key_for_site,
    signed_headers,
    site_market_configuration,
    site_private_key,
    verify_signed_request,
)
from .models import (
    Character,
    CharacterCategory,
    MarketListing,
    MarketReport,
    MarketSiteCredential,
    SiteSettings,
    Worldbook,
)
from .settings_api import decrypt_key, encrypt_key
from .worldbook_transfer import ImportValidationError, commit_import, export_native, parse_import


MAX_MARKET_BYTES = 5 * 1024 * 1024
MAX_TAGS = 20


def _category_path(category):
    names = [category.name]
    node = category
    seen = {str(node.pk)}
    while node.parent_id:
        node = node.parent
        if str(node.pk) in seen or len(names) >= 50:
            break
        seen.add(str(node.pk))
        names.append(node.name)
    return "/".join(reversed(names))


def _character_snapshot(character):
    item = {
        **{key:getattr(character,key) for key in EXTENDED_FIELDS},
        "name": character.name,
        "summary": character.summary,
        "personality": character.personality,
        "speech_habits": character.speech_habits,
        "memories": character.memories,
        "relationship_notes": character.relationship_notes,
        "state_fields": character.state_fields,
        "affinity": character.affinity,
        "clothing_type": character.clothing_type,
        "clothing_state": character.clothing_state,
        "is_player_controlled": character.is_player_controlled,
        "categories": [_category_path(category) for category in character.categories.all()],
    }
    warnings = ["角色头像不会随素材市场条目传输。"] if character.avatar else []
    return {"format": "neon-tavern-character", "version": 1, "characters": [item]}, warnings


def _worldbook_snapshot(book):
    payload = export_native(book)
    omitted_characters = 0
    omitted_conversations = 0
    for entry in payload["entries"]:
        omitted_characters += len(entry.get("scoped_character_ids", []))
        omitted_conversations += len(entry.get("scoped_conversation_ids", []))
        entry["scoped_character_ids"] = []
        entry["scoped_conversation_ids"] = []
    warnings = []
    if omitted_characters:
        warnings.append("指定角色范围不会带入市场素材；导入后需按本地角色重新设置。")
    if omitted_conversations:
        warnings.append("对话专属范围不会跨账号传输。")
    return payload, warnings


def _validate_listing_data(data):
    allowed = {"kind", "scope", "source_id", "character_ids", "worldbook_ids", "title", "description", "tags", "author_alias"}
    if not isinstance(data, dict) or set(data) - allowed:
        raise ImportValidationError("发布内容无效")
    kind = data.get("kind")
    scope = data.get("scope")
    if kind not in {MarketListing.CHARACTER, MarketListing.WORLDBOOK, MarketListing.BUNDLE} or scope not in {MarketListing.LOCAL, MarketListing.PUBLIC}:
        raise ImportValidationError("素材类型或发布范围无效")
    title = data.get("title")
    description = data.get("description", "")
    author_alias = data.get("author_alias", "")
    tags = data.get("tags", [])
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 120:
        raise ImportValidationError("标题需为 1 到 120 个字符")
    if not isinstance(description, str) or not description.strip():
        raise ImportValidationError("简介不能为空")
    if len(description) > 500:
        raise ImportValidationError("简介不能超过 500 个字符")
    if not isinstance(author_alias, str) or not author_alias.strip():
        raise ImportValidationError("署名不能为空")
    if len(author_alias.strip()) > 80:
        raise ImportValidationError("署名不能超过 80 个字符")
    if not isinstance(tags, list) or len(tags) > MAX_TAGS or any(not isinstance(tag, str) or len(tag.strip()) > 40 for tag in tags):
        raise ImportValidationError("标签最多 20 个，每个标签不能超过 40 个字符")
    result = {
        "kind": kind,
        "scope": scope,
        "title": title.strip(),
        "description": description.strip(),
        "author_alias": author_alias.strip(),
        "tags": list(dict.fromkeys(tag.strip() for tag in tags if tag.strip())),
    }
    if not result["tags"]:
        raise ImportValidationError("至少填写一个标签")
    if kind == MarketListing.BUNDLE:
        character_ids = data.get("character_ids", [])
        worldbook_ids = data.get("worldbook_ids", [])
        if not isinstance(character_ids, list) or not isinstance(worldbook_ids, list):
            raise ImportValidationError("整合包内容选择无效")
        if len(character_ids) > 100 or len(worldbook_ids) > 100 or any(not isinstance(value, str) or not value or len(value) > 80 for value in character_ids + worldbook_ids):
            raise ImportValidationError("整合包最多包含 100 张角色卡和 100 本世界书")
        result["character_ids"] = list(dict.fromkeys(character_ids))
        result["worldbook_ids"] = list(dict.fromkeys(worldbook_ids))
        if not result["character_ids"] and not result["worldbook_ids"]:
            raise ImportValidationError("整合包至少选择一张角色卡或一本世界书")
    else:
        source_id = data.get("source_id")
        if not isinstance(source_id, str) or not source_id or len(source_id) > 80:
            raise ImportValidationError("请选择要发布的素材")
        result["source_id"] = source_id
    return result


def _listing_payload(listing, *, include_payload=False):
    preview = ""
    counts = {}
    warnings = []
    if listing.kind == MarketListing.BUNDLE:
        characters = listing.payload.get("characters", [])
        worldbooks = listing.payload.get("worldbooks", [])
        preview = "、".join([row.get("name", "未命名角色") for row in characters[:3]] + [row.get("payload", {}).get("worldbook", {}).get("name", "未命名世界书") for row in worldbooks[:3]])
        warnings = listing.payload.get("warnings", [])
        counts = {"角色卡": len(characters), "世界书": len(worldbooks)}
    elif listing.kind == MarketListing.CHARACTER:
        item = (listing.payload.get("characters") or [{}])[0]
        preview = item.get("summary", "")
        warnings = listing.payload.get("warnings", [])
        counts = {"角色卡": len(listing.payload.get("characters", []))}
    else:
        book = listing.payload.get("worldbook", {})
        preview = book.get("description", "")
        warnings = listing.payload.get("warnings", [])
        counts = {"分类": len(listing.payload.get("categories", [])), "条目": len(listing.payload.get("entries", []))}
    result = {
        "id": str(listing.id),
        "kind": listing.kind,
        "scope": listing.scope,
        "title": listing.title,
        "description": listing.description,
        "preview": preview,
        "tags": listing.tags,
        "author_alias": listing.author_alias,
        "created_at": listing.created_at.isoformat(),
        "counts": counts,
        "warnings": warnings,
        "status": listing.status,
        "origin_site_id": str(listing.origin_site.site_id) if listing.origin_site_id else "",
    }
    if include_payload:
        result["payload"] = listing.payload
    return result


def _market_base_url(record):
    raw = record.public_market_url.strip().rstrip("/")
    if not raw:
        return ""
    parts = urlsplit(raw)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ImportValidationError("公共市场地址无效")
    return raw


def _remote_call(record, path, *, method="GET", payload=None):
    base = _market_base_url(record)
    if not base:
        raise ImportValidationError("尚未连接公共市场")
    body = b"" if payload is None else json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
    private_pem = site_private_key(record)
    if method != "GET":
        if not private_pem:
            raise ImportValidationError("尚未生成本站连接密钥，请在管理员设置中重新连接")
        headers.update(signed_headers(record.market_site_id, private_pem, method, path, body))
    try:
        response = httpx.request(method, f"{base}{path}", content=body if payload is not None else None, headers=headers, timeout=12, follow_redirects=False)
        if len(response.content) > MAX_MARKET_BYTES + 100_000:
            raise ImportValidationError("公共市场返回内容超过大小限制")
        try:
            data = response.json()
        except ValueError as exc:
            raise ImportValidationError("公共市场返回了无法读取的内容") from exc
    except httpx.HTTPError as exc:
        raise ImportValidationError("公共市场暂时无法连接，请检查地址和网络") from exc
    if not response.is_success:
        message = data.get("error") if isinstance(data, dict) else None
        raise ImportValidationError(message if isinstance(message, str) else f"公共市场请求失败（{response.status_code}）")
    return data


def _publish_snapshot(user, values):
    if values["kind"] == MarketListing.BUNDLE:
        payload = export_bundle(user, values["character_ids"], values["worldbook_ids"])
        payload["warnings"] = parse_bundle(payload, user).warnings
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(serialized) > MAX_MARKET_BYTES:
            raise ImportValidationError("整合包市场条目不能超过 5 MB")
        return payload
    if values["kind"] == MarketListing.CHARACTER:
        item = get_object_or_404(Character, pk=values["source_id"], owner=user)
        payload, warnings = _character_snapshot(item)
    else:
        item = get_object_or_404(Worldbook, pk=values["source_id"], owner=user)
        payload, warnings = _worldbook_snapshot(item)
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(serialized) > MAX_MARKET_BYTES:
        raise ImportValidationError("单个素材市场条目不能超过 5 MB")
    payload["warnings"] = warnings
    return payload


def _create_listing(*, owner, site, scope, kind, title, description, tags, author_alias, payload, source_item_id=""):
    return MarketListing.objects.create(
        owner=owner,
        origin_site=site,
        source_item_id=source_item_id,
        scope=scope,
        kind=kind,
        title=title,
        description=description,
        tags=tags,
        author_alias=author_alias,
        payload=payload,
    )


@require_http_methods(["GET", "POST"])
def listings(request):
    if error := authentication_error(request):
        return error
    if request.method == "GET":
        scope = request.GET.get("scope", MarketListing.LOCAL)
        kind = request.GET.get("kind", "")
        query = request.GET.get("q", "").strip()[:100]
        if scope not in {MarketListing.LOCAL, MarketListing.PUBLIC}:
            return JsonResponse({"error": "市场范围无效"}, status=400)
        record = site_market_configuration()
        if scope == MarketListing.PUBLIC and _market_base_url(record):
            query_string = f"?{urlencode({'kind': kind, 'q': query})}"
            try:
                data = _remote_call(record, f"/api/market/public/v1/listings/{query_string}")
            except ImportValidationError as exc:
                return JsonResponse({"error": str(exc), "listings": []}, status=503)
            listings = data.get("listings", [])
            for item in listings:
                item["can_withdraw"] = item.get("origin_site_id") == str(record.market_site_id)
            return JsonResponse({"listings": listings, "remote": True})
        rows = MarketListing.objects.filter(scope=scope, status=MarketListing.PUBLISHED)
        if scope == MarketListing.LOCAL:
            rows = rows.filter(owner=request.user)
        if kind in {MarketListing.CHARACTER, MarketListing.WORLDBOOK, MarketListing.BUNDLE}:
            rows = rows.filter(kind=kind)
        if query:
            rows = rows.filter(title__icontains=query) | rows.filter(description__icontains=query)
        visible_rows = list(rows.distinct()[:100])
        listings = [_listing_payload(row) for row in visible_rows]
        for item, row in zip(listings, visible_rows):
            item["can_withdraw"] = row.owner_id == request.user.id
        return JsonResponse({"listings": listings, "remote": False})

    data, error = body_or_error(request)
    if error:
        return error
    try:
        values = _validate_listing_data(data)
        if MarketListing.objects.filter(owner=request.user, status=MarketListing.PUBLISHED).count() >= 500:
            raise ImportValidationError("每个账号最多发布 500 个素材条目")
        snapshot = _publish_snapshot(request.user, values)
        author = values["author_alias"] or request.user.username[:80]
        if values["scope"] == MarketListing.PUBLIC:
            record = site_market_configuration()
            base = _market_base_url(record)
            if base:
                source_id = str(uuid.uuid4())
                remote = _remote_call(record, "/api/market/public/v1/listings/submit/", method="POST", payload={
                    "source_item_id": source_id,
                    "kind": values["kind"],
                    "title": values["title"],
                    "description": values["description"],
                    "tags": values["tags"],
                    "author_alias": author,
                    "payload": snapshot,
                })
                listing = _create_listing(
                    owner=request.user, site=None, scope=MarketListing.PUBLIC, kind=values["kind"],
                    title=values["title"], description=values["description"], tags=values["tags"],
                    author_alias=author, payload=snapshot, source_item_id=source_id,
                )
                listing.remote_listing_id = str(remote["id"])
                listing.save(update_fields=["remote_listing_id", "updated_at"])
            else:
                listing = _create_listing(
                    owner=request.user, site=None, scope=MarketListing.PUBLIC, kind=values["kind"],
                    title=values["title"], description=values["description"], tags=values["tags"],
                    author_alias=author, payload=snapshot,
                )
        else:
            listing = _create_listing(
                owner=request.user, site=None, scope=MarketListing.LOCAL, kind=values["kind"],
                title=values["title"], description=values["description"], tags=values["tags"],
                author_alias=author, payload=snapshot,
            )
    except ImportValidationError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Http404:
        return JsonResponse({"error": "所选素材不存在或不属于当前账号"}, status=404)
    except (ValueError, TypeError, KeyError) as exc:
        return JsonResponse({"error": "发布内容无效，请检查素材和填写内容"}, status=400)
    return JsonResponse(_listing_payload(listing), status=201)


@require_GET
def listing_detail(request, listing_id):
    if error := authentication_error(request):
        return error
    scope = request.GET.get("scope", MarketListing.LOCAL)
    if scope == MarketListing.PUBLIC:
        record = site_market_configuration()
        if _market_base_url(record):
            try:
                data = _remote_call(record, f"/api/market/public/v1/listings/{listing_id}/")
            except ImportValidationError as exc:
                return JsonResponse({"error": str(exc)}, status=503)
            data["can_withdraw"] = data.get("origin_site_id") == str(record.market_site_id)
            return JsonResponse(data)
    listing = get_object_or_404(MarketListing, pk=listing_id, scope=scope, status=MarketListing.PUBLISHED)
    if scope == MarketListing.LOCAL and listing.owner_id != request.user.id:
        return JsonResponse({"error": "未找到素材"}, status=404)
    payload = _listing_payload(listing, include_payload=True)
    payload["can_withdraw"] = listing.owner_id == request.user.id
    return JsonResponse(payload)


def _import_character_snapshot(owner, payload):
    preview = _parse_character_payload(payload)
    imported = []
    for raw in preview["characters"]:
        item = _native_character(raw)
        name = item.pop("name")
        categories = item.pop("categories")
        base_name = name[:60]
        name = base_name
        suffix = 2
        while Character.objects.filter(owner=owner, name=name).exists():
            tail = f"（副本 {suffix}）"
            name = f"{base_name[:60 - len(tail)]}{tail}"
            suffix += 1
        character = Character.objects.create(owner=owner, name=name, **item)
        for path in categories:
            category = _ensure_category_path(owner, path)
            if category:
                category.characters.add(character)
        imported.append({"id": str(character.id), "name": character.name})
    return {"characters": imported, "worldbooks": [], "warnings": payload.get("warnings", [])}


def _import_worldbook_snapshot(owner, payload):
    parsed = parse_import(payload, "native")
    raw_warnings = payload.get("warnings", [])
    warnings = [item for item in raw_warnings if isinstance(item, str)][:20] if isinstance(raw_warnings, list) else []
    for entry in parsed.entries:
        if entry["scoped_character_ids"] or entry["scoped_conversation_ids"]:
            warnings.append("外部角色或对话范围未导入；可在本站按需重新设置。")
        entry["scoped_character_ids"] = []
        entry["scoped_conversation_ids"] = []
    book = commit_import(parsed, owner, conflict_policy="keep_both")
    return {"characters": [], "worldbooks": [{"id": str(book.id), "name": book.name}], "warnings": list(dict.fromkeys(warnings + parsed.warnings))}


@require_POST
def download_listing(request, listing_id):
    if error := authentication_error(request):
        return error
    data, error = body_or_error(request)
    if error:
        return error
    scope = data.get("scope", MarketListing.LOCAL)
    try:
        if scope == MarketListing.PUBLIC and _market_base_url(site_market_configuration()):
            remote = _remote_call(site_market_configuration(), f"/api/market/public/v1/listings/{listing_id}/download/")
            kind, payload = remote["kind"], remote["payload"]
        else:
            listing = get_object_or_404(MarketListing, pk=listing_id, scope=scope, status=MarketListing.PUBLISHED)
            if scope == MarketListing.LOCAL and listing.owner_id != request.user.id:
                return JsonResponse({"error": "未找到素材"}, status=404)
            kind, payload = listing.kind, listing.payload
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(serialized) > MAX_MARKET_BYTES:
            raise ImportValidationError("素材市场条目不能超过 5 MB")
        with transaction.atomic():
            if kind == MarketListing.BUNDLE:
                preview = parse_bundle(payload, request.user)
                result = commit_bundle(preview, request.user)
                result["warnings"] = list(dict.fromkeys((result.get("warnings") or []) + [item for item in payload.get("warnings", []) if isinstance(item, str)]))
            elif kind == MarketListing.CHARACTER:
                result = _import_character_snapshot(request.user, payload)
            else:
                result = _import_worldbook_snapshot(request.user, payload)
    except ImportValidationError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        return JsonResponse({"error": "素材内容无效，未导入任何数据"}, status=400)
    return JsonResponse(result, status=201, json_dumps_params={"ensure_ascii": False})


@require_POST
def withdraw_listing(request, listing_id):
    if error := authentication_error(request):
        return error
    listing = get_object_or_404(
        MarketListing.objects.filter(owner=request.user, status=MarketListing.PUBLISHED).filter(
            Q(pk=listing_id) | Q(remote_listing_id=str(listing_id))
        )
    )
    if listing.scope == MarketListing.PUBLIC and listing.remote_listing_id:
        record = site_market_configuration()
        try:
            _remote_call(record, f"/api/market/public/v1/listings/{listing.remote_listing_id}/withdraw/", method="POST", payload={"source_item_id": listing.source_item_id})
        except ImportValidationError as exc:
            return JsonResponse({"error": str(exc)}, status=503)
    listing.status = MarketListing.WITHDRAWN
    listing.save(update_fields=["status", "updated_at"])
    return JsonResponse({"ok": True})


@require_POST
def report_listing(request, listing_id):
    if error := authentication_error(request):
        return error
    data, error = body_or_error(request)
    if error:
        return error
    reason = data.get("reason")
    scope = data.get("scope", MarketListing.LOCAL)
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 500:
        return JsonResponse({"error": "请填写举报原因（不超过 500 个字符）"}, status=400)
    if scope == MarketListing.PUBLIC and _market_base_url(site_market_configuration()):
        try:
            _remote_call(site_market_configuration(), "/api/market/public/v1/reports/", method="POST", payload={
                "listing_id": str(listing_id), "reason": reason.strip(), "reporter_alias": request.user.username[:80],
            })
        except ImportValidationError as exc:
            return JsonResponse({"error": str(exc)}, status=503)
        return JsonResponse({"ok": True}, status=201)
    listing = get_object_or_404(MarketListing, pk=listing_id, scope=scope, status=MarketListing.PUBLISHED)
    if scope == MarketListing.LOCAL and listing.owner_id == request.user.id:
        return JsonResponse({"error": "不能举报自己的素材"}, status=400)
    MarketReport.objects.create(listing=listing, reporter=request.user, reason=reason.strip())
    return JsonResponse({"ok": True}, status=201)


@require_GET
def public_listings(request):
    query = request.GET.get("q", "").strip()[:100]
    kind = request.GET.get("kind", "")
    rows = MarketListing.objects.filter(scope=MarketListing.PUBLIC, status=MarketListing.PUBLISHED)
    if kind in {MarketListing.CHARACTER, MarketListing.WORLDBOOK, MarketListing.BUNDLE}:
        rows = rows.filter(kind=kind)
    if query:
        rows = rows.filter(title__icontains=query) | rows.filter(description__icontains=query)
    return JsonResponse({"listings": [_listing_payload(row) for row in rows.distinct()[:100]]})


@require_GET
def public_listing_detail(request, listing_id):
    listing = get_object_or_404(MarketListing, pk=listing_id, scope=MarketListing.PUBLIC, status=MarketListing.PUBLISHED)
    return JsonResponse(_listing_payload(listing, include_payload=True))


@require_GET
def public_listing_download(request, listing_id):
    listing = get_object_or_404(MarketListing, pk=listing_id, scope=MarketListing.PUBLIC, status=MarketListing.PUBLISHED)
    return JsonResponse({"id": str(listing.id), "kind": listing.kind, "payload": listing.payload}, json_dumps_params={"ensure_ascii": False})


@csrf_exempt
@require_POST
def public_site_register(request):
    data, error = body_or_error(request)
    if error:
        return error
    if set(data) != {"site_id", "name", "public_key"}:
        return JsonResponse({"error": "站点登记内容无效"}, status=400)
    try:
        site_id = uuid.UUID(str(data["site_id"]))
        public_key = data["public_key"]
        from .market_signing import encode_public_key
        encode_public_key(public_key)
    except (ValueError, TypeError, AttributeError):
        return JsonResponse({"error": "站点编号或公钥无效"}, status=400)
    name = data["name"]
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
        return JsonResponse({"error": "站点名称需为 1 到 120 个字符"}, status=400)
    existing = MarketSiteCredential.objects.filter(site_id=site_id).first()
    if existing:
        if existing.public_key != public_key:
            return JsonResponse({"error": "此站点编号已登记其他公钥，请联系市场管理员撤销旧登记"}, status=409)
        return JsonResponse({"site_id": str(site_id), "status": existing.status})
    credential = MarketSiteCredential.objects.create(site_id=site_id, name=name.strip(), public_key=public_key)
    return JsonResponse({"site_id": str(site_id), "status": credential.status}, status=201)


@require_GET
def public_site_status(request, site_id):
    credential = get_object_or_404(MarketSiteCredential, site_id=site_id)
    return JsonResponse({"site_id": str(credential.site_id), "status": credential.status})


@csrf_exempt
@require_POST
def public_listing_submit(request):
    site, error = verify_signed_request(request)
    if error:
        return JsonResponse({"error": error}, status=401)
    data, error = body_or_error(request)
    if error:
        return error
    allowed = {"source_item_id", "kind", "title", "description", "tags", "author_alias", "payload"}
    if set(data) != allowed:
        return JsonResponse({"error": "发布内容无效"}, status=400)
    if MarketListing.objects.filter(origin_site=site, source_item_id=str(data["source_item_id"])).exists():
        return JsonResponse({"error": "该素材已发布"}, status=409)
    try:
        values = _validate_listing_data({
            "kind": data["kind"], "scope": MarketListing.PUBLIC, "source_id": str(data["source_item_id"]),
            "character_ids": [], "worldbook_ids": [],
            "title": data["title"], "description": data["description"], "tags": data["tags"],
            "author_alias": data["author_alias"],
        })
        payload = data["payload"]
        if not isinstance(payload, dict) or len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > MAX_MARKET_BYTES:
            raise ImportValidationError("单个素材市场条目不能超过 5 MB")
        if values["kind"] == MarketListing.CHARACTER:
            if len(_parse_character_payload(payload)["characters"]) != 1:
                raise ImportValidationError("一个角色卡市场条目只能包含一张角色卡")
        elif values["kind"] == MarketListing.WORLDBOOK:
            parse_import(payload, "native")
        else:
            parse_bundle(payload, None)
        if MarketListing.objects.filter(origin_site=site).count() >= 500:
            raise ImportValidationError("每个站点最多发布 500 个公共素材条目")
        listing = _create_listing(
            owner=None, site=site, scope=MarketListing.PUBLIC, kind=values["kind"], title=values["title"],
            description=values["description"], tags=values["tags"], author_alias=values["author_alias"],
            payload=payload, source_item_id=str(data["source_item_id"]),
        )
    except ImportValidationError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except (ValueError, TypeError, KeyError, IntegrityError):
        return JsonResponse({"error": "发布内容无效或重复"}, status=400)
    return JsonResponse(_listing_payload(listing), status=201)


@csrf_exempt
@require_POST
def public_listing_withdraw(request, listing_id):
    site, error = verify_signed_request(request)
    if error:
        return JsonResponse({"error": error}, status=401)
    data, error = body_or_error(request)
    if error:
        return error
    listing = get_object_or_404(MarketListing, pk=listing_id, scope=MarketListing.PUBLIC, origin_site=site, status=MarketListing.PUBLISHED)
    if data.get("source_item_id") != listing.source_item_id:
        return JsonResponse({"error": "无权撤回此素材"}, status=403)
    listing.status = MarketListing.WITHDRAWN
    listing.save(update_fields=["status", "updated_at"])
    return JsonResponse({"ok": True})


@csrf_exempt
@require_POST
def public_report_submit(request):
    site, error = verify_signed_request(request)
    if error:
        return JsonResponse({"error": error}, status=401)
    data, error = body_or_error(request)
    if error:
        return error
    if set(data) != {"listing_id", "reason", "reporter_alias"}:
        return JsonResponse({"error": "举报内容无效"}, status=400)
    reason = data["reason"]
    alias = data["reporter_alias"]
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 500 or not isinstance(alias, str) or len(alias) > 80:
        return JsonResponse({"error": "举报内容无效"}, status=400)
    listing = get_object_or_404(MarketListing, pk=data["listing_id"], scope=MarketListing.PUBLIC, status=MarketListing.PUBLISHED)
    site_label = site.name[:30]
    recent = MarketReport.objects.filter(
        reporter_site__startswith=f"{site_label} ·", created_at__gte=timezone.now() - timedelta(hours=1)
    ).count()
    if recent >= 10:
        return JsonResponse({"error": "本站点每小时最多提交 10 条举报"}, status=429)
    MarketReport.objects.create(listing=listing, reporter_site=f"{site_label} · {alias[:40]}", reason=reason.strip())
    return JsonResponse({"ok": True}, status=201)
