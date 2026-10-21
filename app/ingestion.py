"""Bounded extraction; provenance uses Unicode offsets in cleaned page/paragraph text."""
import io
import json
import re
import subprocess
import sys
import tiktoken

MAX_FILE = 10 * 1024 * 1024
MAX_CHARS = 60000
ENCODING = tiktoken.get_encoding("cl100k_base")
PARSER = "pdfplumber-layout-guard-v1/txt-utf8-v1"


class ProcessingError(Exception):
    pass


def clean(text):
    # Preserve numerals, dates, units, punctuation and interior spacing.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def quality(text):
    if not text.strip() or "\ufffd" in text or "\x00" in text:
        raise ProcessingError("內容空白、編碼錯誤或文字品質不足")
    if any(ord(c) < 32 and c not in "\n\t" for c in text):
        raise ProcessingError("文件包含無法辨識的控制字元")
    if "(cid:" in text:
        raise ProcessingError("PDF 缺少可靠的字元對應")


def extract(body, media):
    if not 0 < len(body) <= MAX_FILE:
        raise ProcessingError("檔案大小必須介於 1 byte 與 10 MiB")
    locations = []
    if media == "text/plain":
        try:
            text = clean(body.decode("utf-8-sig"))
        except UnicodeDecodeError:
            raise ProcessingError("TXT 必須使用 UTF-8 編碼") from None
        quality(text)
        for number, paragraph in enumerate(re.split(r"\n\s*\n", text), 1):
            locations.append({"kind": "paragraph", "number": number, "text": paragraph})
    elif media == "application/pdf":
        if not body.startswith(b"%PDF-"):
            raise ProcessingError("檔案不是有效 PDF")
        import pdfplumber
        try:
            with pdfplumber.open(io.BytesIO(body), password=None) as pdf:
                if pdf.doc.encryption:
                    raise ProcessingError("不支援加密 PDF")
                if not 1 <= len(pdf.pages) <= 50:
                    raise ProcessingError("PDF 頁數上限為 50 頁")
                total = 0
                for number, page in enumerate(pdf.pages, 1):
                    if any(image.get("width", 0) * image.get("height", 0) > page.width * page.height * .15
                           for image in page.images):
                        raise ProcessingError("PDF 含大幅影像，請先提供可核對的文字版本")
                    if page.find_tables():
                        raise ProcessingError("表格 PDF 請先轉為逐筆欄位文字，避免欄位錯配")
                    rows = {}
                    for word in page.extract_words():
                        rows.setdefault(round(word["top"] / 4), []).append(word)
                    for row in rows.values():
                        row.sort(key=lambda word: word["x0"])
                        if any(right["x0"] - left["x1"] > max(70, page.width * .18)
                               for left, right in zip(row, row[1:])):
                            raise ProcessingError("PDF 多欄或閱讀順序不明，請轉為單欄文字")
                    text = clean(page.extract_text(x_tolerance=2, y_tolerance=3) or "")
                    quality(text)
                    total += len(text)
                    if total > MAX_CHARS:
                        raise ProcessingError("解析文字超過 60,000 字元")
                    locations.append({"kind": "page", "number": number, "text": text})
        except ProcessingError:
            raise
        except Exception:
            raise ProcessingError("PDF 損壞、加密或無法解析") from None
    else:
        raise ProcessingError("僅接受文字型 PDF 與 UTF-8 TXT")
    text = "\n\n".join(location["text"] for location in locations)
    if len(text) > MAX_CHARS or len(locations) > 1000:
        raise ProcessingError("文字或段落數超過處理上限")
    if len(ENCODING.encode(text, disallowed_special=())) > 24000:
        raise ProcessingError("文件超過 24,000 tokens")
    return {"content": text, "locations": locations, "parser": PARSER}


def extract_bounded(body, media):
    """Run without credentials, with CPU/memory/time limits on the Linux worker."""
    import os
    environment = {key: value for key, value in os.environ.items()
                   if key in {"PATH", "PYTHONPATH", "TIKTOKEN_CACHE_DIR", "LANG"}}
    try:
        result = subprocess.run([sys.executable, "-m", "app.ingestion", media],
                                input=body, capture_output=True, timeout=25, env=environment)
        if result.returncode:
            raise ProcessingError("解析逾時或資源超限")
        output = json.loads(result.stdout)
        if "error" in output:
            raise ProcessingError(output["error"])
        return output
    except (subprocess.TimeoutExpired, ValueError):
        raise ProcessingError("解析逾時或資料無效") from None


def chunk_locations(locations):
    result = []
    for location in locations:
        text = location["text"]
        tokens = ENCODING.encode(text, disallowed_special=())
        boundaries = [0]
        for token in tokens:
            boundaries.append(boundaries[-1] + len(ENCODING.decode_single_token_bytes(token)))
        raw = text.encode()
        start = 0
        while start < len(tokens):
            end = min(start + 600, len(tokens))
            while end > start:
                try:
                    fragment = raw[boundaries[start]:boundaries[end]].decode()
                    break
                except UnicodeDecodeError:
                    end -= 1
            if end <= start:
                raise ProcessingError("無法建立完整字元段落")
            if fragment.strip():
                offset = len(raw[:boundaries[start]].decode())
                result.append({"ordinal": len(result), "location_kind": location["kind"],
                    "location_no": location["number"], "start_offset": offset,
                    "end_offset": offset + len(fragment), "content": fragment, "tokens": end - start})
            if end == len(tokens):
                break
            start = max(start + 1, end - 60)
            while start < end:
                try:
                    raw[:boundaries[start]].decode()
                    break
                except UnicodeDecodeError:
                    start += 1
    if not 1 <= len(result) <= 64:
        raise ProcessingError("切塊數量超過 64 段")
    return result


if __name__ == "__main__":
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024**2, 768 * 1024**2))
    resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    try:
        print(json.dumps(extract(sys.stdin.buffer.read(MAX_FILE + 1), sys.argv[1]), ensure_ascii=False))
    except ProcessingError as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
