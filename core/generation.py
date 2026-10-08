import json
import re
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from queue import Empty, Queue

import httpx
from django.db import transaction
from django.utils import timezone

from .ai_text_filter import sanitize_ai_value
from .models import Character, Conversation, GenerationJob, Message, UserProfile, WorldbookEntry
from .settings_api import active_api_key, effective_values
from .worldbook_resolver import resolve_worldbook_entries
from .story_state import actor_context, ensure_story_state, apply_actor_result, StoryConflict
from .story_memory import memory_context, apply_memory_summary
from .character_lore import resolve_personal_lore


class ModelResponseTruncated(ValueError):
    """模型在 JSON 完整返回前停止。"""


class ModelResponseInvalidJSON(ValueError):
    """模型返回内容无法解析为 JSON。"""


def _stream_json_field(raw, field):
    match = re.search(rf'"{re.escape(field)}"\s*:\s*"', raw)
    if not match:
        return ""
    start = match.end()
    escaped = False
    for index in range(start, len(raw)):
        char = raw[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            try:
                return json.loads(raw[start - 1:index + 1])
            except (TypeError, ValueError):
                return ""
    partial = raw[start:]
    if partial.endswith("\\"):
        partial = partial[:-1]
    try:
        return json.loads(f'"{partial}"')
    except (TypeError, ValueError):
        return partial.replace('\\"', '"')


def _stream_public_text(raw):
    return "\n".join(value for value in (_stream_json_field(raw, field) for field in ("narration", "action", "dialogue")) if value)


def _phase_messages(messages, phase, *, thought="", public_text=""):
    system = json.loads(messages[0]["content"])
    user = json.loads(messages[1]["content"])
    system["generation_phase"] = phase
    if phase == "thought":
        system["task"] = "当前阶段只生成指定角色的 thought。只返回 JSON，thought 写角色此刻的内心想法，narration、action、dialogue、state_updates、fixed_status 置空。"
        user["output"] = "只生成 thought，不生成公开动作、旁白、台词或状态。"
    elif phase == "public":
        system["task"] = "当前阶段只生成指定角色的公开回应。只返回 JSON，生成 narration、action、dialogue；thought、state_updates、fixed_status 置空。不要输出内心想法或状态。"
        user["internal_thought"] = thought
        user["output"] = "只生成 narration、action、dialogue，公开内容按当前角色自然顺序组织。"
    elif phase == "state":
        system["task"] = "当前阶段只整理指定角色的状态变化。只返回 JSON，生成 state_updates 和 fixed_status；thought、narration、action、dialogue 置空。state_updates 必须是扁平对象，禁止用角色名再包一层。"
        user["internal_thought"] = thought
        user["actor_public_output"] = public_text
        user["output"] = "只生成 state_updates 和 fixed_status，不重复生成任何可见内容。"
    else:
        raise ValueError("生成阶段无效")
    return [
        {"role": "system", "content": json.dumps(system, ensure_ascii=False)},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]


def call_json_model(messages, options, key, *, max_tokens=None, on_chunk=None, on_raw_chunk=None, timings=None, force_stream=False):
    request_started_at = time.monotonic()
    payload = {
        "model": options["model"],
        "messages": messages,
        "temperature": options["temperature"],
        "top_p": options["top_p"],
        "max_tokens": max_tokens or options["max_tokens"],
        "response_format": {"type": "json_object"},
        "stream": force_stream or (on_chunk is not None or on_raw_chunk is not None) and options.get("stream_output", False),
    }
    try:
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        timeout = httpx.Timeout(120, connect=15)
        api_url = f"{options.get('api_base_url', 'https://api.deepseek.com').rstrip('/')}/chat/completions"
        if payload["stream"]:
            chunks = []
            length = 0
            last_update = 0.0
            finish_reason = None
            with httpx.stream("POST", api_url, headers=headers, json=payload, timeout=timeout) as response:
                if timings is not None:
                    timings["headers_ms"] = round((time.monotonic() - request_started_at) * 1000)
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.startswith("data: ") or line == "data: [DONE]":
                        continue
                    try:
                        choice = json.loads(line[6:])["choices"][0]
                        delta = choice["delta"].get("content", "")
                        if choice.get("finish_reason"):
                            finish_reason = choice["finish_reason"]
                    except (ValueError, KeyError, IndexError, TypeError):
                        continue
                    if isinstance(delta, str) and delta:
                        chunks.append(delta)
                        length += len(delta)
                        if length > 120000:
                            raise ValueError("模型回复过长")
                        if timings is not None and "first_token_ms" not in timings:
                            timings["first_token_ms"] = round((time.monotonic() - request_started_at) * 1000)
                        now = time.monotonic()
                        if now - last_update >= 0.35:
                            raw = "".join(chunks)
                            if on_raw_chunk:
                                on_raw_chunk(raw)
                            if on_chunk:
                                on_chunk(_stream_public_text(raw)[-6000:])
                            last_update = now
            content = "".join(chunks)
            if on_raw_chunk:
                on_raw_chunk(content)
            if on_chunk:
                on_chunk(_stream_public_text(content)[-6000:])
            if timings is not None:
                timings["response_complete_ms"] = round((time.monotonic() - request_started_at) * 1000)
            if finish_reason == "length":
                raise ModelResponseTruncated("模型回复达到输出上限，内容可能被截断")
        else:
            response = httpx.post(api_url, headers=headers, json=payload, timeout=timeout)
            if timings is not None:
                timings["headers_ms"] = round((time.monotonic() - request_started_at) * 1000)
            response.raise_for_status()
            choice = response.json()["choices"][0]
            content = choice["message"]["content"]
            if choice.get("finish_reason") == "length":
                raise ModelResponseTruncated("模型回复达到输出上限，内容可能被截断")
            if timings is not None:
                timings["response_complete_ms"] = round((time.monotonic() - request_started_at) * 1000)
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(f"模型请求失败（HTTP {exc.response.status_code}）") from exc
    except httpx.HTTPError as exc:
        raise RuntimeError("模型连接失败，请稍后重试") from exc
    try:
        result = json.loads(content)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ModelResponseInvalidJSON("模型未返回有效 JSON") from exc
    if not isinstance(result, dict):
        raise ValueError("模型返回格式无效")
    return result


def call_model(messages, options, key, on_chunk=None):
    started_at = time.monotonic()
    timings = {}
    last_thought = ""
    last_public = ""
    emitted = set()

    def report_raw(raw):
        nonlocal last_thought, last_public
        if not on_chunk:
            return
        thought_partial = _stream_json_field(raw, "thought")
        public_partial = _stream_public_text(raw)
        if thought_partial and thought_partial != last_thought:
            last_thought = thought_partial
            emitted.add("thought")
            timings.setdefault("thought_visible_ms", round((time.monotonic() - started_at) * 1000))
            on_chunk({"phase": "thought", "thought": thought_partial, "public": ""})
        if public_partial and public_partial != last_public:
            last_public = public_partial
            emitted.add("public")
            timings.setdefault("public_visible_ms", round((time.monotonic() - started_at) * 1000))
            on_chunk({"phase": "public", "thought": last_thought, "public": public_partial, "partial": public_partial})

    result = call_json_model(
        messages,
        options,
        key,
        on_raw_chunk=report_raw if on_chunk else None,
        timings=timings,
    )
    thought = result.get("thought", "")
    if not isinstance(thought, str) or len(thought) > 20000:
        raise ValueError("模型内心想法格式无效")

    public = {}
    for field in ("narration", "dialogue", "action"):
        value = result.get(field, "")
        if not isinstance(value, str) or len(value) > 20000:
            raise ValueError("模型公开内容格式无效")
        public[field] = value
    if not any(public.values()):
        raise ValueError("模型没有返回可显示的公开内容")

    public_text = "\n".join(value for value in (public["narration"], public["action"], public["dialogue"]) if value)
    timings.setdefault("first_token_ms", timings.get("thought_visible_ms", round((time.monotonic() - started_at) * 1000)))
    timings.setdefault("response_complete_ms", round((time.monotonic() - started_at) * 1000))
    timings.setdefault("thought_visible_ms", round((time.monotonic() - started_at) * 1000))
    timings.setdefault("public_visible_ms", round((time.monotonic() - started_at) * 1000))
    state_updates = result.get("state_updates", {})
    if not isinstance(state_updates, dict) or len(state_updates) > 40:
        raise ValueError("模型状态格式无效")
    for name, value in state_updates.items():
        if not isinstance(name, str) or not 1 <= len(name) <= 50:
            raise ValueError("模型状态字段无效")
        if not isinstance(value, (str, int, float, bool, dict)) or len(json.dumps(value, ensure_ascii=False)) > 2000:
            raise ValueError("模型状态值无效")
    fixed = result.get("fixed_status", {})
    if not isinstance(fixed, dict):
        raise ValueError("模型固定状态格式无效")
    allowed_fixed = {"affinity", "clothing_type", "clothing_state"}
    if any(name not in allowed_fixed for name in fixed):
        raise ValueError("模型固定状态字段无效")
    if "affinity" in fixed and (not isinstance(fixed["affinity"], (int, float)) or isinstance(fixed["affinity"], bool)):
        raise ValueError("模型好感度无效")
    for name in ("clothing_type", "clothing_state"):
        if name in fixed and (not isinstance(fixed[name], str) or len(fixed[name]) > 200):
            raise ValueError("模型衣服状态无效")
    timings["state_ready_ms"] = round((time.monotonic() - started_at) * 1000)
    if on_chunk:
        if "thought" not in emitted:
            on_chunk({"phase": "thought", "thought": thought, "public": ""})
        if "public" not in emitted:
            on_chunk({"phase": "public", "thought": thought, "public": public_text, "partial": public_text})
        on_chunk({"phase": "state", "thought": thought, "public": public_text, "partial": public_text, "state": state_updates, "fixed_status": fixed})
    return {
        **public,
        "thought": thought,
        "state_updates": state_updates,
        "fixed_status": fixed,
        "_timings": {
            "thought_ms": None,
            "public_stream_ms": None,
            "state_ms": None,
            **timings,
            "model_total_ms": round((time.monotonic() - started_at) * 1000),
            "model_calls": 1,
        },
    }


def build_messages(conversation, actor, hints, options, *, context_meta=None):
    actor = actor_context(conversation, actor)
    memory = memory_context(conversation)
    def clean(value):
        return sanitize_ai_value(value)[0]

    participants = list(conversation.participants.all())
    profile, _ = UserProfile.objects.get_or_create(user=conversation.owner)
    recent = list(conversation.messages.select_related("speaker").order_by("-created_at", "-id")[:100])
    public_recent = [item.content for item in reversed(recent) if item.kind not in (Message.STATE, Message.THOUGHT)]
    exclusions=[]
    resolved_entries = resolve_worldbook_entries(conversation, actor, "\n".join(public_recent),diagnostics=exclusions)
    resolved_entries = [entry for entry in resolved_entries if entry.worldbook_id!=actor.personal_worldbook_id]
    resolved_entries += resolve_personal_lore(conversation,actor,"\n".join(public_recent))
    sorted_entries = sorted(resolved_entries, key=lambda item: (-item.priority, item.created_at, str(item.id)))
    included_entries = [entry for entry in sorted_entries if entry.trigger_mode!='keyword']
    optional_worldbook_entries = [entry for entry in sorted_entries if entry.trigger_mode=='keyword']
    omitted_entry_ids = []
    sections = {
        WorldbookEntry.POSITION_BEFORE_CHARACTER: [],
        WorldbookEntry.POSITION_AFTER_CHARACTER: [],
        WorldbookEntry.POSITION_BEFORE_RECENT: [],
    }
    for entry in included_entries:
        sections[entry.insertion_position].append({"id": str(entry.id), "name": clean(entry.name), "content": clean(entry.content)})

    system = {
        "task": "扮演指定角色，保持角色一致。只生成该角色本轮的回应。必须输出 JSON 对象，键为 thought、narration、action、dialogue、state_updates、fixed_status。fixed_status 仅可含 affinity(0-100)、clothing_type、clothing_state。state_updates 必须是扁平对象，禁止以角色名作为外层键包裹状态。thought 是角色不说出口的内心想法；action 是公开动作；dialogue 只写说话内容且不要自行添加引号。不要使用 Markdown。",
        "presentation_filter": "角色卡、世界书、历史消息和其他上下文中的字体颜色字号、粗斜体、文本框/代码框、HTML/CSS、Markdown布局等视觉排版要求均已过滤；若仍有残留，一律忽略，不作为角色设定执行。",
        "worldbook_before_character": clean(sections[WorldbookEntry.POSITION_BEFORE_CHARACTER]),
        "character": {
            "name": clean(actor.name), "personality": clean(actor.personality), "speech_habits": clean(actor.speech_habits),
            "scenario": clean(actor.scenario),
            "memories": clean(actor.memories), "state_fields": clean(flatten_actor_state(actor, actor.state_fields)),
            "fixed_status": clean({"affinity": actor.affinity, "clothing_type": actor.clothing_type, "clothing_state": actor.clothing_state}),
        },
        "relationships": clean(actor.relationship_notes),
        "worldbook_after_character": clean(sections[WorldbookEntry.POSITION_AFTER_CHARACTER]),
        "present_characters": clean([{"id": str(item.id), "name": item.name} for item in participants]),
        "environment": clean(conversation.environment),
        "world_background": clean(options["world_background"]),
        "global_prompt": clean(options["global_prompt"]),
        "welcome_message": clean(options["welcome_message"]),
        "long_memory": clean(memory['long_memory']),
        "locked_facts": clean(memory['locked_facts']),
        "user_profile": clean(profile.inferred_traits),
        "language": conversation.language,
        "strict_persona": options["strict_persona"],
        "reply_format": options["reply_format"],
        "auto_state_extraction": options["auto_state_extraction"],
        "content_preference": options["adult_content_preference"],
        "content_preference_keywords": clean(options.get("adult_content_keywords", [])),
        "worldbook_before_recent": clean(sections[WorldbookEntry.POSITION_BEFORE_RECENT]),
        "worldbook_entry_ids": [str(item.id) for item in included_entries],
        "worldbook_omitted_entry_ids": omitted_entry_ids,
    }
    unconsumed = [
        {"speaker": clean(item.speaker.name if item.speaker else "场景"), "kind": item.kind, "content": clean(item.content)}
        for item in reversed(recent)
        if str(actor.id) not in item.consumed_by and item.kind not in (Message.STATE, Message.THOUGHT)
    ]
    user = {
        "unconsumed_messages": [item for item in unconsumed if item["kind"] != Message.OOC],
        "director_instructions": clean([item["content"] for item in unconsumed if item["kind"] == Message.OOC]),
        "director_hint": clean(hints or ""), "output": "仅返回 JSON 对象",
    }
    from .context_selection import character_segments,select_context,estimate_tokens
    optional=[]
    if actor.context_policy.get('confirmed'):
        system['character']['personality']=clean(actor.context_policy.get('core_text',''))
        system['character']['memories']=''
        for segment in character_segments(actor):
            if segment.get('required'):
                system.setdefault('required_character_segments',[]).append(clean(segment))
            else:
                optional.append(segment)
    capacity=options.get('context_capacity',65536)
    budget=capacity-options.get('max_tokens',4096)-1024
    optional += [{'id':str(entry.id),'content':entry.content,'name':entry.name,'position':entry.insertion_position,'kind':'worldbook'} for entry in optional_worldbook_entries]
    selection=select_context(json.dumps(system,ensure_ascii=False),[],optional,'\n'.join(public_recent),max(1,budget-estimate_tokens(user)-128))
    system['character_detail_segments']=clean([item for item in selection['selected'] if item.get('kind')!='worldbook'])
    for entry in selection['selected']:
        if entry.get('kind')=='worldbook':
            key={WorldbookEntry.POSITION_BEFORE_CHARACTER:'worldbook_before_character',WorldbookEntry.POSITION_AFTER_CHARACTER:'worldbook_after_character',WorldbookEntry.POSITION_BEFORE_RECENT:'worldbook_before_recent'}[entry['position']]
            system[key].append(clean({'id':entry['id'],'name':entry['name'],'content':entry['content']}))
            system['worldbook_entry_ids'].append(entry['id'])
    omitted_entry_ids.extend(item['id'] for item in selection['excluded'] if any(str(entry.id)==item['id'] for entry in optional_worldbook_entries))
    if context_meta is not None:
        context_meta.update({key:value for key,value in selection.items() if key!='selected'})
        selected_ids=set(system['worldbook_entry_ids'])
        context_meta['trace_selected']=[{'id':str(entry.id),'name':clean(entry.name),'content':clean(entry.content),'position':entry.insertion_position,'mode':entry.trigger_mode,'keywords':entry.keywords,'matched_keywords':[key for key in entry.keywords if key.casefold() in '\n'.join(public_recent).casefold()],'scope':entry.scope_type,'priority':entry.priority,'reason':{'always':'始终使用','manual':'已手动选择','keyword':'关键词命中且预算允许'}[entry.trigger_mode]} for entry in resolved_entries if str(entry.id) in selected_ids]+clean([item for item in selection['selected'] if item.get('kind')!='worldbook'])
        context_meta['trace_character']={'character':system['character'],'relationships':system['relationships'],'environment':system['environment'],'locked_facts':system['locked_facts'],'long_memory':system['long_memory'],'story_revision':conversation.story_revision,'memory_revision':conversation.memory_revision,'state_revision':ensure_story_state(conversation,actor).revision}
        resolved_ids={str(entry.pk) for entry in resolved_entries}
        context_meta['excluded'].extend(row for row in exclusions if row['id'] not in resolved_ids)
    # 将估算报告、JSON 外壳及消息角色标签的开销一并计入最终门限。
    if estimate_tokens(system)+estimate_tokens(user)>budget:
        raise ValueError('必要设定超过上下文预算：消息与报告开销超过剩余容量，请调整预算。')
    return [
        {"role": "system", "content": json.dumps(system, ensure_ascii=False)},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]


def flatten_actor_state(actor, value):
    state = dict(value or {})
    wrapped = state.pop(actor.name, None)
    if isinstance(wrapped, dict):
        for name, item in wrapped.items():
            state.setdefault(name, item)
    return state


def apply_result(conversation, actor, result, consumed_ids, *, expected_revision=None, expected_story_revision=None, job_id=None, lease_token=None, context_trace_id=None):
    with transaction.atomic():
        current = ensure_story_state(conversation, actor)
        saved = apply_actor_result(conversation, actor, result,
            current.revision if expected_revision is None else expected_revision,
            expected_story_revision=expected_story_revision, job_id=job_id, lease_token=lease_token)
        for field, kind in (("thought", Message.THOUGHT), ("narration", Message.NARRATION), ("action", Message.ACTION), ("dialogue", Message.DIALOGUE)):
            content = result.get(field, "").strip()
            if content:
                Message.objects.create(conversation=conversation, speaker=actor, kind=kind, content=content, source="ai",context_trace_id=context_trace_id)
        Message.objects.create(
            conversation=conversation, speaker=actor, kind=Message.STATE,
            content=f"{actor.name}的当前状态", source="ai", state_snapshot={**saved.state_fields, "affinity": saved.affinity, "clothing_type": saved.clothing_type, "clothing_state": saved.clothing_state},
        )
        for item in Message.objects.filter(pk__in=consumed_ids, conversation=conversation):
            if str(actor.id) not in item.consumed_by:
                item.consumed_by = [*item.consumed_by, str(actor.id)]
                item.save(update_fields=["consumed_by"])
        conversation.save(update_fields=["updated_at"])


def _generation_input(conversation, actor, hints, options, *, job=None):
    state_revision = ensure_story_state(conversation, actor).revision
    context_diagnostics={}
    messages = build_messages(conversation, actor, hints, options, context_meta=context_diagnostics)
    system = json.loads(messages[0]["content"])
    trace_id=None
    if job is not None:
        from .generation_trace import record_context_trace
        trace=record_context_trace(job,actor,context_diagnostics.pop('trace_selected',[]),context_diagnostics.get('excluded',[]),context_diagnostics,
            options=options,character_snapshot=context_diagnostics.pop('trace_character',{}))
        trace_id=str(trace.pk)
    recent = list(conversation.messages.order_by("-created_at", "-id")[:100])
    consumed_ids = [
        item.id for item in recent
        if str(actor.id) not in item.consumed_by and item.kind not in (Message.STATE, Message.THOUGHT)
    ]
    return messages, consumed_ids, {
        "state_revision": state_revision,
        "worldbook_entry_ids": system.get("worldbook_entry_ids", []),
        "worldbook_omitted_entry_ids": system.get("worldbook_omitted_entry_ids", []),
        "context_diagnostics": context_diagnostics,
        "trace_id": trace_id,
    }


def process_job(job_id):
    from datetime import timedelta
    from threading import Event,Thread
    from django.db import close_old_connections
    import uuid
    token=uuid.uuid4().hex
    now=timezone.now()
    changed=GenerationJob.objects.filter(pk=job_id,status='queued').update(status='running',started_at=now,lease_token=token,lease_until=now+timedelta(seconds=180))
    if not changed:
        return
    stop=Event()
    def renew():
        close_old_connections()
        try:
            while not stop.wait(30):
                count=GenerationJob.objects.filter(pk=job_id,status='running',lease_token=token,lease_until__gte=timezone.now()).update(lease_until=timezone.now()+timedelta(seconds=180))
                if not count:
                    break
        finally:
            close_old_connections()
    renewal=Thread(target=renew,daemon=True)
    renewal.start()
    try:
        return _process_job(job_id,token)
    finally:
        stop.set()
        renewal.join(timeout=2)


def _process_job(job_id,lease_token):
    job_started_at = time.monotonic()
    job = GenerationJob.objects.select_related('conversation','requested_by').get(pk=job_id)
    if job.status!='running' or job.lease_token!=lease_token:
        return
    conversation = job.conversation
    def leased_job():
        return GenerationJob.objects.filter(pk=job.pk,status='running',lease_token=lease_token,lease_until__gte=timezone.now())
    queue_wait_ms = max(0, round((timezone.now() - job.created_at).total_seconds() * 1000))
    options = effective_values(job.requested_by)
    from .story_snapshots import GENERATION_FIELDS
    options.update({key:value for key,value in conversation.generation_configuration.items() if key in GENERATION_FIELDS})
    key = active_api_key(job.requested_by)
    if not key:
        job.status = "failed"
        job.error = "请先在模型设置中配置 API 密钥"
        job.finished_at = timezone.now()
        changed=leased_job().update(status=job.status,error=job.error,finished_at=job.finished_at)
        if changed and job.task_kind=='memory':
            Conversation.objects.filter(pk=conversation.pk).update(memory_summary_status='failed',memory_summary_error='请先配置可用的模型 API 密钥')
        return
    actors = list(Character.objects.filter(id__in=job.actor_ids, owner=job.requested_by))
    if job.task_kind == 'memory':
        maintain_context(conversation,options,key,force_memory=True,expected_story_revision=job.story_revision,job_id=job.pk,lease_token=lease_token)
        conversation.refresh_from_db()
        leased_job().update(
            status='done' if conversation.memory_summary_status=='done' else 'failed',
            error=conversation.memory_summary_error,finished_at=timezone.now())
        return
    actors.sort(key=lambda item: job.actor_ids.index(str(item.id)))
    progress = {}
    successes = 0

    def run_one(actor, prepared, callback):
        messages, consumed_ids, context_meta = prepared
        return actor, call_model(messages, options, key, on_chunk=callback), consumed_ids, context_meta

    def finish_actor(actor, result, consumed_ids, context_meta):
        nonlocal successes
        persist_started_at = time.monotonic()
        raw_response = result.copy()
        timings = raw_response.pop("_timings", {})
        if not options["auto_state_extraction"]:
            result["state_updates"] = {}
        apply_result(conversation, actor, result, consumed_ids,
            expected_revision=context_meta['state_revision'], expected_story_revision=job.story_revision, job_id=job.pk, lease_token=lease_token,context_trace_id=context_meta.get('trace_id'))
        timings["persist_ms"] = round((time.monotonic() - persist_started_at) * 1000)
        timings["queue_wait_ms"] = queue_wait_ms
        timings["actor_total_ms"] = round((time.monotonic() - job_started_at) * 1000)
        progress[str(actor.id)] = {"status": "done", "timings": timings, **context_meta}
        if options["save_raw_response"]:
            progress[str(actor.id)]["raw_response"] = raw_response
        successes += 1

    def save_progress():
        job.progress = dict(progress)
        if not leased_job().update(progress=job.progress):
            raise StoryConflict('生成任务已取消或租约已过期')

    if job.mode == "parallel" and len(actors) > 1:
        chunks = Queue()

        def flush_chunks():
            changed = False
            while True:
                try:
                    actor_id, partial = chunks.get_nowait()
                except Empty:
                    break
                if progress.get(actor_id, {}).get("status") not in {"done", "failed"}:
                    progress[actor_id] = {"status": "running", **partial} if isinstance(partial, dict) else {"status": "running", "partial": partial}
                    changed = True
            if changed:
                save_progress()

        with ThreadPoolExecutor(max_workers=min(len(actors), 4)) as pool:
            futures = {
                pool.submit(
                    run_one, actor,
                    _generation_input(conversation, actor, job.director_hints.get(str(actor.id)), options, job=job),
                    lambda partial, actor_id=str(actor.id): chunks.put((actor_id, partial)),
                ): actor
                for actor in actors
            }
            pending = set(futures)
            while pending:
                done, pending = wait(pending, timeout=0.4, return_when=FIRST_COMPLETED)
                flush_chunks()
                for future in done:
                    actor = futures[future]
                    try:
                        _, result, consumed_ids, context_meta = future.result()
                        finish_actor(actor, result, consumed_ids, context_meta)
                    except Exception as exc:
                        previous = progress.get(str(actor.id), {})
                        progress[str(actor.id)] = {"status": "failed", "error": str(exc)[:200], "partial": previous.get("partial", ""), "retryable": bool(previous.get("partial"))}
                    save_progress()
    else:
        for actor in actors:
            def serial_chunk(partial):
                progress[str(actor.id)] = {"status": "running", **partial} if isinstance(partial, dict) else {"status": "running", "partial": partial}
                save_progress()

            try:
                _, result, consumed_ids, context_meta = run_one(actor, _generation_input(conversation, actor, job.director_hints.get(str(actor.id)), options, job=job), serial_chunk)
                finish_actor(actor, result, consumed_ids, context_meta)
            except Exception as exc:
                previous = progress.get(str(actor.id), {})
                progress[str(actor.id)] = {"status": "failed", "error": str(exc)[:200], "partial": previous.get("partial", ""), "retryable": bool(previous.get("partial"))}
            save_progress()
    job.status = "done" if successes else "failed"
    job.error = "" if successes else "全部角色生成失败"
    job.finished_at = timezone.now()
    changed = leased_job().update(
        status=job.status, error=job.error, finished_at=job.finished_at)
    if successes and changed:
        maintain_context(conversation, options, key)


def maintain_context(conversation, options, key, *, force_memory=False, expected_story_revision=None, job_id=None, lease_token=None):
    def clean(value):
        return sanitize_ai_value(value)[0]

    conversation.refresh_from_db()
    memory_revision = conversation.memory_revision
    story_revision = conversation.story_revision if expected_story_revision is None else expected_story_revision
    def summary_query():
        query=Conversation.objects.filter(pk=conversation.pk,memory_revision=memory_revision,story_revision=story_revision)
        if job_id is not None:
            query=query.filter(pk__in=GenerationJob.objects.filter(pk=job_id,status='running',lease_token=lease_token,lease_until__gte=timezone.now()).values('conversation_id'))
        return query
    total = conversation.messages.exclude(kind=Message.STATE).count()
    memory_threshold = options["memory_threshold"]
    profile_threshold = options["profile_threshold"]
    older_count = total if force_memory else max(0, total - 20)
    if force_memory or (total >= memory_threshold and older_count > conversation.memory_message_count and (
        conversation.memory_message_count == 0 or older_count - conversation.memory_message_count >= memory_threshold
    )):
        start = 0 if force_memory else conversation.memory_message_count
        older = list(conversation.messages.exclude(kind=Message.STATE).select_related("speaker").order_by("created_at", "id")[start:older_count])
        summary_query().update(memory_summary_status='running',memory_summary_error='')
        if older:
            try:
                from .context_selection import estimate_tokens
                previous=conversation.long_memory
                pending=[clean(f"{item.speaker.name if item.speaker else '场景'}: {item.content}") for item in older]
                while pending:
                    if job_id is not None and not summary_query().exists():
                        return
                    group=[]
                    capacity=options.get('context_capacity',65536)-2048
                    base={'previous':clean(previous),'locked_facts':clean(conversation.locked_facts),'messages':group}
                    while pending and len(group)<100:
                        candidate={**base,'messages':[*group,pending[0]]}
                        if estimate_tokens(candidate)>capacity:
                            break
                        group.append(pending.pop(0))
                    if not group:
                        raise ValueError('记忆消息超过上下文预算，请调整模型容量')
                    base['messages']=group
                    result = call_json_model(
                        [{"role": "system", "content": "请把故事事件、人际变化和未解决线索压缩为长期记忆。仅返回 JSON 对象，格式 {\"summary\":\"...\"}。"},
                         {"role": "user", "content": json.dumps(base, ensure_ascii=False)}],
                        options, key, max_tokens=800,
                    )
                    if not isinstance(result.get('summary'),str) or len(result['summary'])>20000:
                        raise ValueError('记忆总结返回格式无效')
                    previous=result['summary']
                accepted=apply_memory_summary(conversation,result['summary'],memory_revision,older_count,expected_story_revision=story_revision,job_id=job_id,lease_token=lease_token)
                if not accepted:
                    summary_query().filter(memory_summary_status='running').update(
                        memory_summary_status='idle',memory_summary_error='故事已变化，过期总结未保存')
            except (RuntimeError, ValueError, httpx.HTTPError):
                summary_query().update(
                    memory_summary_status='failed',memory_summary_error='记忆总结失败，请检查模型连接后重试')
        elif force_memory:
            summary_query().update(
                memory_summary_status='failed',memory_summary_error='当前还没有可总结的消息')
    if force_memory:
        return
    if total >= profile_threshold and total - conversation.profile_message_count >= profile_threshold:
        recent = list(conversation.messages.exclude(kind=Message.STATE).select_related("speaker").order_by("-created_at", "-id")[:40])
        profile, _ = UserProfile.objects.get_or_create(user=conversation.owner)
        try:
            result = call_json_model(
                [{"role": "system", "content": "从故事中提取用户偏好、称呼、角色关系倾向。不要猜测敏感个人信息。仅返回 JSON 对象，格式 {\"inferred_traits\":\"...\"}。"},
                 {"role": "user", "content": json.dumps({"previous": clean(profile.inferred_traits), "messages": [clean(f"{item.speaker.name if item.speaker else '场景'}: {item.content}") for item in reversed(recent)]}, ensure_ascii=False)}],
                options, key, max_tokens=500,
            )
            if isinstance(result.get("inferred_traits"), str):
                profile.inferred_traits = result["inferred_traits"][:10000]
                profile.save(update_fields=["inferred_traits", "updated_at"])
                conversation.profile_message_count = total
                conversation.save(update_fields=["profile_message_count"])
        except (RuntimeError, ValueError):
            pass
