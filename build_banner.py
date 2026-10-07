"""Build the UIA roll-up banner PDF.

    python3 build_banner.py             -> print version: all type converted to
                                           vector outlines, no fonts in the PDF
    python3 build_banner.py --editable  -> design version: live, editable text
                                           with the fonts embedded

The logo is traced to vectors in both, and every element is a separate object.
"""
import os
import re
import sys

import numpy as np
import potrace
import uharfbuzz as hb
from fontTools.pens.basePen import BasePen
from fontTools.pens.boundsPen import BoundsPen
from fontTools.ttLib import TTFont
from fontTools.ttLib.removeOverlaps import removeOverlaps
from PIL import Image, ImageFilter
from reportlab.lib.colors import CMYKColor
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont as RLTTFont
from reportlab.pdfgen import canvas

HERE = os.path.dirname(os.path.abspath(__file__))
FONTS = os.path.join(HERE, "assets", "fonts")
LOGO_SRC = os.path.join(HERE, "assets", "source.webp")
EDITABLE = "--editable" in sys.argv
OUT = os.path.join(HERE, "UIA-Banner-85x200cm-%s.pdf"
                   % ("editable" if EDITABLE else "print"))

# Artwork is laid out in millimetres, origin top-left (the reference image is
# 830x2000 px, so 1 px ~ 1 mm; X is shifted 10 mm to fill the 850 mm width).
W, H = 850, 2000
BLEED = 5
DX = 10

NAVY = CMYKColor(0.95, 0.80, 0.45, 0.65)
TEAL = CMYKColor(0.80, 0.45, 0.32, 0.30)
GOLD = CMYKColor(0.08, 0.40, 0.92, 0.02)
WHITE = CMYKColor(0, 0, 0, 0)
RULE = CMYKColor(0.70, 0.55, 0.35, 0.35)
LABEL = CMYKColor(0.12, 0.08, 0.06, 0.0)

c = canvas.Canvas(OUT, pagesize=((W + 2 * BLEED) * mm, (H + 2 * BLEED) * mm))
c.setTitle("United International Academy - Floor 4 Banner (85x200 cm)")
c.setAuthor("United International Academy")
c.setSubject("Editable banner design, live text, CMYK" if EDITABLE else
             "Print-ready banner, text converted to outlines, CMYK")
c.setPageCompression(1)


def P(x, y):
    """Design mm (top-left origin) -> PDF points (bottom-left origin)."""
    return (x + DX + BLEED) * mm, (H - y + BLEED) * mm


class RLPen(BasePen):
    def __init__(self, glyphset, path, tf):
        super().__init__(glyphset)
        self.path, self.tf = path, tf

    def _moveTo(self, p):
        self.path.moveTo(*self.tf(p))

    def _lineTo(self, p):
        self.path.lineTo(*self.tf(p))

    def _curveToOne(self, a, b, d):
        self.path.curveTo(*self.tf(a), *self.tf(b), *self.tf(d))

    def _closePath(self):
        self.path.close()


class Font:
    def __init__(self, name):
        path = os.path.join(FONTS, name)
        self.tt = TTFont(path)
        removeOverlaps(self.tt)  # variable-font instances keep overlapping contours
        self.gs = self.tt.getGlyphSet()
        self.upm = self.tt["head"].unitsPerEm
        self.cap = self.tt["OS/2"].sCapHeight
        self.order = self.tt.getGlyphOrder()
        self.hbfont = hb.Font(hb.Face(hb.Blob.from_file_path(path)))
        self.name = os.path.splitext(name)[0]
        if EDITABLE:
            pdfmetrics.registerFont(RLTTFont(self.name, path))

    def shape(self, text):
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        # Live text is placed by advance widths only, so match that when editable.
        hb.shape(self.hbfont, buf, {"kern": not EDITABLE, "liga": not EDITABLE})
        return [(self.order[i.codepoint], p.x_advance, p.x_offset, p.y_offset)
                for i, p in zip(buf.glyph_infos, buf.glyph_positions)]

    def layout(self, text, tracking):
        """Glyph origins in font units plus ink extents (xmin, xmax)."""
        x, out, xmin, xmax = 0, [], None, None
        for g, adv, xo, yo in self.shape(text):
            bp = BoundsPen(self.gs)
            self.gs[g].draw(bp)
            if bp.bounds:
                l, r = x + xo + bp.bounds[0], x + xo + bp.bounds[2]
                xmin = l if xmin is None else min(xmin, l)
                xmax = r if xmax is None else max(xmax, r)
            out.append((g, x + xo, yo))
            x += adv + tracking
        return out, xmin, xmax


