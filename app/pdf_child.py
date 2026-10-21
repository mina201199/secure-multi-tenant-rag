"""只讀取 stdin PDF 並輸出固定 JSON 契約；不得連線或持有 DB 憑證。"""
import ctypes
import errno
from io import BytesIO
import json
import logging
import math
import os
import platform
import resource
import signal
import sys


def _confine():
  # 先設硬限制，再載入解析器；父程序死亡時一併結束，避免孤兒工作。
  parent = os.getppid()
  libc = ctypes.CDLL(None, use_errno=True)
  if parent != int(os.environ.get('PDF_PARENT_PID', parent)) or libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != parent:
    raise RuntimeError('containment unavailable')
  for name, maximum in ((resource.RLIMIT_AS, 512 * 1024 * 1024), (resource.RLIMIT_CPU, 10),
      (resource.RLIMIT_CORE, 0), (resource.RLIMIT_FSIZE, 0), (resource.RLIMIT_NOFILE, 32)):
    resource.setrlimit(name, (maximum, maximum))
  # 限制網路與另開程序；固定架構清單以外拒絕執行，避免錯用 syscall 編號。
  if platform.machine() == 'x86_64':
    arch, forbidden = 0xc000003e, [41, 42, 43, 49, 50, 53, 56, 57, 58, 59, 101, 272, 288, 308, 310, 311, 322, 435]
  elif platform.machine() == 'aarch64':
    arch, forbidden = 0xc00000b7, [97, 117, 198, 199, 200, 201, 202, 203, 220, 221, 242, 268, 270, 271, 281, 435]
  else:
    raise RuntimeError('unsupported architecture')

  class Filter(ctypes.Structure):
    _fields_ = [('code', ctypes.c_ushort), ('jt', ctypes.c_ubyte), ('jf', ctypes.c_ubyte), ('k', ctypes.c_uint)]

  class Program(ctypes.Structure):
    _fields_ = [('length', ctypes.c_ushort), ('filter', ctypes.POINTER(Filter))]

  filters = [Filter(0x20, 0, 0, 4), Filter(0x15, 1, 0, arch), Filter(0x06, 0, 0, 0x80000000),
    Filter(0x20, 0, 0, 0)]
  # 拒絕 x32 ABI；同架構不同編號不能繞過黑名單。
  filters += [Filter(0x35, 0, 1, 0x40000000), Filter(0x06, 0, 0, 0x80000000)]
  for syscall in forbidden:
    filters += [Filter(0x15, 0, 1, syscall), Filter(0x06, 0, 0, 0x00050000 | errno.EPERM)]
  filters.append(Filter(0x06, 0, 0, 0x7fff0000))
  array = (Filter * len(filters))(*filters)
  program = Program(len(filters), array)
  if libc.prctl(38, 1, 0, 0, 0) != 0 or libc.prctl(22, 2, ctypes.byref(program), 0, 0) != 0:
    raise RuntimeError('containment unavailable')


class Rejected(Exception):
  pass


