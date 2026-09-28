from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver
from django.utils import timezone

from .auth_api import PRIVACY_POLICY_VERSION, USAGE_RULES_VERSION
from .models import ChatSyncOutbox, Message, UserProfile
from .site_federation_client import active_federation_identities


def user_has_current_consent(user_id):
    profile = UserProfile.objects.filter(user_id=user_id).first()
    return not (
        profile is None
        or profile.policy_consent_at is None
        or profile.privacy_policy_version != PRIVACY_POLICY_VERSION
        or profile.usage_rules_version != USAGE_RULES_VERSION
    )


def _has_current_consent(message):
    return user_has_current_consent(message.conversation.owner_id)


def _payload(message):
    return {
        "source_user_id": str(message.conversation.owner_id),
        "source_conversation_id": str(message.conversation_id),
        "source_message_id": str(message.pk),
        "kind": message.kind,
        "source": message.source,
        "content": message.content,
        "speaker_name": message.speaker.name if message.speaker_id else None,
        "state_snapshot": message.state_snapshot,
        "source_created_at": message.created_at.isoformat(),
    }


def _queue(message, event_type, payload):
    if not _has_current_consent(message):
        return
    for identity in active_federation_identities():
        ChatSyncOutbox.objects.create(
            site_id=identity.site_id,
            source_message_id=str(message.pk),
            event_type=event_type,
            payload=payload,
            attempt_count=0,
            next_attempt_at=timezone.now(),
            last_error="",
        )


def seed_pending_chat_outbox(identity):
    pending = ChatSyncOutbox.objects.exclude(site_id=identity.site_id).order_by("id")
    batch = []
    for row in pending.iterator(chunk_size=500):
        batch.append(ChatSyncOutbox(
            site_id=identity.site_id,
            source_message_id=row.source_message_id,
            event_type=row.event_type,
            payload=row.payload,
            next_attempt_at=timezone.now(),
        ))
        if len(batch) == 500:
            ChatSyncOutbox.objects.bulk_create(batch, ignore_conflicts=True)
            batch = []
    if batch:
        ChatSyncOutbox.objects.bulk_create(batch, ignore_conflicts=True)


@receiver(post_save, sender=Message, dispatch_uid="core.chat_sync_message_saved")
def queue_message_save(sender, instance, raw=False, **kwargs):
    if raw:
        return
    _queue(instance, ChatSyncOutbox.UPSERT, _payload(instance))


@receiver(pre_delete, sender=Message, dispatch_uid="core.chat_sync_message_deleted")
def queue_message_delete(sender, instance, **kwargs):
    _queue(instance, ChatSyncOutbox.DELETE, {
        "source_user_id": str(instance.conversation.owner_id),
        "source_conversation_id": str(instance.conversation_id),
        "source_message_id": str(instance.pk),
        "source_created_at": instance.created_at.isoformat(),
    })
