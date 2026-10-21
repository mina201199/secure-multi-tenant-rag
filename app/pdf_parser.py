"""PDF 子程序邊界：限制輸入／輸出與等待時間，不能繼承 worker 憑證。"""
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time

MAX_BYTES = 2 * 1024 * 1024
MAX_CHARS = 200000
MAX_OUTPUT_BYTES = 1300000
WALL_SECONDS = 15
_CHILD_PATH = Path(__file__).with_name('pdf_child.py')
ERROR_CODES = frozenset({'invalid_pdf', 'encrypted_pdf', 'unreadable_page', 'unsupported_pdf',
  'pdf_limit_exceeded', 'pdf_timeout', 'pdf_resource_limit', 'pdf_parser_unavailable'})


class PdfProcessingError(ValueError):
  def __init__(self, code):
    self.code = code if code in ERROR_CODES else 'invalid_pdf'
    super().__init__(self.code)


def validatePages(pages):
  if not isinstance(pages, list) or not 1 <= len(pages) <= 20:
    raise PdfProcessingError('invalid_pdf')
  total = 2 * (len(pages) - 1)
  for number, page in enumerate(pages, 1):
    if (not isinstance(page, dict) or set(page) != {'page', 'text'}
        or type(page['page']) is not int or page['page'] != number
        or not isinstance(page['text'], str) or not page['text'].strip()):
      raise PdfProcessingError('invalid_pdf')
    text = page['text']
    if any((ord(c) < 32 and c not in '\t\n\r') or 127 <= ord(c) <= 159
           or c == '\ufffd' or 0xd800 <= ord(c) <= 0xdfff for c in text):
      raise PdfProcessingError('invalid_pdf')
    total += len(text)
    if total > MAX_CHARS:
      raise PdfProcessingError('pdf_limit_exceeded')
  return pages


def parsePdf(sourceBytes):
  if sys.platform != 'linux':
    raise PdfProcessingError('pdf_parser_unavailable')
  if len(sourceBytes) > MAX_BYTES:
    raise PdfProcessingError('pdf_limit_exceeded')
  if not sourceBytes.startswith(b'%PDF-'):
    raise PdfProcessingError('invalid_pdf')
  process = None
  try:
    process = subprocess.Popen([sys.executable, '-I', '-B', str(_CHILD_PATH)],
      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
      env={'LANG': 'C.UTF-8', 'PDF_PARENT_PID': str(os.getpid())}, cwd='/', close_fds=True, start_new_session=True)
    output = bytearray()
    offset = 0
    deadline = time.monotonic() + WALL_SECONDS
    with selectors.DefaultSelector() as selector:
      os.set_blocking(process.stdin.fileno(), False)
      os.set_blocking(process.stdout.fileno(), False)
      selector.register(process.stdin, selectors.EVENT_WRITE)
      selector.register(process.stdout, selectors.EVENT_READ)
      while selector.get_map():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
          raise PdfProcessingError('pdf_timeout')
        for key, _ in selector.select(min(remaining, 0.1)):
          if key.fileobj is process.stdin:
            try:
              offset += os.write(process.stdin.fileno(), sourceBytes[offset:offset + 65536])
            except BrokenPipeError:
              offset = len(sourceBytes)
            if offset == len(sourceBytes):
              selector.unregister(process.stdin)
              process.stdin.close()
          else:
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
              selector.unregister(process.stdout)
            output.extend(chunk)
            if len(output) > MAX_OUTPUT_BYTES:
              raise PdfProcessingError('pdf_limit_exceeded')
    try:
      returnCode = process.wait(timeout=max(0.001, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
      raise PdfProcessingError('pdf_timeout') from None
    if returnCode < 0:
      raise PdfProcessingError('pdf_resource_limit')
    try:
      result = json.loads(output)
    except (ValueError, UnicodeError):
      raise PdfProcessingError('invalid_pdf') from None
    if isinstance(result, dict) and set(result) == {'error'} and isinstance(result['error'], str) and result['error'] in ERROR_CODES:
      raise PdfProcessingError(result['error'])
    if returnCode != 0:
      raise PdfProcessingError('invalid_pdf')
    return validatePages(result)
  except OSError:
    raise PdfProcessingError('pdf_parser_unavailable') from None
  finally:
    if process is not None:
      if process.poll() is None:
        try:
          os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
          pass
      process.wait()
      process.stdin.close()
      process.stdout.close()