def text(font, s, *, baseline, color, x=None, cx=None, width=None,
         cap=None, size=None, track=0.0):
    """Draw `s` as outlines, or as live text in the editable build.

    Size comes from `cap` (cap height in mm) or `size` (em in mm) or `width`.
    With cap/size + width, letter tracking is solved to hit the ink width.
    Position: `cx` centres the ink, `x` puts the ink's left edge there.
    """
    if cap is not None:
        size = cap * font.upm / font.cap
    glyphs, l, r = font.layout(s, 0)
    n = len(glyphs)
    if size is None:
        size = width * font.upm / (r - l)
    elif width is not None and n > 1:
        track = (width * font.upm / size - (r - l)) / (n - 1)
    glyphs, l, r = font.layout(s, track)
    k = size / font.upm
    ink_w = (r - l) * k
    left = (cx - ink_w / 2) if cx is not None else x
    ox = left - l * k

    c.setFillColor(color)
    if EDITABLE:
        c.setFont(font.name, size * mm)
        c.drawString(*P(ox, baseline), s, charSpace=track * k * mm)
        return

    path = c.beginPath()
    for g, gx, gy in glyphs:
        def tf(p, gx=gx, gy=gy):
            return P(ox + (gx + p[0]) * k, baseline - (gy + p[1]) * k)
        font.gs[g].draw(RLPen(font.gs, path, tf))
    c.drawPath(path, stroke=0, fill=1, fillMode=0)  # nonzero winding


def rect(x0, y0, x1, y1, color):
    c.setFillColor(color)
    px, py = P(x0, y1)
    c.rect(px, py, (x1 - x0) * mm, (y1 - y0) * mm, stroke=0, fill=1)


# --- Background ----------------------------------------------------------------
c.setFillColor(NAVY)
c.rect(0, 0, (W + 2 * BLEED) * mm, (H + 2 * BLEED) * mm, stroke=0, fill=1)
rect(-DX - BLEED, 1135, W - DX + BLEED, 1605, TEAL)

# Thin frame around the top panel and the short divider rule
c.setStrokeColor(RULE)
c.setLineWidth(1.2 * mm)
x0, y0 = P(40, 1094)
c.rect(x0, y0, 750 * mm, 1054 * mm, stroke=1, fill=0)
c.setLineWidth(1.0 * mm)
c.line(*P(355, 609), *P(475, 609))


# --- Logo (traced from the artwork to vector paths) ------------------------------
def trace_logo():
    box = (285, 140, 545, 385)            # crop in reference px
    scale = 6
    im = Image.open(LOGO_SRC).convert("RGB").crop(box)
    im = im.resize((im.width * scale, im.height * scale), Image.LANCZOS)
    im = im.filter(ImageFilter.GaussianBlur(scale * 0.45))
    a = np.asarray(im).astype(int)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    masks = {
        GOLD: (r > 120) & (r - b > 70) & ~((r > 200) & (g > 200) & (b > 200)),
        WHITE: (r > 150) & (g > 150) & (b > 150) & (abs(r - b) < 60),
    }
    for color, m in masks.items():
        plist = potrace.Bitmap(~m).trace(turdsize=40, alphamax=1.0,
                                         opttolerance=0.2)

        def tf(pt):
            return P(box[0] + pt.x / scale, box[1] + pt.y / scale)

        path = c.beginPath()
        for curve in plist:
            path.moveTo(*tf(curve.start_point))
            for seg in curve:
                if seg.is_corner:
                    path.lineTo(*tf(seg.c))
                    path.lineTo(*tf(seg.end_point))
                else:
                    path.curveTo(*tf(seg.c1), *tf(seg.c2), *tf(seg.end_point))
            path.close()
        c.setFillColor(color)
        c.drawPath(path, stroke=0, fill=1, fillMode=1)  # even-odd keeps holes


