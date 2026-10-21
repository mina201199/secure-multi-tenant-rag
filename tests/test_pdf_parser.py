"""Run standalone with pytest --noconftest tests/test_pdf_parser.py."""
import importlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from tests.pdf_fixtures import make_pdf


@pytest.mark.parametrize('commands', [
    b'BT /F1 12 Tf 1 0 0 1 50 740 Tm (One) Tj 1 0 0 1 50 740 Tm (Two) Tj ET',
    b'BT /F1 12 Tf 1 0 0 1 50 740 Tm [(A) ' + b'-200 ' * 300 + b'(B)] TJ ET',
    b'BT /F1 12 Tf 1 0 0 1 50 740 Tm [(A) ' + b'200 ' * 3 + b'(B)] TJ ET',
], ids=['same-position', 'cumulative-overflow', 'cumulative-overlap'])
def test_merged_or_adjusted_text_operations_cannot_hide_geometry(parser, commands):
    from io import BytesIO
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject
    writer = PdfWriter(clone_from=PdfReader(BytesIO(make_pdf())))
    stream = DecodedStreamObject()
    stream.set_data(commands)
    writer.pages[0][NameObject('/Contents')] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    with pytest.raises(parser.PdfProcessingError, match='unsupported_pdf'):
        parser.parsePdf(output.getvalue())


@pytest.mark.parametrize('change', ['white', 'zero-size', 'crop', 'overflow', 'overlap'])
def test_invisible_clipped_or_overlapping_text_is_rejected(parser, change):
    from io import BytesIO
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject, RectangleObject
    writer = PdfWriter(clone_from=PdfReader(BytesIO(make_pdf(('Visible part AB-1001\nSecond line',)))))
    page = writer.pages[0]
    data = page.get_contents().get_data()
    if change == 'white':
        data = b'1 g\n' + data
    elif change == 'zero-size':
        data = data.replace(b'/F1 12 Tf', b'/F1 0 Tf')
    elif change == 'crop':
        page[NameObject('/CropBox')] = RectangleObject([0, 0, 612, 100])
    elif change == 'overflow':
        data = data.replace(b'1 50 ', b'1 610 ')
    elif change == 'overlap':
        data = data.replace(b'50 722 Tm', b'50 739 Tm')
    stream = DecodedStreamObject()
    stream.set_data(data)
    page[NameObject('/Contents')] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    with pytest.raises(parser.PdfProcessingError, match='unsupported_pdf'):
        parser.parsePdf(output.getvalue())


@pytest.fixture
def parser():
    assert importlib.util.find_spec("app.pdf_parser") is not None, "bounded PDF parser is missing"
    return importlib.import_module("app.pdf_parser")


def test_native_text_preserves_pages_and_numbers(parser):
    assert parser.parsePdf(make_pdf(("Part AB-001: 1,234.50\nQty 007", "Page two: -0.05 / 99%"))) == [
        {"page": 1, "text": "Part AB-001: 1,234.50\nQty 007"},
        {"page": 2, "text": "Page two: -0.05 / 99%"},
    ]


def test_traditional_chinese_and_part_values_keep_exact_unicode(parser):
    text = '出差補助 NT$1,500\n料號 AB-001 規格 8 mm 單位：支'
    assert parser.parsePdf(make_pdf((text,))) == [{'page': 1, 'text': text}]


def test_hard_memory_limit_prevents_unbounded_allocation(parser):
    script = ("import importlib.util\n"
              f"s=importlib.util.spec_from_file_location('child',{str(Path(__file__).resolve().parents[1] / 'app/pdf_child.py')!r})\n"
              "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);m._confine()\n"
              "try:\n data=bytearray(1024*1024*1024);raise AssertionError('allocation permitted')\n"
              "except MemoryError:\n print('limited')\n")
    result = subprocess.run([sys.executable, '-I', '-c', script], capture_output=True, timeout=5)
    assert result.returncode == 0 and result.stdout == b'limited\n'


def test_hard_cpu_limit_terminates_actual_busy_parser(parser, monkeypatch, tmp_path):
    child_script(monkeypatch, parser, tmp_path,
      "import importlib.util\n"
      f"s=importlib.util.spec_from_file_location('child',{str(Path(__file__).resolve().parents[1] / 'app/pdf_child.py')!r})\n"
      "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);m._confine()\nwhile True: pass\n")
    with pytest.raises(parser.PdfProcessingError, match='pdf_resource_limit'):
      parser.parsePdf(make_pdf())


