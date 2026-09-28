from django.shortcuts import render

# Create your views here.
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.conf import settings
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET

from .auth_api import user_payload


def _page_context():
    standby_origin = settings.TAVERN_DR_STANDBY_ORIGIN
    dr_enabled = bool(settings.TAVERN_DR_ENABLED and settings.TAVERN_DR_PEER_URL and standby_origin)
    return {
        "dr_enabled": dr_enabled,
        "dr_standby_status_url": f"{standby_origin}/api/disaster-recovery/status/" if dr_enabled else "",
        "dr_standby_node_id": settings.TAVERN_DR_STANDBY_NODE_ID if dr_enabled else "",
        "dr_standby_origin": standby_origin if dr_enabled else "",
    }


@require_GET
def home(request):
    if not request.user.is_authenticated:
        return redirect("/login/")
    return redirect("/app/")


@never_cache
@ensure_csrf_cookie
@require_GET
def login_page(request):
    if request.user.is_authenticated and not user_payload(request.user)["policy_consent_required"]:
        return redirect("/app/")
    return render(request, "login.html", _page_context())


@never_cache
@require_GET
def app_page(request):
    if not request.user.is_authenticated:
        return redirect("/login/?next=/app/")
    if user_payload(request.user)["policy_consent_required"]:
        return redirect("/login/?consent_required=1")
    return render(request, "index.html", _page_context())


@never_cache
@ensure_csrf_cookie
@require_GET
def admin_page(request):
    return render(request, "admin.html")


@require_GET
def health(request):
    from .site_federation_client import dual_federation_registered

    registered = dual_federation_registered()
    return JsonResponse({
        "ok": True,
        "registered": registered,
        "federation": "registered" if registered else "unregistered",
    })
