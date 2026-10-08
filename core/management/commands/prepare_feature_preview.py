"""仅给开发副本准备中文测试材料，不连接模型服务。"""
import json
import secrets
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand,CommandError
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from core.models import Character,Conversation,ConversationParticipant,Message,UserProfile,ImportTaskEvent
from core.auth_api import PRIVACY_POLICY_VERSION,USAGE_RULES_VERSION
from core.settings_api import effective_values
from core.smart_import import normalize_smart_import
from core.smart_import_extract import ExtractedDocument
from core.import_tasks import create_import_task
from core.story_state import ensure_story_state
from core.story_snapshots import create_checkpoint


class Command(BaseCommand):
    help='准备本地测试账号、故事、导入草稿；不请求真实 AI'

    @transaction.atomic
    def handle(self,*args,**options):
        root=Path(settings.BASE_DIR).resolve()
        if not settings.DEBUG or Path(settings.DATABASES['default']['NAME']).resolve()!=root/'db.sqlite3':
            raise CommandError('仅允许在本地开发副本的 db.sqlite3 中准备测试数据')
        # SQLite 先取得写锁，避免密码计算期间读事务被工作进程的轮询写入打断。
        get_user_model().objects.filter(pk=-1).update(is_active=True)
        credentials=root.parent/'.本地验收账号.json'
        if get_user_model().objects.filter(username='本地验收').exists():
            if not credentials.exists():
                raise CommandError('测试账号已存在，请保留其原密码；不会自动重置')
            self.stdout.write('本地测试账号已准备，原有测试数据保留。')
            return
        password=secrets.token_urlsafe(18)
        user=get_user_model().objects.create_user(username='本地验收',password=password)
        UserProfile.objects.update_or_create(user=user,defaults={'policy_consent_at':timezone.now(),
            'privacy_policy_version':PRIVACY_POLICY_VERSION,'usage_rules_version':USAGE_RULES_VERSION})
        player=Character.objects.create(owner=user,name='林岚（测试玩家）',is_player_controlled=True,
            personality='身份：钟塔档案员。能力：读取旧纸上的墨迹。禁止凭空创造记忆。',state_fields={'体力':5,'身份':{'等级':2}},relationship_notes={'盟约':'永久'})
        actor=Character.objects.create(owner=user,name='阿澄（测试NPC）',summary='雨夜值守的档案员',personality='阿澄是钟塔档案员。不能飞行。除非持有通行证，否则不能进入地下室。',
            memories='曾在钟塔修复一本被雨水损坏的日志。',scenario='雨夜的钟塔。',first_mes='你带来了通行证吗？',alternate_greetings=['钟声响了，进来避雨吧。'],
            state_fields={'体力':5,'当前状态':'平静'},relationship_notes={'盟约':'永久'},
            character_worldbook={'entries':[{'name':'档案员对话规则','content':'档案员不能把猜测描述成已经发生的事实。','trigger_mode':'always'},
                {'name':'地下室补充设定','content':'地下室收藏修复中的钟表。','trigger_mode':'keyword','keywords':['地下室']}]})
        for title in ('钟塔 · 测试故事甲','钟塔 · 测试故事乙'):
            story=Conversation.objects.create(owner=user,title=title,player_character=player,environment={'时间':'雨夜','地点':'钟塔'})
            for index,character in enumerate((player,actor)):
                ConversationParticipant.objects.create(conversation=story,character=character,position=index)
                ensure_story_state(story,character)
            Message.objects.create(conversation=story,speaker=actor,kind='dialogue',source='user',content='【测试预置消息，未调用真实AI】你带来了通行证吗？')
            create_checkpoint(story,'测试初始节点')
        source='世界设定：钟塔位于河畔，禁止在雨夜开启地下室。\n角色：阿澄是档案员，不能飞行。\n玩家：林岚是档案员，体力为5。\n输出字体改成蓝色。'
        items=[{'type':'worldbook','fields':{'name':'钟塔测试世界','description':'河畔的钟塔','entries':[{'name':'钟塔规则','content':'钟塔位于河畔，禁止在雨夜开启地下室。','keywords':['钟塔'],'scoped_characters':[],'trigger_mode':'always'}]},'source_excerpt':'钟塔位于河畔，禁止在雨夜开启地下室。','confidence':1,'warnings':[]},
            {'type':'npc','fields':{'name':'阿澄','personality':'阿澄是档案员，不能飞行。','categories':['档案员']},'source_excerpt':'阿澄是档案员，不能飞行。','confidence':1,'warnings':[]}]
        drafts,payload=normalize_smart_import({'items':items})
        task=create_import_task(user,ExtractedDocument('钟塔功能验收.txt',source,[]),effective_values(user))
        task.status='done';task.result={'drafts':drafts,'payload':payload,'batch_name':'钟塔测试批次'};task.save()
        task.segments.update(status='done',raw_output=json.dumps({'items':items},ensure_ascii=False),result={'items':items})
        ImportTaskEvent.objects.create(task=task,payload={'type':'progress','message':'这是本地测试预置结果，未连接真实 AI。'})
        ImportTaskEvent.objects.create(task=task,payload={'type':'raw_delta','text':json.dumps({'items':items},ensure_ascii=False)})
        credentials.write_text(json.dumps({'说明':'仅用于 127.0.0.1:8766 本地测试，未使用真实账号或密钥','账号':'本地验收','密码':password},ensure_ascii=False,indent=2),encoding='utf-8')
        self.stdout.write('本地测试数据准备完成。测试账号保存在工作区根目录的 .本地验收账号.json。')
