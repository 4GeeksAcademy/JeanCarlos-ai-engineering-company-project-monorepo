"""Write a simple text PDF (no extra dependency). Used to build the synthetic test RFP that
carries fake patient data (BRIEFING Hito 9 §4 case 4) in tests/fixtures/rfp/.

    python scripts/make_test_pdf.py <out.pdf> <text-file>
"""

import sys
import textwrap


def _escape(line: str) -> str:
    return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(lines: list[str]) -> bytes:
    wrapped: list[str] = []
    for line in lines:
        wrapped.extend(textwrap.wrap(line, 95) or [""])
    pages = [wrapped[i:i + 52] for i in range(0, len(wrapped), 52)] or [[]]
    objects: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>", b""]  # pages object filled later
    font_id = 3
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    kids = []
    for page_lines in pages:
        stream = "BT /F1 10 Tf 13 TL 50 790 Td " + " ".join(f"({_escape(l)}) '" for l in page_lines) + " ET"
        data = stream.encode("cp1252", "replace")
        objects.append(b"<< /Length %d >>\nstream\n" % len(data) + data + b"\nendstream")
        content_id = len(objects)
        objects.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Resources << /Font << /F1 %d 0 R >> >> "
                       b"/Contents %d 0 R >>" % (font_id, content_id))
        kids.append(len(objects))
    objects[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (" ".join(f"{k} 0 R" for k in kids).encode(), len(kids))
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


if __name__ == "__main__":
    out, source = sys.argv[1], sys.argv[2]
    with open(source, encoding="utf-8") as f:
        lines = f.read().splitlines()
    with open(out, "wb") as f:
        f.write(make_pdf(lines))
