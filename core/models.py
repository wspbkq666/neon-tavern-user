import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.db.models.signals import m2m_changed
from django.dispatch import receiver


class Character(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="characters")
    name = models.CharField(max_length=60)
    summary = models.CharField(max_length=200, blank=True)
    personality = models.TextField(blank=True)
    speech_habits = models.TextField(blank=True)
    memories = models.TextField(blank=True)
    scenario = models.TextField(blank=True)
    first_mes = models.TextField(blank=True)
    alternate_greetings = models.JSONField(default=list, blank=True)
    character_worldbook = models.JSONField(default=dict, blank=True)
    context_policy = models.JSONField(default=dict, blank=True)
    personal_worldbook = models.ForeignKey('Worldbook',null=True,blank=True,on_delete=models.SET_NULL,related_name='bound_character_cards')
    lore_hash = models.CharField(max_length=64, blank=True)
    avatar = models.ImageField(upload_to="avatars/", blank=True)
    relationship_notes = models.JSONField(default=dict, blank=True)
    state_fields = models.JSONField(default=dict, blank=True)
    affinity = models.PositiveSmallIntegerField(default=0)
    clothing_type = models.CharField(max_length=200, blank=True)
    clothing_state = models.CharField(max_length=200, blank=True)
    is_player_controlled = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self,*args,**kwargs):
        from .context_selection import validate_context_policy
        update_fields=kwargs.get('update_fields')
        source_fields={'personality','memories'}
        if not self._state.adding and (update_fields is None or source_fields.intersection(update_fields)):
            original=Character.objects.filter(pk=self.pk).values('personality','memories').first()
            if original and any(original[key]!=getattr(self,key) for key in source_fields if update_fields is None or key in update_fields):
                self.context_policy={}
                if update_fields is not None:
                    kwargs['update_fields']=set(update_fields)|{'context_policy'}
        validate_context_policy(self.context_policy)
        from .character_lore import validate_extended_fields, sync_character_lore
        validate_extended_fields(self)
        with transaction.atomic():
            result=super().save(*args,**kwargs)
            update_fields=kwargs.get('update_fields')
            if update_fields is None or 'character_worldbook' in update_fields:
                sync_character_lore(self)
            if update_fields is None or {'personality','memories'}.intersection(update_fields):
                from .context_selection import split_source
                retained=[]
                for field in ('personality','memories'):
                    if update_fields is not None and field not in update_fields:
                        continue
                    for row in split_source(getattr(self,field)):
                        segment,_=CharacterSettingSegment.objects.update_or_create(character=self,source_field=field,start=row['start'],end=row['end'],defaults={'text':row['text']})
                        retained.append(segment.pk)
                    self.setting_segments.filter(source_field=field).exclude(pk__in=retained).delete()
            return result

    class Meta:
        ordering = ["name", "created_at"]


class GenerationContextTrace(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    job=models.ForeignKey('GenerationJob',on_delete=models.CASCADE,related_name='context_traces')
    actor=models.ForeignKey(Character,null=True,on_delete=models.SET_NULL)
    selected=models.JSONField(default=list)
    excluded=models.JSONField(default=list)
    estimated_usage=models.JSONField(default=dict)
    configuration=models.JSONField(default=dict)
    character_snapshot=models.JSONField(default=dict)
    content_hash=models.CharField(max_length=64)
    created_at=models.DateTimeField(auto_now_add=True)


class PersonalBackupJob(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    owner=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name='personal_backup_jobs')
    kind=models.CharField(max_length=20)
    status=models.CharField(max_length=20,default='queued')
    package=models.BinaryField(null=True,blank=True)
    result=models.JSONField(default=dict)
    error=models.TextField(blank=True)
    lease_token=models.CharField(max_length=64,blank=True)
    lease_until=models.DateTimeField(null=True,blank=True)
    created_at=models.DateTimeField(auto_now_add=True)


class PersonalRestoreReceipt(models.Model):
    owner=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE)
    idempotency_key=models.CharField(max_length=200)
    package_hash=models.CharField(max_length=64)
    result=models.JSONField(default=dict)

    class Meta:
        constraints=[models.UniqueConstraint(fields=['owner','idempotency_key'],name='unique_personal_restore_key')]


