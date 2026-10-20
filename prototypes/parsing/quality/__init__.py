"""Trusted fictional corpus and conservative offline quality checks; no indexing."""
from io import BytesIO
import json
import os
from pathlib import Path

from prototypes.parsing.parser import ParseError, parse_document


def load_manifest():
    """Read authored gold, never derive expected facts from extraction output."""
    return json.loads(Path(__file__).with_name('manifest.json').read_text(encoding='utf-8'))['cases']


def case_by_id(case_id):
    return next(case for case in load_manifest() if case['id'] == case_id)


def generate(case_id):
    """Generate reproducible bytes in memory; callers explicitly decide any disk write."""
    # Source text intentionally independent of manifest expectations.
    first = '料號 A-1001｜規格 M8x30｜數量 12｜單位 支'
    second = '料號 A-1002｜規格 M8x35｜數量 21｜單位 盒'
    if case_id == 'txt_utf8':
        return (first + '\n\n' + second + '\n').encode('utf-8')
    if case_id == 'txt_bom_crlf':
        return ('\ufeff  ' + first + '\r\n\r\n' + second + '\r\n').encode('utf-8')
    if case_id == 'txt_invalid_utf8':
        return b'\xff\xfe\x00fictional'
    if case_id == 'pdf_malformed':
        return b'%PDF-1.7\nfictional broken PDF\n%%EOF\n'
    if case_id not in {'pdf_basic', 'pdf_columns', 'pdf_table', 'pdf_cross_page', 'pdf_no_text', 'pdf_mixed_blank'}:
        raise ValueError('unknown fictional quality case')

    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    # CID-only fonts can extract correctly while rendering blank on machines without
    # matching CJK font packs. Embed an explicit, locally supplied TrueType font.
    font_path = Path(os.environ.get('PARSING_QUALITY_CJK_FONT', 'C:/Windows/Fonts/msjh.ttc'))
    if not font_path.is_file():
        raise ValueError('set PARSING_QUALITY_CJK_FONT to a local Traditional Chinese TrueType font')
    pdfmetrics.registerFont(TTFont('FictionalQualityCJK', str(font_path), subfontIndex=0))
    stream = BytesIO()
    pdf = canvas.Canvas(stream, pagesize=(595, 842), invariant=1, pageCompression=0)
    pdf.setTitle('Fictional offline parsing quality fixture')
    pdf.setAuthor('Synthetic fixture generator')

    def line(text, x=35, y=770):
        pdf.setFont('FictionalQualityCJK', 11)
        pdf.drawString(x, y, text)

    if case_id == 'pdf_basic':
        line(first)
        pdf.showPage()
        line(second)
    elif case_id == 'pdf_columns':
        # Visually two separate catalogs; interleaved stream is deliberately ambiguous.
        line('甲欄：料號 A-1001', 35, 770)
        line('乙欄：料號 A-1002', 315, 770)
        line('規格 M8x30｜數量 12｜單位 支', 35, 745)
        line('規格 M8x35｜數量 21｜單位 盒', 315, 745)
    elif case_id == 'pdf_table':
        # Draw a real 2-row table in column-major stream order. Loose token tests pass,
        # but extraction cannot establish item/spec/quantity/unit row relationships.
        for x in (35, 180, 300, 400, 490):
            pdf.line(x, 690, x, 780)
        for y in (690, 720, 750, 780):
            pdf.line(35, y, 490, y)
        for x, values in [(45, ['料號', 'A-1001', 'A-1002']), (190, ['規格', 'M8x30', 'M8x35']), (310, ['數量', '12', '21']), (410, ['單位', '支', '盒'])]:
            for y, value in zip((760, 730, 700), values):
                line(value, x, y)
    elif case_id == 'pdf_cross_page':
        line('料號 A-1001｜規格 M8x30')
        line('數量與單位見次頁；A-1002 是不同料號', y=745)
        pdf.showPage()
        line('承前頁 A-1001：數量 12｜單位 支')
        line(second, y=745)
    elif case_id == 'pdf_no_text':
        # Graphic-only, OCR required; the parser must reject instead of fabricating text.
        pdf.rect(40, 650, 200, 100, fill=1)
    elif case_id == 'pdf_mixed_blank':
        line(first)
        pdf.showPage()
    pdf.showPage()
    pdf.save()
    return stream.getvalue()


def assess(case, parsed):
    """Exact local associations, not bags of tokens; this is NOT a table parser."""
    segments = {segment.locator: segment.text for segment in parsed.segments}
    checks = [dict(fact, passed=fact['text'] in segments.get(fact['locator'], '')) for fact in case['facts']]
    source_ranges_valid = True
    if parsed.format == 'txt':
        source_ranges_valid = parsed.source_text is not None and all(
            segment.start is not None and segment.end is not None
            and parsed.source_text[segment.start:segment.end] == segment.text
            for segment in parsed.segments)
    facts_passed = bool(checks) and all(check['passed'] for check in checks) and source_ranges_valid
    basic = case['layout'] == 'basic' and facts_passed
    classification = 'supported_basic' if basic else 'needs_manual_quality_review'
    return {
        'id': case['id'], 'classification': classification,
        'expectation_met': classification == case['expected_classification'],
        'extraction_nonempty': bool(segments) and all(value.strip() for value in segments.values()),
        'facts_passed': facts_passed, 'fact_checks': checks,
        'source_ranges_valid': source_ranges_valid,
        'automatic_structure_supported': basic,
        'limitations': case['limitations'], 'review_required': True, 'indexable': False,
        'human_review': dict(case['human_review']), 'error': None,
    }


def check_case(case):
    try:
        parsed = parse_document(generate(case['id']), case['filename'])
    except ParseError as error:
        return {
            'id': case['id'], 'classification': 'rejected', 'error': error.code,
            'expectation_met': case['expected_classification'] == 'rejected' and error.code == case.get('expected_error'),
            'extraction_nonempty': False, 'facts_passed': False, 'fact_checks': [],
            'source_ranges_valid': None, 'automatic_structure_supported': False,
            'limitations': case['limitations'], 'review_required': True, 'indexable': False,
            'human_review': dict(case['human_review']),
        }
    return assess(case, parsed)
