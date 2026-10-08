from django.test import SimpleTestCase
from .import_coverage import build_coverage_report


class ImportCoverageTests(SimpleTestCase):
    def test_no_drafts_do_not_claim_coverage(self):
        report=build_coverage_report('角色禁止飞行。代号为 A/B。',[],[])
        self.assertTrue(report['paragraphs'])
        self.assertTrue(all(row['status']=='待确认' for row in report['paragraphs']))

    def test_source_excerpt_alone_does_not_prove_field_coverage(self):
        source='角色不能飞行。'
        report=build_coverage_report(source,[{'type':'npc','fields':{'name':'角色'},'source_excerpt':source}],[])
        self.assertEqual(report['paragraphs'][0]['status'],'待确认')

    def test_exact_field_content_has_traceable_offsets(self):
        source='角色不能飞行。'
        report=build_coverage_report(source,[{'type':'npc','fields':{'personality':source}}],[])
        row=report['paragraphs'][0]
        self.assertEqual(row['status'],'原文匹配')
        reference=row['references'][0]
        self.assertEqual(source[reference['start']:reference['end']],source)

    def test_formatting_removal_is_visible_and_necessary_symbols_survive(self):
        from .import_coverage import filter_source_with_records
        source='用灰色字写角色很虚弱。代号 A/B。'
        cleaned,records=filter_source_with_records(source)
        self.assertIn('角色很虚弱',cleaned)
        self.assertIn('A/B',cleaned)
        self.assertTrue(records)
        self.assertTrue(all(source[row['start']:row['end']]==row['original'] for row in records))
