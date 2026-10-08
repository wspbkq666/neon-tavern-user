from django.test import SimpleTestCase
from .import_merge import preview_merge,duplicate_groups


class ImportMergeTests(SimpleTestCase):
    def item(self,text,state):
        return {'type':'npc','fields':{'name':'原角色','personality':text,'state_fields':{'体力':state},'categories':['档案员']},'source_excerpt':text,'confidence':1,'warnings':[]}

    def test_same_name_is_only_a_candidate_not_an_automatic_merge(self):
        rows=[self.item('不能飞行。',5),self.item('只有通行证持有者能进地下室。',3)]
        self.assertEqual(duplicate_groups(rows)[0]['indices'],[0,1])
        result=preview_merge(rows,[0,1])
        self.assertIsNone(result['conflicts'][0]['selected'])
        self.assertIn('不能飞行。',result['draft']['fields']['personality'])
        self.assertIn('只有通行证持有者能进地下室。',result['draft']['fields']['personality'])
        self.assertEqual(rows[0]['fields']['state_fields']['体力'],5)

    def test_explicit_state_choice_preserves_type_and_originals(self):
        result=preview_merge([self.item('不能飞行。',5),self.item('原文补充。',3)],[0,1],{'fields.state_fields.体力':1})
        self.assertEqual(result['draft']['fields']['state_fields']['体力'],3)
        self.assertEqual(len(result['originals']),2)

    def test_different_roles_cannot_be_merged(self):
        rows=[self.item('原文一。',5),self.item('原文二。',3)];rows[1]['fields']['name']='另一角色'
        with self.assertRaises(ValueError):preview_merge(rows,[0,1])
