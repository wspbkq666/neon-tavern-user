from django.http import JsonResponse
from django.shortcuts import redirect


class PolicyConsentMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            from .auth_api import user_payload

            if user_payload(request.user)["policy_consent_required"]:
                allowed = {
                    "/login/",
                    "/api/auth/me/",
                    "/api/auth/consent/",
                    "/api/auth/logout/",
                    "/api/health/",
                }
                if request.path not in allowed:
                    if request.path.startswith("/api/"):
                        return JsonResponse({
                            "error": "请先阅读并确认更新后的隐私协议",
                            "policy_consent_required": True,
                        }, status=428)
                    return redirect("/login/?consent_required=1")
        return self.get_response(request)
