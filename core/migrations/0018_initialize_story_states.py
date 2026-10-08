import copy
from django.db import migrations


def initialize_states(apps,schema_editor):
    Conversation=apps.get_model('core','Conversation')
    State=apps.get_model('core','ConversationActorState')
    Message=apps.get_model('core','Message')
    snapshot_fields=('name','summary','personality','speech_habits','memories','relationship_notes',
                     'state_fields','affinity','clothing_type','clothing_state')
    for conversation in Conversation.objects.using(schema_editor.connection.alias).iterator():
        for link in conversation.conversationparticipant_set.select_related('character').all():
            actor=link.character
            template={key:copy.deepcopy(getattr(actor,key)) for key in snapshot_fields}
            values={key:copy.deepcopy(template[key]) for key in
                    ('state_fields','relationship_notes','affinity','clothing_type','clothing_state')}
            last=Message.objects.filter(conversation=conversation,speaker=actor,kind='state').order_by('-created_at','-id').first()
            source='升级时角色卡'
            if last and isinstance(last.state_snapshot,dict) and last.state_snapshot:
                raw=copy.deepcopy(last.state_snapshot)
                for key in ('affinity','clothing_type','clothing_state'):
                    if key in raw:
                        values[key]=raw.pop(key)
                values['state_fields']=raw
                source='历史状态记录'
            State.objects.get_or_create(conversation=conversation,character=actor,defaults={
                **values,'template_snapshot':template,'initialization_source':source})


class Migration(migrations.Migration):
    dependencies=[('core','0017_story_actor_state')]
    operations=[migrations.RunPython(initialize_states,migrations.RunPython.noop)]