@pytest.mark.parametrize("source,code", [
    (b"not a pdf", "invalid_pdf"),
    (b"%PDF-1.7\ncorrupt", "invalid_pdf"),
    (b"%PDF-" + b"x" * (2 * 1024 * 1024), "pdf_limit_exceeded"),
    (make_pdf(encrypted=True), "encrypted_pdf"),
    (make_pdf(("",)), "unreadable_page"),
    (make_pdf(("Native", "")), "unreadable_page"),
    (make_pdf(image_pages=(0,)), "unsupported_pdf"),
    (make_pdf(rotated=True), "unsupported_pdf"),
    (make_pdf(two_columns=True), "unsupported_pdf"),
    (make_pdf(active="action"), "unsupported_pdf"),
    (make_pdf(active="form"), "unsupported_pdf"),
    (make_pdf(active="attachment"), "unsupported_pdf"),
    (make_pdf(tuple("Page" for _ in range(21))), "pdf_limit_exceeded"),
    (make_pdf(("X" * 200001,)), "pdf_limit_exceeded"),
    (make_pdf(("Bad\x00text",)), "unreadable_page"),
], ids=lambda value: type(value).__name__)
def test_unsafe_or_unsupported_document_never_returns_partial_pages(parser, source, code):
    with pytest.raises(parser.PdfProcessingError) as error:
        parser.parsePdf(source)
    assert error.value.code == code
    assert str(error.value) == code


def child_script(monkeypatch, parser, tmp_path, source):
    path = tmp_path / "child.py"
    path.write_text(source, encoding="utf-8")
    monkeypatch.setattr(parser, "_CHILD_PATH", path)


def test_wall_timeout_kills_and_reaps_actual_child(parser, monkeypatch, tmp_path):
    pidfile = tmp_path / "pid"
    child_script(monkeypatch, parser, tmp_path,
                 f"import os,time\nopen({str(pidfile)!r},'w').write(str(os.getpid()))\ntime.sleep(60)\n")
    monkeypatch.setattr(parser, "WALL_SECONDS", 0.5)
    started = time.monotonic()
    with pytest.raises(parser.PdfProcessingError, match="pdf_timeout"):
        parser.parsePdf(make_pdf())
    assert time.monotonic() - started < 5
    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)


def test_unbounded_child_output_is_stopped(parser, monkeypatch, tmp_path):
    child_script(monkeypatch, parser, tmp_path,
                 "import os\nwhile True: os.write(1,b'x'*65536)\n")
    with pytest.raises(parser.PdfProcessingError, match="pdf_limit_exceeded"):
        parser.parsePdf(make_pdf())


@pytest.mark.parametrize("result", [
    [], [{"page": 2, "text": "bad"}], [{"page": True, "text": "bad"}],
    [{"page": 1, "text": "\x00"}], [{"page": 1, "text": ""}],
    [{"page": 1, "text": "ok", "extra": True}],
    {"error": "secret exception details"},
])
def test_child_response_is_revalidated(parser, monkeypatch, tmp_path, result):
    child_script(monkeypatch, parser, tmp_path, f"print({json.dumps(result)!r})\n")
    with pytest.raises(parser.PdfProcessingError, match="invalid_pdf"):
        parser.parsePdf(make_pdf())


def test_child_does_not_inherit_secrets_or_open_files(parser, monkeypatch, tmp_path):
    monkeypatch.setenv("WORKER_DATABASE_URL", "must-not-leak")
    monkeypatch.setenv("APP_CSRF_SECRET", "must-not-leak")
    with open(tmp_path / "secret", "w") as secret:
        os.set_inheritable(secret.fileno(), True)
        child_script(monkeypatch, parser, tmp_path,
                     "import os,json\n"
                     f"fd={secret.fileno()}\n"
                     "try:\n os.fstat(fd); closed=False\n"
                     "except OSError:\n closed=True\n"
                     "assert closed and not os.environ.get('WORKER_DATABASE_URL') and not os.environ.get('APP_CSRF_SECRET')\n"
                     "print(json.dumps([{'page':1,'text':'safe'}]))\n")
        assert parser.parsePdf(make_pdf()) == [{"page": 1, "text": "safe"}]


def test_child_resource_and_network_restrictions_are_real(parser):
    script = ("import importlib.util,json,resource,socket\n"
              f"spec=importlib.util.spec_from_file_location('child',{str(Path(__file__).resolve().parents[1] / 'app/pdf_child.py')!r})\n"
              "child=importlib.util.module_from_spec(spec);spec.loader.exec_module(child)\n"
              "child._confine()\n"
              "assert resource.getrlimit(resource.RLIMIT_AS)==(536870912,536870912)\n"
              "assert resource.getrlimit(resource.RLIMIT_CPU)==(10,10)\n"
              "try:\n socket.socket(); raise AssertionError('network allowed')\n"
              "except PermissionError:\n pass\n"
              "print('confined')\n")
    result = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == b"confined\n"


def test_resource_killed_child_becomes_safe_failure(parser, monkeypatch, tmp_path):
    child_script(monkeypatch, parser, tmp_path,
                 "import os,signal\nos.kill(os.getpid(),signal.SIGKILL)\n")
    with pytest.raises(parser.PdfProcessingError, match="pdf_resource_limit"):
        parser.parsePdf(make_pdf())


def test_unsupported_platform_fails_closed(parser, monkeypatch):
    monkeypatch.setattr(parser.sys, "platform", "win32")
    with pytest.raises(parser.PdfProcessingError, match="pdf_parser_unavailable"):
        parser.parsePdf(make_pdf())