trace_logo()

# --- Type ---------------------------------------------------------------------
exp_xb = Font("ArchivoExp-ExtraBold.ttf")
exp_b = Font("ArchivoExp-Bold.ttf")
exp_sb = Font("ArchivoExp-SemiBold.ttf")
sans_b = Font("Archivo-Bold.ttf")
serif = Font("Newsreader-Reg.ttf")
serif_it = Font("Newsreader-Italic.ttf")

CX = 415
text(exp_xb, "UNITED", baseline=457, cap=44, cx=CX, width=416, color=WHITE)
text(exp_sb, "INTERNATIONAL ACADEMY", baseline=487, cap=12, cx=CX, width=422,
     color=WHITE)
text(serif_it, "Shaping Global Minds.", baseline=546, cx=CX, width=312,
     color=LABEL)

text(serif, "International courses", baseline=697, cx=CX, width=462,
     color=WHITE)
text(serif, "& exam preparation", baseline=754, cx=CX, width=433, color=WHITE)

for label, line, lb, b, lw, w in [
    ("CURRICULUM", "AP • IB • IGCSE", 826, 876, 134, 328),
    ("UNIVERSITY ADMISSION", "SAT • ACT • EST II", 917, 967, 244, 383),
    ("ENGLISH LANGUAGE", "IELTS • TOEFL", 1008, 1058, 208, 313),
]:
    text(exp_sb, label, baseline=lb, cap=10, cx=CX, width=lw, color=LABEL)
    text(sans_b, line, baseline=b, cap=33, cx=CX, width=w, color=WHITE)

# Floor panel
text(exp_b, "FIND US ON", baseline=1274, cap=15, x=94, width=180, color=WHITE)
text(exp_xb, "FLOOR", baseline=1442, cap=44, x=97, width=287, color=WHITE)
four_cap = 168
text(serif, "4", baseline=1440, size=four_cap * serif.upm /
     serif.tt["OS/2"].sCapHeight * 0.96, x=408, color=GOLD)

# Up arrow
c.setFillColor(GOLD)
arrow = c.beginPath()
for i, (ax, ay) in enumerate([(662, 1301), (703, 1346), (672, 1346),
                              (672, 1432), (651, 1432), (651, 1346),
                              (621, 1346)]):
    (arrow.moveTo if i == 0 else arrow.lineTo)(*P(ax, ay))
arrow.close()
c.drawPath(arrow, stroke=0, fill=1)

# Footer
text(serif, "Visit us to explore your options.", baseline=1680, cx=CX,
     width=533, color=WHITE)
text(exp_sb, "PHONE / WHATSAPP", baseline=1739, cap=11, cx=CX, width=216,
     color=LABEL)
text(sans_b, "+962 79 055 5890", baseline=1805, cap=40, cx=CX, width=477,
     color=WHITE)

# Trim / bleed boxes so print RIPs know the finished size
c.setCropBox((0, 0, (W + 2 * BLEED) * mm, (H + 2 * BLEED) * mm))
c.setTrimBox((BLEED * mm, BLEED * mm, (W + BLEED) * mm, (H + BLEED) * mm))
c.setBleedBox((0, 0, (W + 2 * BLEED) * mm, (H + 2 * BLEED) * mm))
c.showPage()
c.save()

# ReportLab always registers Helvetica (as /F1) even when unused; drop it.
# In the print build that leaves zero font resources (all type is outlines).
import pymupdf  # noqa: E402

doc = pymupdf.open(OUT)
page = doc[0]
kind, val = doc.xref_get_key(page.xref, "Resources/Font")
if EDITABLE:
    fonts_xref = int(val.split()[0]) if kind == "xref" else None
    fonts = doc.xref_object(fonts_xref) if fonts_xref else val
    fonts = re.sub(r"/F1\s+\d+\s+0\s+R", "", fonts)
    if fonts_xref:
        doc.update_object(fonts_xref, fonts)
    else:
        doc.xref_set_key(page.xref, "Resources/Font", fonts)
else:
    doc.xref_set_key(page.xref, "Resources/Font", "null")
doc.save(OUT + ".tmp", garbage=4, deflate=True)
doc.close()
os.replace(OUT + ".tmp", OUT)
print("wrote", OUT)