class ImportBatch(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    owner=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name='import_batches')
    task=models.ForeignKey('ImportTask',null=True,blank=True,on_delete=models.SET_NULL,related_name='batches')
    idempotency_key=models.CharField(max_length=200)
    payload_hash=models.CharField(max_length=64)
    created_objects=models.JSONField(default=list)
    result=models.JSONField(default=dict)
    undo_result=models.JSONField(default=dict)
    revision=models.PositiveIntegerField(default=0)
    created_at=models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints=[models.UniqueConstraint(fields=['owner','idempotency_key'],name='unique_import_batch_key')]


class ImportTask(models.Model):
    parent=models.ForeignKey('self',null=True,blank=True,on_delete=models.SET_NULL,related_name='rescans')
    source_start=models.PositiveIntegerField(default=0)
    source_end=models.PositiveIntegerField(default=0)
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    owner=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name='import_tasks')
    filename=models.CharField(max_length=250)
    source=models.TextField()
    source_hash=models.CharField(max_length=64)
    options=models.JSONField(default=dict)
    status=models.CharField(max_length=20,default='queued')
    revision=models.PositiveIntegerField(default=0)
    result=models.JSONField(default=dict)
    coverage_reviews=models.JSONField(default=dict)
    error=models.TextField(blank=True)
    created_at=models.DateTimeField(auto_now_add=True)
    updated_at=models.DateTimeField(auto_now=True)


class ImportSegment(models.Model):
    task=models.ForeignKey(ImportTask,on_delete=models.CASCADE,related_name='segments')
    index=models.PositiveIntegerField()
    start=models.PositiveIntegerField()
    end=models.PositiveIntegerField()
    status=models.CharField(max_length=20,default='queued')
    lease_token=models.CharField(max_length=64,blank=True)
    lease_until=models.DateTimeField(null=True,blank=True)
    worker_id=models.CharField(max_length=120,blank=True)
    attempts=models.PositiveIntegerField(default=0)
    raw_output=models.TextField(blank=True)
    result=models.JSONField(default=dict)
    error=models.TextField(blank=True)

    class Meta:
        constraints=[models.UniqueConstraint(fields=['task','index'],name='unique_import_task_segment')]
        ordering=['index']


class ImportTaskEvent(models.Model):
    task=models.ForeignKey(ImportTask,on_delete=models.CASCADE,related_name='events')
    payload=models.JSONField(default=dict)
    created_at=models.DateTimeField(auto_now_add=True)


class CharacterSettingSegment(models.Model):
    character=models.ForeignKey(Character,on_delete=models.CASCADE,related_name='setting_segments')
    source_field=models.CharField(max_length=30)
    start=models.PositiveIntegerField()
    end=models.PositiveIntegerField()
    text=models.TextField()

    class Meta:
        constraints=[models.UniqueConstraint(fields=['character','source_field','start','end'],name='unique_character_source_segment')]
        ordering=['source_field','start']


class CharacterCategory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="character_categories")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children")
    name = models.CharField(max_length=120)
    position = models.PositiveIntegerField(default=0)
    characters = models.ManyToManyField(Character, blank=True, related_name="categories")

    def clean(self):
        super().clean()
        node = self.parent
        seen = set()
        while node:
            if node.pk == self.pk or node.pk in seen or node.owner_id != self.owner_id:
                raise ValidationError({"parent": "分类层级无效"})
            seen.add(node.pk)
            node = node.parent

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self.children.update(parent=self.parent)
        return super().delete(*args, **kwargs)

    class Meta:
        ordering = ["position", "name", "id"]
        constraints = [models.UniqueConstraint(fields=["owner", "parent", "name"], name="unique_character_category_sibling")]


class Conversation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="conversations")
    title = models.CharField(max_length=120)
    environment = models.JSONField(default=dict, blank=True)
    player_character = models.ForeignKey(Character, on_delete=models.PROTECT, related_name="player_conversations")
    participants = models.ManyToManyField(Character, through="ConversationParticipant", related_name="conversations")
    language = models.CharField(max_length=32, default="中文")
    auto_generate = models.BooleanField(default=False)
    auto_actor_ids = models.JSONField(default=list, blank=True)
    auto_mode = models.CharField(max_length=8, default="serial")
    generation_configuration = models.JSONField(default=dict,blank=True)
    long_memory = models.TextField(blank=True)
    memory_message_count = models.PositiveIntegerField(default=0)
    profile_message_count = models.PositiveIntegerField(default=0)
    story_revision = models.PositiveIntegerField(default=0)
    memory_revision = models.PositiveIntegerField(default=0)
    locked_facts = models.JSONField(default=list, blank=True)
    memory_summary_status = models.CharField(max_length=12, default='idle')
    memory_summary_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]


