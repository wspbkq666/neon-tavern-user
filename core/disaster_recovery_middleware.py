from django.conf import settings
from django.http import JsonResponse

from .disaster_recovery import WriteLeaseUnavailable, is_read_only_node, require_write_lease


class DisasterRecoveryWriteMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if getattr(settings, "TAVERN_DR_ENABLED", False) and request.method not in {"GET", "HEAD", "OPTIONS"}:
            if request.path == "/api/disaster-recovery/replica/" and is_read_only_node():
                return self.get_response(request)
            try:
                request.disaster_recovery_lease = require_write_lease()
            except WriteLeaseUnavailable as exc:
                return JsonResponse({"error_code": "write_lease_unavailable", "error": str(exc)}, status=503)
        return self.get_response(request)
