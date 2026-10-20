"""Offline quality checks against independently authored fictional gold facts."""
from dataclasses import replace
import importlib.util
from io import BytesIO
import unittest

from pypdf import PdfReader

from prototypes.parsing.parser import parse_document


class QualitySamplesTests(unittest.TestCase):
    def quality(self):
        self.assertIsNotNone(importlib.util.find_spec('prototypes.parsing.quality'),
                             'quality corpus and checker have not been implemented')
        from prototypes.parsing import quality
        return quality

    def test_simple_chinese_pdf_and_txt_preserve_neighboring_part_relations(self):
        quality = self.quality()
        for name in ('txt_utf8', 'txt_bom_crlf', 'pdf_basic'):
            with self.subTest(name=name):
                case = quality.case_by_id(name)
                result = quality.check_case(case)
                self.assertEqual(result['classification'], 'supported_basic')
                self.assertTrue(result['facts_passed'])
                self.assertFalse(result['indexable'])
                self.assertTrue(result['review_required'])
                self.assertEqual(result['human_review'], {'reviewer': None, 'reviewed_at': None, 'decision': None})
                parsed = parse_document(quality.generate(case['id']), case['filename'])
                self.assertFalse(parsed.indexable)
                self.assertTrue(parsed.requires_review)
                by_locator = {s.locator: s.text for s in parsed.segments}
                self.assertIn('料號 A-1001｜規格 M8x30｜數量 12｜單位 支', by_locator[case['facts'][0]['locator']])
                self.assertIn('料號 A-1002｜規格 M8x35｜數量 21｜單位 盒', by_locator[case['facts'][1]['locator']])

    def test_raw_txt_encoding_and_exact_source_slices_are_preserved(self):
        quality = self.quality()
        raw = quality.generate('txt_bom_crlf')
        self.assertTrue(raw.startswith(b'\xef\xbb\xbf'))
        self.assertIn(b'\r\n\r\n', raw)
        parsed = parse_document(raw, 'fixture.txt')
        self.assertEqual(parsed.source_text, raw.decode('utf-8-sig'))
        for segment in parsed.segments:
            self.assertEqual(parsed.source_text[segment.start:segment.end], segment.text)
        self.assertNotIn(b'\xef\xbb\xbf', quality.generate('txt_utf8'))

    def test_column_table_and_page_spanning_cases_never_claim_layout_support(self):
        quality = self.quality()
        for name in ('pdf_columns', 'pdf_table', 'pdf_cross_page'):
            with self.subTest(name=name):
                result = quality.check_case(quality.case_by_id(name))
                self.assertTrue(result['extraction_nonempty'])
                self.assertEqual(result['classification'], 'needs_manual_quality_review')
                self.assertFalse(result['automatic_structure_supported'])
                self.assertFalse(result['indexable'])
                self.assertTrue(result['limitations'])
        table = quality.check_case(quality.case_by_id('pdf_table'))
        self.assertFalse(table['facts_passed'], 'loose token presence must not prove row associations')
        table_text = parse_document(quality.generate('pdf_table'), 'table.pdf').segments[0].text
        for token in ('A-1001', 'A-1002', 'M8x30', 'M8x35', '12', '21', '支', '盒'):
            self.assertIn(token, table_text)
        self.assertTrue(quality.check_case(quality.case_by_id('pdf_cross_page'))['facts_passed'],
                        'correct local facts still do not establish cross-page relationship support')

    def test_swapped_quantities_fail_even_when_every_token_survives(self):
        quality = self.quality()
        case = quality.case_by_id('txt_utf8')
        parsed = parse_document(quality.generate('txt_utf8'), case['filename'])
        corrupted = replace(parsed, segments=tuple(replace(s, text=s.text.replace('數量 12', '數量 TEMP').replace('數量 21', '數量 12').replace('數量 TEMP', '數量 21')) for s in parsed.segments))
        report = quality.assess(case, corrupted)
        self.assertFalse(report['facts_passed'])
        self.assertEqual(report['classification'], 'needs_manual_quality_review')

    def test_correct_fact_on_wrong_page_fails_source_location_check(self):
        quality = self.quality()
        case = quality.case_by_id('pdf_basic')
        parsed = parse_document(quality.generate('pdf_basic'), case['filename'])
        corrupted = replace(parsed, segments=tuple(replace(s, text=parsed.segments[1 - i].text) for i, s in enumerate(parsed.segments)))
        self.assertFalse(quality.assess(case, corrupted)['facts_passed'])

    def test_invalid_and_no_text_cases_are_rejected_with_expected_codes(self):
        quality = self.quality()
        for name, code in [('pdf_malformed', 'invalid_pdf'), ('pdf_no_text', 'unreadable_page'), ('pdf_mixed_blank', 'unreadable_page'), ('txt_invalid_utf8', 'invalid_utf8')]:
            with self.subTest(name=name):
                result = quality.check_case(quality.case_by_id(name))
                self.assertEqual(result['classification'], 'rejected')
                self.assertEqual(result['error'], code)
                self.assertTrue(result['expectation_met'])
                self.assertFalse(result['indexable'])

    def test_corpus_is_byte_reproducible_and_report_has_no_review_signoff(self):
        quality = self.quality()
        for case in quality.load_manifest():
            with self.subTest(name=case['id']):
                self.assertEqual(quality.generate(case['id']), quality.generate(case['id']))
                report = quality.check_case(case)
                self.assertTrue(report['expectation_met'])
                self.assertFalse(report['indexable'])
                self.assertEqual(report['human_review']['decision'], None)

    def test_chinese_pdf_embeds_a_font_instead_of_relying_on_renderer_cid_fonts(self):
        quality = self.quality()
        page = PdfReader(BytesIO(quality.generate('pdf_basic'))).pages[0]
        fonts = [font.get_object() for font in page['/Resources']['/Font'].values()]
        self.assertTrue(any('/FontFile2' in font.get('/FontDescriptor', {}) for font in fonts),
                        'Chinese fixture must embed its glyphs for source visual inspection')


if __name__ == '__main__':
    unittest.main()
