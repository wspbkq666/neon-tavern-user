from django.urls import path

from witness_core import api


urlpatterns = [
    path("api/lease/acquire/", api.acquire),
    path("api/lease/renew/", api.renew),
    path("api/lease/handoff/", api.handoff),
    path("api/lease/status/", api.status),
]
