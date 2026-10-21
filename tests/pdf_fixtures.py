"""Synthetic PDFs with explicit text positions; no customer documents."""
from io import BytesIO

from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject, DecodedStreamObject, DictionaryObject, NameObject,
    NumberObject, TextStringObject,
)


def make_pdf(pages=("Page 1",), *, encrypted=False, rotated=False,
             two_columns=False, active=None, image_pages=()):
    writer = PdfWriter()
    unicodeText = any(ord(char) > 255 for text in pages for char in text)
    glyphs = {char: index for index, char in enumerate(sorted(set(''.join(pages))), 1)}
    font = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
        NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
    }))
    if unicodeText:
        cmap = DecodedStreamObject()
        entries = '\n'.join(f'<{index:04x}> <{char.encode("utf-16-be").hex()}>' for char, index in glyphs.items())
        cmap.set_data(('begincmap\n1 begincodespacerange\n<0000> <ffff>\nendcodespacerange\n'
                      f'{len(glyphs)} beginbfchar\n{entries}\nendbfchar\nendcmap').encode())
        descendant = writer._add_object(DictionaryObject({
            NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/CIDFontType0'),
            NameObject('/BaseFont'): NameObject('/HeiseiMin-W3'),
            NameObject('/CIDSystemInfo'): DictionaryObject({NameObject('/Registry'): TextStringObject('Adobe'),
                NameObject('/Ordering'): TextStringObject('Identity'), NameObject('/Supplement'): NumberObject(0)})}))
        font = writer._add_object(DictionaryObject({
            NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type0'),
            NameObject('/BaseFont'): NameObject('/HeiseiMin-W3'), NameObject('/Encoding'): NameObject('/Identity-H'),
            NameObject('/DescendantFonts'): ArrayObject([descendant]),
            NameObject('/ToUnicode'): writer._add_object(cmap)}))
    for index, text in enumerate(pages):
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})
        })
        commands = []
        for line_index, line in enumerate(text.split("\n")):
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            operand = '<' + ''.join(f'{glyphs[char]:04x}' for char in line) + '>' if unicodeText else f'({escaped})'
            commands.append(f"BT /F1 12 Tf 1 0 0 1 50 {740 - 18 * line_index} Tm {operand} Tj ET")
        if two_columns:
            commands.append("BT /F1 12 Tf 1 0 0 1 340 740 Tm (Column two) Tj ET")
        if index in image_pages:
            image = DecodedStreamObject()
            image.set_data(b"\x00")
            image.update({NameObject("/Type"): NameObject("/XObject"),
                          NameObject("/Subtype"): NameObject("/Image"),
                          NameObject("/Width"): NumberObject(1),
                          NameObject("/Height"): NumberObject(1),
                          NameObject("/ColorSpace"): NameObject("/DeviceGray"),
                          NameObject("/BitsPerComponent"): NumberObject(8)})
            page["/Resources"][NameObject("/XObject")] = DictionaryObject({
                NameObject("/Im1"): writer._add_object(image)})
            commands.append("q 100 0 0 100 50 500 cm /Im1 Do Q")
        stream = DecodedStreamObject()
        stream.set_data("\n".join(commands).encode("cp1252"))
        page[NameObject("/Contents")] = writer._add_object(stream)
        if rotated:
            page.rotate(90)
    if active == "action":
        writer._root_object[NameObject('/OpenAction')] = DictionaryObject({
            NameObject('/S'): NameObject('/JavaScript'), NameObject('/JS'): TextStringObject("app.alert('synthetic');")})
    elif active == "form":
        writer._root_object[NameObject("/AcroForm")] = DictionaryObject({
            NameObject("/Fields"): ArrayObject()})
    elif active == "attachment":
        writer.add_attachment("synthetic.txt", b"synthetic")
    if encrypted:
        writer.encrypt("synthetic-password")
    output = BytesIO()
    writer.write(output)
    return output.getvalue()