def extract(source):
  from pypdf import PdfReader
  from pypdf.generic import ContentStream
  # 固定版本的字型 metrics；版本更新必須重跑幾何／CMap 案例。
  from pypdf.generic._font import Font
  logging.disable(logging.CRITICAL)
  reader = PdfReader(BytesIO(source), strict=True)
  if reader.is_encrypted:
    raise Rejected('encrypted_pdf')
  root = reader.trailer['/Root']
  if any(key in root for key in ('/OpenAction', '/AA', '/AcroForm', '/Names', '/OCProperties')):
    raise Rejected('unsupported_pdf')
  if not 1 <= len(reader.pages) <= 20:
    raise Rejected('pdf_limit_exceeded')
  pages = []
  count = 2 * (len(reader.pages) - 1)
  for pageNumber, page in enumerate(reader.pages, 1):
    if page.rotation or any(key in page for key in ('/Annots', '/AA')):
      raise Rejected('unsupported_pdf')
    resources = page.get('/Resources', {})
    if hasattr(resources, 'get_object'):
      resources = resources.get_object()
    if any(key in resources for key in ('/XObject', '/ExtGState', '/Pattern', '/Shading')):
      raise Rejected('unsupported_pdf')
    content = page.get_contents()
    if content is None:
      raise Rejected('unreadable_page')
    # 圖形、裁切、隱藏文字、定位調整過的表格皆不在此保守文字格式範圍。
    allowed = {b'BT', b'ET', b'Tf', b'Tm', b'Td', b'TD', b'T*', b'TL', b'Tj', b'TJ',
      b'q', b'Q', b'cm', b'rg', b'g', b'RG', b'G', b'Tc', b'Tw', b'Tz'}
    textShows = 0
    activeText = False
    shownInText = False
    for operands, operator in ContentStream(content, reader).operations:
      if operator not in allowed:
        raise Rejected('unsupported_pdf')
      if operator in (b'g', b'rg', b'G', b'RG') and any(float(v) != 0 for v in operands):
        raise Rejected('unsupported_pdf')
      if operator in (b'Tc', b'Tw') and float(operands[0]) != 0:
        raise Rejected('unsupported_pdf')
      if operator == b'Tz' and float(operands[0]) != 100:
        raise Rejected('unsupported_pdf')
      if operator == b'BT':
        if activeText:
          raise Rejected('unsupported_pdf')
        activeText, shownInText = True, False
      elif operator == b'ET':
        if not activeText:
          raise Rejected('unsupported_pdf')
        activeText = False
      elif operator in (b'Tj', b'TJ'):
        # visitor 會合併多次繪字；每個文字物件限一次，避免位移被隱藏。
        if not activeText or shownInText:
          raise Rejected('unsupported_pdf')
        shownInText = True
        textShows += 1
      if operator == b'TJ' and any(isinstance(v, (int, float)) and float(v) != 0 for v in operands[0]):
        raise Rejected('unsupported_pdf')
    if activeText:
      raise Rejected('unsupported_pdf')
    spans = []
    metrics = {}
    visible = (max(float(page.mediabox.left), float(page.cropbox.left)),
      max(float(page.mediabox.bottom), float(page.cropbox.bottom)),
      min(float(page.mediabox.right), float(page.cropbox.right)),
      min(float(page.mediabox.top), float(page.cropbox.top)))

    def visit(text, cm, tm, font, size):
      if not text.strip():
        return
      if len(text) + count > 200000:
        raise Rejected('pdf_limit_exceeded')
      if font is None or not all(math.isfinite(float(v)) for v in [*cm, *tm, size]) or size <= 0:
        raise Rejected('unsupported_pdf')
      if abs(cm[1]) > 0.001 or abs(cm[2]) > 0.001 or abs(tm[1]) > 0.001 or abs(tm[2]) > 0.001 or cm[0] <= 0 or cm[3] <= 0 or tm[0] <= 0 or tm[3] <= 0:
        raise Rejected('unsupported_pdf')
      x, y = tm[4] * cm[0] + cm[4], tm[5] * cm[3] + cm[5]
      line = text.rstrip('\r\n')
      if '\n' in line or '\r' in line or font.get('/Subtype') not in ('/Type1', '/TrueType', '/Type0'):
        raise Rejected('unsupported_pdf')
      if id(font) not in metrics:
        metrics[id(font)] = Font.from_font_resource(font)
      metric = metrics[id(font)]
      widths = [float(value) for value in metric.character_widths.values()]
      if not metric.interpretable or not widths or any(not math.isfinite(w) or w < 0 for w in widths):
        raise Rejected('unsupported_pdf')
      # 使用最大 advance 的上界，避免 Unicode→CID 映射缺漏低估字寬；
      # 至少一個 em，刻意拒絕可能超出頁面的長行。
      advance = max(1000, *widths) / 1000
      bbox = metric.font_descriptor.bbox
      bounds = [float(v) for v in bbox]
      if len(bounds) != 4 or not all(math.isfinite(v) for v in bounds):
        raise Rejected('unsupported_pdf')
      sx, sy = size * tm[0] * cm[0], size * tm[3] * cm[3]
      left = x + min(0, bounds[0] / 1000) * sx
      right = x + (len(line) * advance + max(0, bounds[2] / 1000 - advance)) * sx
      bottom = y + min(-0.25, bounds[1] / 1000) * sy
      top = y + max(1, bounds[3] / 1000) * sy
      if not (visible[0] <= left and right <= visible[2] and visible[1] <= bottom and top <= visible[3]):
        raise Rejected('unsupported_pdf')
      spans.append((x, bottom, top))

    text = page.extract_text(extraction_mode='plain', visitor_text=visit).rstrip('\r\n')
    count += len(text)
    if count > 200000:
      raise Rejected('pdf_limit_exceeded')
    if not text.strip() or any((ord(c) < 32 and c not in '\t\n\r') or 127 <= ord(c) <= 159
        or c == '\ufffd' or 0xd800 <= ord(c) <= 0xdfff for c in text):
      raise Rejected('unreadable_page')
    if not spans:
      raise Rejected('unreadable_page')
    if len(spans) != textShows:
      raise Rejected('unsupported_pdf')
    firstX, previousBottom = spans[0][0], math.inf
    for x, bottom, top in spans:
      if abs(x - firstX) > 2 or top >= previousBottom:
        raise Rejected('unsupported_pdf')
      previousBottom = bottom
    pages.append({'page': pageNumber, 'text': text})
  return pages


def main():
  try:
    _confine()
  except Exception:
    print(json.dumps({'error': 'pdf_parser_unavailable'}))
    return 2
  try:
    source = sys.stdin.buffer.read(2097153)
    if len(source) > 2097152:
      raise Rejected('pdf_limit_exceeded')
    if not source.startswith(b'%PDF-'):
      raise Rejected('invalid_pdf')
    result = extract(source)
    print(json.dumps(result, ensure_ascii=False))
    return 0
  except Rejected as error:
    code = str(error)
  except MemoryError:
    code = 'pdf_resource_limit'
  except Exception:
    code = 'invalid_pdf'
  print(json.dumps({'error': code}))
  return 2


if __name__ == '__main__':
  raise SystemExit(main())