class ConversationParticipant(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE)
    character = models.ForeignKey(Character, on_delete=models.PROTECT)
    position = models.PositiveSmallIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["conversation", "character"], name="unique_conversation_character")]
        ordering = ["position"]


class ConversationActorState(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='actor_states')
    character = models.ForeignKey(Character, on_delete=models.PROTECT, related_name='story_states')
    template_snapshot = models.JSONField(default=dict)
    state_fields = models.JSONField(default=dict)
    relationship_notes = models.JSONField(default=dict)
    affinity = models.PositiveSmallIntegerField(default=0)
    clothing_type = models.CharField(max_length=200, blank=True)
    clothing_state = models.CharField(max_length=200, blank=True)
    revision = models.PositiveIntegerField(default=0)
    initialization_source = models.CharField(max_length=30, default='角色卡初始设定')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['conversation','character'], name='unique_story_actor_state')]


class Message(models.Model):
    THOUGHT = "thought"
    DIALOGUE = "dialogue"
    ACTION = "action"
    OOC = "ooc"
    NARRATION = "narration"
    STATE = "state"
    KINDS = [(THOUGHT, "内心想法"), (DIALOGUE, "台词"), (ACTION, "动作"), (OOC, "导演"), (NARRATION, "旁白"), (STATE, "状态")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    speaker = models.ForeignKey(Character, on_delete=models.PROTECT, null=True, blank=True)
    kind = models.CharField(max_length=16, choices=KINDS)
    content = models.TextField()
    source = models.CharField(max_length=8, default="user")
    consumed_by = models.JSONField(default=list, blank=True)
    state_snapshot = models.JSONField(default=dict, blank=True)
    story_snapshot = models.JSONField(default=dict, blank=True)
    context_trace = models.ForeignKey(GenerationContextTrace,null=True,blank=True,on_delete=models.SET_NULL,related_name='messages')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]


class StoryCheckpoint(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='checkpoints')
    name = models.CharField(max_length=120)
    snapshot = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at','-id']


class GenerationJob(models.Model):
    lease_until=models.DateTimeField(null=True,blank=True)
    lease_token=models.CharField(max_length=64,blank=True)
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="generation_jobs")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    actor_ids = models.JSONField(default=list)
    director_hints = models.JSONField(default=dict, blank=True)
    mode = models.CharField(max_length=8, default="serial")
    auto = models.BooleanField(default=False)
    status = models.CharField(max_length=12, default="queued")
    progress = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)
    story_revision = models.PositiveIntegerField(default=0)
    task_kind = models.CharField(max_length=12, default='dialogue')
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(fields=["conversation"], condition=Q(status="queued"), name="one_queued_generation_per_conversation")
        ]


class StoryMemoryRevision(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='memory_history')
    revision = models.PositiveIntegerField()
    text = models.TextField(blank=True)
    locked_facts = models.JSONField(default=list)
    source = models.CharField(max_length=20)
    message_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-revision']
        constraints = [models.UniqueConstraint(fields=['conversation','revision'],name='unique_story_memory_revision')]


