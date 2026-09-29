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


def build_messages(conversation, actor, hints, options):
    def clean(value):
        return sanitize_ai_value(value)[0]

    participants = list(conversation.participants.all())
    profile, _ = UserProfile.objects.get_or_create(user=conversation.owner)
    recent = list(conversation.messages.select_related("speaker").order_by("-created_at", "-id")[:100])
    public_recent = [item.content for item in reversed(recent) if item.kind not in (Message.STATE, Message.THOUGHT)]
    resolved_entries = resolve_worldbook_entries(conversation, actor, "\n".join(public_recent))
    included_entries = []
    omitted_entry_ids = []
    used_characters = 0
    for entry in sorted(resolved_entries, key=lambda item: (-item.priority, item.created_at, str(item.id))):
        if used_characters + len(entry.content) > 40000:
            omitted_entry_ids.append(str(entry.id))
            continue
        included_entries.append(entry)
        used_characters += len(entry.content)
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
        "long_memory": clean(conversation.long_memory),
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


def apply_result(conversation, actor, result, consumed_ids):
    with transaction.atomic():
        actor = Character.objects.select_for_update().get(pk=actor.pk)
        state = flatten_actor_state(actor, actor.state_fields)
        for name, value in flatten_actor_state(actor, result["state_updates"]).items():
            if isinstance(value, dict) and set(value) == {"delta"} and isinstance(value["delta"], (int, float)) and not isinstance(value["delta"], bool):
                current = state.get(name, 0)
                state[name] = current + value["delta"] if isinstance(current, (int, float)) and not isinstance(current, bool) else value["delta"]
            else:
                state[name] = value
        actor.state_fields = state
        fixed = result.get("fixed_status", {})
        if "affinity" in fixed:
            actor.affinity = max(0, min(100, round(fixed["affinity"])))
        for field in ("clothing_type", "clothing_state"):
            if field in fixed:
                setattr(actor, field, fixed[field].strip())
        actor.save(update_fields=["state_fields", "affinity", "clothing_type", "clothing_state", "updated_at"])
        for field, kind in (("thought", Message.THOUGHT), ("narration", Message.NARRATION), ("action", Message.ACTION), ("dialogue", Message.DIALOGUE)):
            content = result.get(field, "").strip()
            if content:
                Message.objects.create(conversation=conversation, speaker=actor, kind=kind, content=content, source="ai")
        Message.objects.create(
            conversation=conversation, speaker=actor, kind=Message.STATE,
            content=f"{actor.name}的当前状态", source="ai", state_snapshot={**state, "affinity": actor.affinity, "clothing_type": actor.clothing_type, "clothing_state": actor.clothing_state},
        )
        for item in Message.objects.filter(pk__in=consumed_ids):
            if str(actor.id) not in item.consumed_by:
                item.consumed_by = [*item.consumed_by, str(actor.id)]
                item.save(update_fields=["consumed_by"])
        conversation.save(update_fields=["updated_at"])


def _generation_input(conversation, actor, hints, options):
    messages = build_messages(conversation, actor, hints, options)
    system = json.loads(messages[0]["content"])
    recent = list(conversation.messages.order_by("-created_at", "-id")[:100])
    consumed_ids = [
        item.id for item in recent
        if str(actor.id) not in item.consumed_by and item.kind not in (Message.STATE, Message.THOUGHT)
    ]
    return messages, consumed_ids, {
        "worldbook_entry_ids": system.get("worldbook_entry_ids", []),
        "worldbook_omitted_entry_ids": system.get("worldbook_omitted_entry_ids", []),
    }


def process_job(job_id):
    job_started_at = time.monotonic()
    with transaction.atomic():
        job = GenerationJob.objects.select_for_update().select_related("conversation", "requested_by").get(pk=job_id)
        if job.status != "queued":
            return
        job.status = "running"
        job.started_at = timezone.now()
        job.save(update_fields=["status", "started_at"])
    conversation = job.conversation
    queue_wait_ms = max(0, round((timezone.now() - job.created_at).total_seconds() * 1000))
    options = effective_values(job.requested_by)
    key = active_api_key(job.requested_by)
    if not key:
        job.status = "failed"
        job.error = "请先在模型设置中配置 API 密钥"
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error", "finished_at"])
        return
    actors = list(Character.objects.filter(id__in=job.actor_ids, owner=job.requested_by))
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
        apply_result(conversation, actor, result, consumed_ids)
        timings["persist_ms"] = round((time.monotonic() - persist_started_at) * 1000)
        timings["queue_wait_ms"] = queue_wait_ms
        timings["actor_total_ms"] = round((time.monotonic() - job_started_at) * 1000)
        progress[str(actor.id)] = {"status": "done", "timings": timings, **context_meta}
        if options["save_raw_response"]:
            progress[str(actor.id)]["raw_response"] = raw_response
        successes += 1

    def save_progress():
        job.progress = dict(progress)
        job.save(update_fields=["progress"])

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
                    _generation_input(conversation, actor, job.director_hints.get(str(actor.id)), options),
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
                _, result, consumed_ids, context_meta = run_one(actor, _generation_input(conversation, actor, job.director_hints.get(str(actor.id)), options), serial_chunk)
                finish_actor(actor, result, consumed_ids, context_meta)
            except Exception as exc:
                previous = progress.get(str(actor.id), {})
                progress[str(actor.id)] = {"status": "failed", "error": str(exc)[:200], "partial": previous.get("partial", ""), "retryable": bool(previous.get("partial"))}
            save_progress()
    job.status = "done" if successes else "failed"
    job.error = "" if successes else "全部角色生成失败"
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "error", "finished_at"])
    if successes:
        maintain_context(conversation, options, key)


def maintain_context(conversation, options, key):
    def clean(value):
        return sanitize_ai_value(value)[0]

    total = conversation.messages.exclude(kind=Message.STATE).count()
    memory_threshold = options["memory_threshold"]
    profile_threshold = options["profile_threshold"]
    older_count = max(0, total - 20)
    if total >= memory_threshold and older_count > conversation.memory_message_count and (
        conversation.memory_message_count == 0 or older_count - conversation.memory_message_count >= memory_threshold
    ):
        older = list(conversation.messages.exclude(kind=Message.STATE).select_related("speaker").order_by("created_at", "id")[conversation.memory_message_count:older_count])
        if older:
            try:
                result = call_json_model(
                    [{"role": "system", "content": "请把故事事件、人际变化和未解决线索压缩为长期记忆。仅返回 JSON 对象，格式 {\"summary\":\"...\"}。"},
                     {"role": "user", "content": json.dumps({"previous": clean(conversation.long_memory), "messages": [clean(f"{item.speaker.name if item.speaker else '场景'}: {item.content}") for item in older[-100:]]}, ensure_ascii=False)}],
                    options, key, max_tokens=800,
                )
                if isinstance(result.get("summary"), str):
                    conversation.long_memory = result["summary"][:20000]
                    conversation.memory_message_count = older_count
                    conversation.save(update_fields=["long_memory", "memory_message_count"])
            except (RuntimeError, ValueError):
                pass
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
