from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models.signals import post_save, pre_delete, pre_save
from django.dispatch import receiver

from .auth_api import PRIVACY_POLICY_VERSION, USAGE_RULES_VERSION, role_for_user
from .models import UserDirectoryOutbox
from .site_federation_client import active_federation_identities


def _payload(user, status=None):
    return {
        "source_user_id": str(user.pk),
        "username": user.get_username(),
        "status": status or ("active" if user.is_active else "disabled"),
        "is_site_admin": role_for_user(user) == "site_admin",
        "joined_at": user.date_joined.isoformat() if user.date_joined else None,
    }


def queue_user_directory_event(user, *, status=None, identity=None):
    from .models import UserProfile

    if not UserProfile.objects.filter(
        user_id=user.pk,
        policy_consent_at__isnull=False,
        privacy_policy_version=PRIVACY_POLICY_VERSION,
        usage_rules_version=USAGE_RULES_VERSION,
    ).exists():
        return 0
    identities = [identity] if identity else active_federation_identities()
    payload = _payload(user, status)
    created = 0
    with transaction.atomic():
        for target in identities:
            UserDirectoryOutbox.objects.create(
                site_id=target.site_id,
                source_user_id=str(user.pk),
                payload=payload,
            )
            created += 1
    return created


def seed_user_directory_snapshot(identity):
    User = get_user_model()
    created = 0
    from .models import UserProfile

    consented = UserProfile.objects.filter(
        policy_consent_at__isnull=False,
        privacy_policy_version=PRIVACY_POLICY_VERSION,
        usage_rules_version=USAGE_RULES_VERSION,
    ).values_list("user_id", flat=True)
    for user in User._default_manager.filter(pk__in=consented).order_by("pk").iterator(chunk_size=500):
        created += queue_user_directory_event(user, identity=identity)
    return created


@receiver(pre_save, sender=get_user_model(), dispatch_uid="core.user_directory_before_save")
def capture_user_directory_state(sender, instance, raw=False, **kwargs):
    if raw or instance._state.adding:
        instance._directory_sync_before = None
        return
    instance._directory_sync_before = sender._default_manager.filter(pk=instance.pk).values_list(
        "username", "is_active", "is_staff", "date_joined",
    ).first()


@receiver(post_save, sender=get_user_model(), dispatch_uid="core.user_directory_saved")
def queue_user_directory_save(sender, instance, created=False, raw=False, **kwargs):
    if raw:
        return
    previous = getattr(instance, "_directory_sync_before", None)
    current = (instance.username, instance.is_active, instance.is_staff, instance.date_joined)
    if created or previous != current:
        queue_user_directory_event(instance)
    if hasattr(instance, "_directory_sync_before"):
        del instance._directory_sync_before


@receiver(pre_delete, sender=get_user_model(), dispatch_uid="core.user_directory_deleted")
def queue_user_directory_delete(sender, instance, **kwargs):
    queue_user_directory_event(instance, status="disabled")