class SiteSettings(models.Model):
    values = models.JSONField(default=dict, blank=True)
    encrypted_api_key = models.TextField(blank=True)
    public_market_url = models.CharField(max_length=500, blank=True)
    market_site_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    encrypted_market_private_key = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class UserSettings(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tavern_settings")
    overrides = models.JSONField(default=dict, blank=True)
    encrypted_api_key = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class MarketSiteCredential(models.Model):
    PENDING = "pending"
    APPROVED = "approved"
    REVOKED = "revoked"
    STATUS_CHOICES = [(PENDING, "待审批"), (APPROVED, "已批准"), (REVOKED, "已撤销")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    site_id = models.UUIDField(unique=True)
    name = models.CharField(max_length=120)
    public_key = models.CharField(max_length=200)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["status", "name", "created_at"]


class MarketListing(models.Model):
    LOCAL = "local"
    PUBLIC = "public"
    SCOPE_CHOICES = [(LOCAL, "本站"), (PUBLIC, "公共")]
    CHARACTER = "character"
    WORLDBOOK = "worldbook"
    BUNDLE = "bundle"
    KIND_CHOICES = [(CHARACTER, "角色卡"), (WORLDBOOK, "世界书"), (BUNDLE, "整合包")]
    PUBLISHED = "published"
    WITHDRAWN = "withdrawn"
    HIDDEN = "hidden"
    STATUS_CHOICES = [(PUBLISHED, "已发布"), (WITHDRAWN, "已撤回"), (HIDDEN, "已隐藏")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="market_listings")
    origin_site = models.ForeignKey(MarketSiteCredential, null=True, blank=True, on_delete=models.SET_NULL, related_name="listings")
    source_item_id = models.CharField(max_length=80, blank=True, default="")
    remote_listing_id = models.CharField(max_length=80, blank=True, default="")
    scope = models.CharField(max_length=8, choices=SCOPE_CHOICES, default=LOCAL)
    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    title = models.CharField(max_length=120)
    description = models.CharField(max_length=500, blank=True)
    tags = models.JSONField(default=list, blank=True)
    author_alias = models.CharField(max_length=80)
    payload = models.JSONField()
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=PUBLISHED)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "title"]
        indexes = [models.Index(fields=["scope", "status", "kind", "created_at"], name="market_scope_status_idx")]
        constraints = [
            models.UniqueConstraint(
                fields=["origin_site", "source_item_id"],
                condition=Q(origin_site__isnull=False) & ~Q(source_item_id=""),
                name="unique_market_remote_source_item",
            ),
        ]


class MarketNonce(models.Model):
    site = models.ForeignKey(MarketSiteCredential, on_delete=models.CASCADE, related_name="nonces")
    value = models.CharField(max_length=80)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["site", "value"], name="unique_market_site_nonce")]
        indexes = [models.Index(fields=["created_at"], name="market_nonce_created_idx")]


class MarketReport(models.Model):
    OPEN = "open"
    REVIEWED = "reviewed"
    DISMISSED = "dismissed"
    STATUS_CHOICES = [(OPEN, "待处理"), (REVIEWED, "已处理"), (DISMISSED, "已驳回")]

    listing = models.ForeignKey(MarketListing, on_delete=models.CASCADE, related_name="reports")
    reporter = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="market_reports")
    reporter_site = models.CharField(max_length=80, blank=True)
    reason = models.CharField(max_length=500)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=OPEN)
    created_at = models.DateTimeField(auto_now_add=True)
    handled_at = models.DateTimeField(null=True, blank=True)


class UserProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tavern_profile")
    site_key = models.CharField(max_length=255, blank=True, default="")
    inferred_traits = models.TextField(blank=True)
    policy_consent_at = models.DateTimeField(null=True, blank=True)
    privacy_policy_version = models.CharField(max_length=32, blank=True, default="")
    usage_rules_version = models.CharField(max_length=32, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)


class FederationIdentity(models.Model):
    TARGET_CHOICES = [("main_154", "总站 154"), ("main_123", "总站 123")]

    target_key = models.CharField(max_length=32, unique=True, choices=TARGET_CHOICES)
    site_id = models.UUIDField(unique=True)
    central_url = models.URLField(max_length=500)
    central_public_key = models.CharField(max_length=43)
    private_key_env_name = models.CharField(max_length=100)
    active = models.BooleanField(default=True)
    paired_at = models.DateTimeField(auto_now_add=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_error_code = models.CharField(max_length=64, blank=True)


class ChatSyncOutbox(models.Model):
    UPSERT = "upsert"
    DELETE = "delete"

    site_id = models.UUIDField()
    source_message_id = models.CharField(max_length=100)
    event_type = models.CharField(max_length=8, choices=[(UPSERT, "新增或更新"), (DELETE, "删除")], default=UPSERT)
    payload = models.JSONField(default=dict, blank=True)
    attempt_count = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(auto_now_add=True)
    last_error = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["site_id", "next_attempt_at", "id"], name="chat_outbox_pending_idx")]


class UserDirectoryOutbox(models.Model):
    site_id = models.UUIDField()
    source_user_id = models.CharField(max_length=100)
    payload = models.JSONField(default=dict)
    attempt_count = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(auto_now_add=True)
    last_error = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["site_id", "next_attempt_at", "id"], name="user_dir_outbox_pending_idx")]


