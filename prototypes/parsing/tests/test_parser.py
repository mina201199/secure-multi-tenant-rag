"""Offline, in-memory fictional inputs only; no database or model calls."""
import hashlib
from contextlib import redirect_stdout
from io import BytesIO
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from pypdf import PdfWriter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

from prototypes.parsing.parser import Limits, ParseError, parse_document


def run_cli(args):
    from prototypes.parsing import parser
    return parser.main(args)


def pdf_bytes(pages):
    stream = BytesIO()
    pdfmetrics.registerFont(UnicodeCIDFont('MSung-Light'))
    pdf = canvas.Canvas(stream)
    for text in pages:
        pdf.setFont('MSung-Light', 12)
        pdf.drawString(40, 780, text)
        pdf.showPage()
    pdf.save()
    return stream.getvalue()


class ParserTests(unittest.TestCase):
    def assert_error(self, code, data, name, **kwargs):
        with self.assertRaises(ParseError) as raised:
            parse_document(data, name, **kwargs)
        self.assertEqual(raised.exception.code, code)

    def test_utf8_paragraphs_preserve_facts_and_source_ranges(self):
        source = '\ufeff  料號 A-1001｜規格 M8×30｜單位 支\r\n續行：金額 NT$1,500\r\n \r\n第二段：2026-10-20\r\n'
        data = source.encode('utf-8')
        result = parse_document(data, 'fixture.TXT')
        self.assertEqual(result.sha256, hashlib.sha256(data).hexdigest())
        self.assertEqual(len(result.segments), 2)
        self.assertEqual(result.segments[0].locator, 'paragraph:1')
        self.assertIn('NT$1,500', result.segments[0].text)
        self.assertIn('M8×30', result.segments[0].text)
        for segment in result.segments:
            self.assertEqual(result.source_text[segment.start:segment.end], segment.text)
        self.assertFalse(result.indexable)

    def test_deterministic_paragraph_numbers(self):
        data = '第一段\n\n第二段\n第三行\n\n第四段'.encode()
        one = parse_document(data, 'a.txt')
        two = parse_document(data, 'renamed.txt')
        self.assertEqual(one.segments, two.segments)
        self.assertEqual([s.locator for s in one.segments], ['paragraph:1', 'paragraph:2', 'paragraph:3'])

    def test_invalid_utf8_rejected(self):
        self.assert_error('invalid_utf8', b'\xff\xfehello', 'a.txt')

    def test_binary_control_rejected(self):
        self.assert_error('invalid_text', b'hello\x00world', 'a.txt')

    def test_empty_rejected(self):
        self.assert_error('empty_text', b' \n\t', 'a.txt')

    def test_format_and_signature_rejected(self):
        self.assert_error('unsupported_format', b'abc', 'a.docx')
        self.assert_error('invalid_pdf', b'not PDF', 'a.pdf')
        self.assert_error('format_mismatch', b'%PDF-1.7\n', 'a.txt')

    def test_file_size_limit(self):
        self.assert_error('file_too_large', b'abcd', 'a.txt', limits=Limits(max_bytes=3))

    def test_text_size_limit(self):
        self.assert_error('text_too_large', b'abcd', 'a.txt', limits=Limits(max_chars=3))

    def test_real_chinese_pdf_page_locators_and_facts(self):
        result = parse_document(pdf_bytes(['甲公司補助 NT$1,500，料號 A-1001', '規格 M8x30，單位支']), 'a.pdf')
        self.assertEqual([s.locator for s in result.segments], ['page:1', 'page:2'])
        self.assertIn('甲公司', result.segments[0].text)
        self.assertIn('NT$1,500', result.segments[0].text)
        self.assertIn('M8x30', result.segments[1].text)
        self.assertTrue(result.requires_review)
        self.assertFalse(result.indexable)

    def test_pdf_page_limit(self):
        self.assert_error('too_many_pages', pdf_bytes(['one', 'two']), 'a.pdf', limits=Limits(max_pages=1))

    def test_blank_pdf_page_rejects_whole_document(self):
        self.assert_error('unreadable_page', pdf_bytes(['valid text', '']), 'a.pdf')

    def test_encrypted_pdf_rejected(self):
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.encrypt('fixture-only-password')
        data = BytesIO()
        writer.write(data)
        self.assert_error('encrypted_pdf', data.getvalue(), 'a.pdf')

    def test_pdf_extracted_text_limit(self):
        self.assert_error('text_too_large', pdf_bytes(['abcdefghij']), 'a.pdf', limits=Limits(max_chars=3))

    def test_malformed_pdf_rejected_without_leaking_parser_details(self):
        self.assert_error('invalid_pdf', b'%PDF-1.7\ninvalid', 'a.pdf')

    def test_limits_must_be_positive(self):
        with self.assertRaises(ValueError):
            Limits(max_pages=0)

    def test_cli_returns_json_for_trusted_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.txt'
            path.write_text('規格 M8x30\n\n單位 支', encoding='utf-8')
            output = StringIO()
            with redirect_stdout(output):
                status = run_cli([str(path)])
            result = json.loads(output.getvalue())
            self.assertEqual(status, 0)
            self.assertEqual(result['segments'][1]['locator'], 'paragraph:2')
            self.assertFalse(result['indexable'])

    def test_cli_missing_file_has_stable_error_without_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'missing-private-file.txt'
            output = StringIO()
            with redirect_stdout(output):
                status = run_cli([str(path)])
            self.assertEqual(status, 2)
            self.assertEqual(json.loads(output.getvalue()), {'error': 'input_unavailable'})

    def test_unsupported_pdf_filter_has_stable_error(self):
        from pypdf.generic import EncodedStreamObject, NameObject
        writer = PdfWriter()
        page = writer.add_blank_page(width=100, height=100)
        content = EncodedStreamObject()
        content._data = b'fixture stream'
        content[NameObject('/Filter')] = NameObject('/UnsupportedFixtureFilter')
        page[NameObject('/Contents')] = writer._add_object(content)
        stream = BytesIO()
        writer.write(stream)
        self.assert_error('unsupported_pdf_feature', stream.getvalue(), 'a.pdf')


if __name__ == '__main__':
    unittest.main()
