"""Isolated parsing spike. Never imported by the production application."""
from dataclasses import asdict, dataclass
import argparse
import hashlib
from io import BytesIO
from pathlib import Path
import re
import json


@dataclass(frozen=True)
class Limits:
    max_bytes: int = 2 * 1024 * 1024
    max_pages: int = 20
    max_chars: int = 200_000

    def __post_init__(self):
        if any(type(v) is not int or v <= 0 for v in (self.max_bytes, self.max_pages, self.max_chars)):
            raise ValueError('limits must be positive integers')


@dataclass(frozen=True)
class Segment:
    locator: str
    text: str
    # TXT: codepoint offsets into source_text after UTF-8 BOM removal, not byte offsets.
    # PDF: offsets unavailable; locator is the physical 1-based file page.
    start: int | None = None
    end: int | None = None


@dataclass(frozen=True)
class ParsedDocument:
    sha256: str
    format: str
    segments: tuple[Segment, ...]
    source_text: str | None = None
    requires_review: bool = True
    indexable: bool = False


class ParseError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _validate_text(text):
    if '\ufffd' in text or any(ord(c) < 32 and c not in '\t\n\r' for c in text):
        raise ParseError('invalid_text')


def _txt(data, limits):
    if data.startswith(b'%PDF-'):
        raise ParseError('format_mismatch')
    try:
        text = data.decode('utf-8-sig', errors='strict')
    except UnicodeDecodeError:
        raise ParseError('invalid_utf8') from None
    _validate_text(text)
    if len(text) > limits.max_chars:
        raise ParseError('text_too_large')
    # CRLF and LF blank lines delimit paragraphs. No whitespace or fact rewriting.
    boundaries = [(-1, 0)]
    boundaries += [(m.start(), m.end()) for m in re.finditer(r'\r?\n[ \t]*\r?\n(?:[ \t]*\r?\n)*', text)]
    boundaries.append((len(text), len(text)))
    segments = []
    for previous, following in zip(boundaries, boundaries[1:]):
        start, end = previous[1], following[0]
        fragment = text[start:end]
        start += len(fragment) - len(fragment.lstrip())
        end -= len(fragment) - len(fragment.rstrip())
        if start < end:
            segments.append(Segment(f'paragraph:{len(segments) + 1}', text[start:end], start, end))
    if not segments:
        raise ParseError('empty_text')
    return tuple(segments), text


def _pdf(data, limits):
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError, PdfStreamError

    if not data.startswith(b'%PDF-'):
        raise ParseError('invalid_pdf')
    try:
        reader = PdfReader(BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise ParseError('encrypted_pdf')
        if len(reader.pages) > limits.max_pages:
            raise ParseError('too_many_pages')
        if not reader.pages:
            raise ParseError('empty_text')
        segments = []
        total = 0
        for index, page in enumerate(reader.pages, 1):
            text = page.extract_text(extraction_mode='plain') or ''
            _validate_text(text)
            total += len(text)
            if total > limits.max_chars:
                raise ParseError('text_too_large')
            if not text.strip():
                # Partial successful pages are discarded if any page lacks readable text.
                raise ParseError('unreadable_page')
            segments.append(Segment(f'page:{index}', text))
        return tuple(segments)
    except ParseError:
        raise
    except NotImplementedError:
        raise ParseError('unsupported_pdf_feature') from None
    except (PdfReadError, PdfStreamError, ValueError, TypeError, KeyError, RecursionError, OSError):
        raise ParseError('invalid_pdf') from None


def parse_document(data: bytes, filename: str, *, limits=Limits()) -> ParsedDocument:
    """Trusted synthetic input only. Not a sandbox for adversarial PDFs.

    Bounds on compressed file size/pages/output do NOT bound decompression or CPU.
    No index publication, database, filesystem writes, network or external model calls.
    """
    if len(data) > limits.max_bytes:
        raise ParseError('file_too_large')
    suffix = Path(filename).suffix.lower()
    if suffix not in {'.pdf', '.txt'}:
        raise ParseError('unsupported_format')
    sha256 = hashlib.sha256(data).hexdigest()
    if suffix == '.txt':
        segments, text = _txt(data, limits)
        return ParsedDocument(sha256, 'txt', segments, source_text=text)
    return ParsedDocument(sha256, 'pdf', _pdf(data, limits))


def main(argv=None):
    cli = argparse.ArgumentParser(description='Trusted fictional PDF/TXT parsing spike; no indexing.')
    cli.add_argument('source', type=Path)
    args = cli.parse_args(argv)
    try:
        # Bound the read itself rather than loading a whole oversized file first.
        with args.source.open('rb') as stream:
            data = stream.read(Limits().max_bytes + 1)
        result = parse_document(data, args.source.name)
    except OSError:
        print(json.dumps({'error': 'input_unavailable'}))
        return 2
    except ParseError as error:
        print(json.dumps({'error': error.code}))
        return 2
    print(json.dumps(asdict(result), ensure_ascii=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