class SiteCommandReceipt(models.Model):
    command_id = models.UUIDField(primary_key=True)
    payload_digest = models.CharField(max_length=64)
    status = models.CharField(max_length=12)
    result = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


class UserDisposition(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="disposition")
    status = models.CharField(max_length=12, default="active")
    was_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)


class UserWarning(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="admin_warnings")
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class AuditLog(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="tavern_audit_actions")
    target_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="tavern_audit_targets")
    action = models.CharField(max_length=40)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class Worldbook(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="worldbooks")
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "created_at"]
        constraints = [models.UniqueConstraint(fields=["owner", "name"], name="unique_owner_worldbook_name")]
        indexes = [models.Index(fields=["owner", "enabled"], name="worldbook_owner_enabled_idx")]


class WorldbookCategory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    worldbook = models.ForeignKey(Worldbook, on_delete=models.CASCADE, related_name="categories")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children")
    name = models.CharField(max_length=120)
    position = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        super().clean()
        node = self.parent
        seen = set()
        while node:
            if node.pk == self.pk or node.pk in seen or node.worldbook_id != self.worldbook_id:
                raise ValidationError({"parent": "分类层级无效"})
            seen.add(node.pk)
            node = node.parent

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            if self.parent_id:
                for entry in self.entries.all():
                    entry.categories.add(self.parent_id)
            self.children.update(parent_id=self.parent_id)
            return super().delete(*args, **kwargs)

    class Meta:
        ordering = ["position", "created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["worldbook", "parent", "name"],
                condition=Q(parent__isnull=False),
                name="unique_worldbook_category_sibling_name",
            ),
            models.UniqueConstraint(
                fields=["worldbook", "name"],
                condition=Q(parent__isnull=True),
                name="unique_worldbook_root_category_name",
            ),
        ]
        indexes = [models.Index(fields=["worldbook", "parent", "position"], name="worldbook_category_tree_idx")]


