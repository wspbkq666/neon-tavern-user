"""
URL configuration for tavern project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.urls import path

from core import auth_api, bundle_api, character_api, conversation_api, generation_api, market_admin_api, market_api, profile_api, settings_api, worldbook_api
from core import admin_api
from core import views
from core import disaster_recovery_api, disaster_recovery_transfer
from core import site_federation_client

urlpatterns = [
    path("", views.home),
    path("login/", views.login_page),
    path("app/", views.app_page),
    path("admin/", views.admin_page),
    path("api/health/", views.health),
    path("api/auth/me/", auth_api.session_info),
    path("api/auth/register/", auth_api.register),
    path("api/auth/login/", auth_api.sign_in),
    path("api/auth/logout/", auth_api.sign_out),
    path("api/auth/consent/", auth_api.accept_current_policy),
    path("api/disaster-recovery/status/", disaster_recovery_api.status),
    path("api/disaster-recovery/replica/", disaster_recovery_transfer.receive_snapshot),
    path("api/federation/status/", site_federation_client.federation_status),
    path("api/characters/", character_api.characters),
    path("api/characters/import/preview/", character_api.character_import_preview),
    path("api/characters/import/commit/", character_api.character_import_commit),
    path("api/bundles/export/", bundle_api.export_bundle_api),
    path("api/bundles/import/preview/", bundle_api.import_preview),
    path("api/bundles/import/commit/", bundle_api.import_commit),
    path("api/market/listings/", market_api.listings),
    path("api/market/listings/<uuid:listing_id>/", market_api.listing_detail),
    path("api/market/listings/<uuid:listing_id>/download/", market_api.download_listing),
    path("api/market/listings/<uuid:listing_id>/withdraw/", market_api.withdraw_listing),
    path("api/market/listings/<uuid:listing_id>/report/", market_api.report_listing),
    path("api/market/public/v1/listings/", market_api.public_listings),
    path("api/market/public/v1/listings/submit/", market_api.public_listing_submit),
    path("api/market/public/v1/listings/<uuid:listing_id>/", market_api.public_listing_detail),
    path("api/market/public/v1/listings/<uuid:listing_id>/download/", market_api.public_listing_download),
    path("api/market/public/v1/listings/<uuid:listing_id>/withdraw/", market_api.public_listing_withdraw),
    path("api/market/public/v1/reports/", market_api.public_report_submit),
    path("api/market/public/v1/sites/register/", market_api.public_site_register),
    path("api/market/public/v1/sites/<uuid:site_id>/", market_api.public_site_status),
    path("api/character-categories/", character_api.character_categories),
    path("api/character-categories/<uuid:category_id>/", character_api.character_category_detail),
    path("api/character-categories/<uuid:category_id>/export/", character_api.export_character_category),
    path("api/characters/<uuid:character_id>/export/", character_api.export_character),
    path("api/characters/<uuid:character_id>/", character_api.character_detail),
    path("api/characters/<uuid:character_id>/avatar/", character_api.character_avatar),
    path("api/avatars/<uuid:character_id>/", character_api.avatar_file),
    path("api/conversations/", conversation_api.conversations),
    path("api/conversations/<uuid:conversation_id>/", conversation_api.conversation_detail),
    path("api/conversations/<uuid:conversation_id>/messages/", conversation_api.send_message),
    path("api/conversations/<uuid:conversation_id>/suggest/", generation_api.suggest),
    path("api/conversations/<uuid:conversation_id>/generate/", generation_api.generate),
    path("api/generation-jobs/<uuid:job_id>/", generation_api.job_detail),
    path("api/generation-jobs/<uuid:job_id>/cancel/", generation_api.cancel),
    path("api/generation-jobs/<uuid:job_id>/retry/", generation_api.retry),
    path("api/settings/", settings_api.my_settings),
    path("api/settings/test-connection/", settings_api.connection_test),
    path("api/settings/models/", settings_api.available_models),
    path("api/settings/usage/", settings_api.provider_usage),
    path("api/model-providers/", settings_api.model_providers),
    path("api/account/profile/", profile_api.profile),
    path("api/account/warnings/read/", profile_api.acknowledge_warnings),
    path("api/worldbooks/", worldbook_api.worldbooks),
    path("api/worldbooks/preview/", worldbook_api.preview),
    path("api/worldbooks/import/preview/", worldbook_api.import_preview),
    path("api/worldbooks/import/commit/", worldbook_api.import_commit),
    path("api/worldbooks/<uuid:worldbook_id>/", worldbook_api.worldbook_detail),
    path("api/worldbooks/<uuid:worldbook_id>/export/", worldbook_api.export_worldbook),
    path("api/worldbooks/<uuid:worldbook_id>/categories/", worldbook_api.categories),
    path("api/worldbooks/<uuid:worldbook_id>/categories/<uuid:category_id>/", worldbook_api.category_detail),
    path("api/worldbooks/<uuid:worldbook_id>/entries/", worldbook_api.entries),
    path("api/worldbooks/<uuid:worldbook_id>/entries/bulk-categories/", worldbook_api.bulk_entry_categories),
    path("api/worldbooks/<uuid:worldbook_id>/entries/<uuid:entry_id>/", worldbook_api.entry_detail),
    path("api/conversations/<uuid:conversation_id>/worldbooks/", worldbook_api.conversation_worldbooks),
    path("api/admin/defaults/", settings_api.admin_defaults),
    path("api/admin/market/connection/", market_admin_api.market_connection),
    path("api/admin/market/sites/", market_admin_api.market_sites),
    path("api/admin/market/reports/", market_admin_api.market_reports),
    path("api/admin/market/listings/", market_admin_api.market_admin_listings),
    path("api/admin/market/listings/<uuid:listing_id>/", market_admin_api.moderate_listing),
]

urlpatterns += [
    path("api/admin/users/", admin_api.users),
    path("api/admin/audit/", admin_api.audit),
    path("api/admin/users/<int:user_id>/", admin_api.user_detail),
    path("api/admin/users/<int:user_id>/conversations/<uuid:conversation_id>/", admin_api.user_conversation),
    path("api/admin/users/<int:user_id>/worldbooks/", admin_api.user_worldbooks),
    path("api/admin/users/<int:user_id>/reset-password/", admin_api.reset_password),
    path("api/account/change-password/", admin_api.change_password),
]