class WorldbookEntry(models.Model):
    TRIGGER_KEYWORD = "keyword"
    TRIGGER_ALWAYS = "always"
    TRIGGER_MANUAL = "manual"
    TRIGGER_CHOICES = [
        (TRIGGER_KEYWORD, "关键词"),
        (TRIGGER_ALWAYS, "始终"),
        (TRIGGER_MANUAL, "手动"),
    ]

    POSITION_BEFORE_CHARACTER = "before_character"
    POSITION_AFTER_CHARACTER = "after_character"
    POSITION_BEFORE_RECENT = "before_recent_messages"
    POSITION_CHOICES = [
        (POSITION_BEFORE_CHARACTER, "角色卡前"),
        (POSITION_AFTER_CHARACTER, "角色卡后"),
        (POSITION_BEFORE_RECENT, "最近对话前"),
    ]

    SCOPE_GLOBAL = "global"
    SCOPE_CHARACTER = "character"
    SCOPE_CONVERSATION = "conversation"
    SCOPE_CHOICES = [
        (SCOPE_GLOBAL, "全局"),
        (SCOPE_CHARACTER, "指定角色"),
        (SCOPE_CONVERSATION, "指定对话"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    worldbook = models.ForeignKey(Worldbook, on_delete=models.CASCADE, related_name="entries")
    categories = models.ManyToManyField(WorldbookCategory, blank=True, related_name="entries")
    name = models.CharField(max_length=160)
    content = models.TextField()
    enabled = models.BooleanField(default=True)
    trigger_mode = models.CharField(max_length=12, choices=TRIGGER_CHOICES, default=TRIGGER_KEYWORD)
    keywords = models.JSONField(default=list, blank=True)
    insertion_position = models.CharField(max_length=24, choices=POSITION_CHOICES, default=POSITION_BEFORE_CHARACTER)
    priority = models.IntegerField(default=0)
    scope_type = models.CharField(max_length=16, choices=SCOPE_CHOICES, default=SCOPE_GLOBAL)
    scoped_characters = models.ManyToManyField(Character, blank=True, related_name="scoped_worldbook_entries")
    scoped_conversations = models.ManyToManyField(Conversation, blank=True, related_name="scoped_worldbook_entries")
    import_metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["insertion_position", "-priority", "created_at", "id"]
        indexes = [models.Index(fields=["worldbook", "enabled", "trigger_mode"], name="worldbook_entry_active_idx")]
        constraints = [
            models.UniqueConstraint(fields=["worldbook", "name"], name="unique_worldbook_entry_name"),
            models.CheckConstraint(condition=Q(trigger_mode__in=["keyword", "always", "manual"]), name="valid_worldbook_trigger_mode"),
            models.CheckConstraint(
                condition=Q(insertion_position__in=["before_character", "after_character", "before_recent_messages"]),
                name="valid_worldbook_insertion_position",
            ),
            models.CheckConstraint(condition=Q(scope_type__in=["global", "character", "conversation"]), name="valid_worldbook_scope_type"),
        ]


class ConversationWorldbookConfig(models.Model):
    conversation = models.OneToOneField(Conversation, on_delete=models.CASCADE, related_name="worldbook_config")
    enabled_worldbooks = models.ManyToManyField(Worldbook, blank=True, related_name="enabled_in_conversations")
    enabled_categories = models.ManyToManyField(WorldbookCategory, blank=True, related_name="enabled_in_conversations")
    excluded_categories = models.ManyToManyField(WorldbookCategory, blank=True, related_name="excluded_in_conversations")
    excluded_entries = models.ManyToManyField(WorldbookEntry, blank=True, related_name="excluded_in_conversations")
    manual_entries = models.ManyToManyField(WorldbookEntry, blank=True, related_name="manually_enabled_in_conversations")
    updated_at = models.DateTimeField(auto_now=True)


@receiver(m2m_changed, sender=WorldbookEntry.categories.through)
def validate_entry_categories(sender, instance, action, pk_set, **kwargs):
    if action == "pre_add" and WorldbookCategory.objects.filter(pk__in=pk_set).exclude(worldbook=instance.worldbook).exists():
        raise ValidationError("条目和分类必须属于同一个世界书")


@receiver(m2m_changed, sender=CharacterCategory.characters.through)
def validate_character_category_members(sender, instance, action, pk_set, **kwargs):
    if action == "pre_add" and Character.objects.filter(pk__in=pk_set).exclude(owner=instance.owner).exists():
        raise ValidationError("分类只能包含当前用户的角色")


@receiver(m2m_changed, sender=WorldbookEntry.scoped_characters.through)
def validate_entry_scoped_characters(sender, instance, action, pk_set, **kwargs):
    if action == "pre_add" and Character.objects.filter(pk__in=pk_set).exclude(owner=instance.worldbook.owner).exists():
        raise ValidationError("指定角色必须属于世界书所有者")


@receiver(m2m_changed, sender=WorldbookEntry.scoped_conversations.through)
def validate_entry_scoped_conversations(sender, instance, action, pk_set, **kwargs):
    if action == "pre_add" and Conversation.objects.filter(pk__in=pk_set).exclude(owner=instance.worldbook.owner).exists():
        raise ValidationError("指定对话必须属于世界书所有者")


def _validate_config_relation(model, instance, action, pk_set, owner_path):
    if action != "pre_add":
        return
    queryset = model.objects.filter(pk__in=pk_set)
    if queryset.exclude(**{owner_path: instance.conversation.owner}).exists():
        raise ValidationError("世界书配置只能引用当前用户的数据")


@receiver(m2m_changed, sender=ConversationWorldbookConfig.enabled_worldbooks.through)
def validate_config_worldbooks(sender, instance, action, pk_set, **kwargs):
    _validate_config_relation(Worldbook, instance, action, pk_set, "owner")


@receiver(m2m_changed, sender=ConversationWorldbookConfig.enabled_categories.through)
@receiver(m2m_changed, sender=ConversationWorldbookConfig.excluded_categories.through)
def validate_config_categories(sender, instance, action, pk_set, **kwargs):
    _validate_config_relation(WorldbookCategory, instance, action, pk_set, "worldbook__owner")


@receiver(m2m_changed, sender=ConversationWorldbookConfig.excluded_entries.through)
@receiver(m2m_changed, sender=ConversationWorldbookConfig.manual_entries.through)
def validate_config_entries(sender, instance, action, pk_set, **kwargs):
    _validate_config_relation(WorldbookEntry, instance, action, pk_set, "worldbook__owner")
