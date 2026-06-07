"""Generate realistic SYNTHETIC demo bills-of-materials (Stücklisten) for demos.

100% mock data — no customer/ZF content. Cell values are drawn from the real
Schaufler master-data vocabulary so the mapping system scores them as intended:

  GREEN  -> exact catalog matches / cleanly coercible values
  YELLOW -> fuzzy / free-text / combined / slightly-off values
  RED    -> contradictory / garbage / missing-required values

Three documents with THREE DISTINCT visual identities (so they don't look
templated), and the two messy ones are messy in DIFFERENT ways:

  1. demo_clean_de_en.pdf  Modern ERP export, blue banner, zebra   75/20/5  (20 p)
  2. demo_messy_fr.pdf     Hand-maintained Excel, landscape, the    60/35/5
                           customer's own colour highlights, merged
                           section rows, footnotes, totals, comma
                           decimals, Excel print footer.
  3. demo_messy_zh.pdf     Old controlled-copy fax/scan: monochrome,
                           Courier digits, heavy uneven grid,        60/35/5
                           diagonal watermark, stamp, scan grain.

Run:  python scripts/make_demo_boms.py
Out:  data/demo/*.pdf  +  data/demo/answer_key.md
"""

from __future__ import annotations

import random
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageTemplate,
    Paragraph,
    Table,
    TableStyle,
)

pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))

OUTDIR = Path(__file__).resolve().parents[1] / "data" / "demo"
OUTDIR.mkdir(parents=True, exist_ok=True)

INK = colors.HexColor("#15202b")

# ===========================================================================
# Vocabulary grounded in config/master_data (these are the GREEN-eligible values)
# ===========================================================================
GREEN_MAT = ["1.2343", "1.2344", "1.2312", "1.2738", "1.2311", "1.2709", "1.2767",
             "1.2085", "1.0570", "1.4404", "1.4305", "AlSi9Cu3", "Cu", "Dievar",
             "Orvar Supreme", "1.2343 ESU", "H13", "P20", "X38CrMoV5-1", "SKD61",
             "1.2344 ESU"]
YELLOW_MAT = ["Toolox 44", "Stavax ESR", "Ramax HH", "Corrax", "Vidar Superior",
              "Böhler W302", "CuCoBe", "AMPCO 940", "1.2343 + nitriert",
              "Werkstoff lt. Zeichnung", "GGG-40", "Hartmetall K20"]
RED_MAT = ["?", "k.A.", "n.b.", "---", "siehe Norm"]

GREEN_HARD = ["46-48 HRC", "48-50 HRC", "50-52 HRC", "44-46 HRC", "42-44 HRC",
              "30-32 HRC", "28-30 HRC", "52-54 HRC"]
YELLOW_HARD = ["ca. 46 HRC", "~30 HRC", "52±2 HRC", "gehärtet", "vergütet", "58 HRC"]
RED_HARD = ["hart", "?", "-", "siehe Z."]

GREEN_PG = ["D2", "F", "U", "E", "K1", "K2", "N", "S", "B", "BB", "A", "H"]
YELLOW_PG = ["Kern", "Schieber", "Normteil", "Platte", "div."]
RED_PG = ["?", "x"]

GREEN_COAT = ["PVD", "CrN", "TiN", "TiAlN", "DLC", "BALINIT ALCRONA PRO", "Duplex Tigral"]
YELLOW_COAT = ["Hartverchromt", "beschichtet", "auf Anfrage", "nitriert + PVD"]

GREEN_NITTYPE = ["Plasmanitrieren", "Gasnitrieren", "Salzbadnitrieren", "Einsatzhärten"]
YELLOW_NITTYPE = ["Nitrieren", "Tenifer", "QPQ", "lt. Vorschrift"]

PART_DE = ["Formeinsatz Auswerferseite", "Formeinsatz Düsenseite", "Schieber links",
           "Schieber rechts", "Kernzug", "Auswerferstift", "Auswerferhülse",
           "Führungsbuchse", "Führungssäule", "Angussbuchse", "Heißkanaldüse",
           "Kühleinsatz", "Zentrierring", "Druckplatte", "Aufspannplatte",
           "Stützleiste", "Konturkern", "Backenführung", "Distanzleiste",
           "Halteplatte", "Isolierplatte", "Anschlagbolzen", "Rückdrückstift",
           "Verschlussschraube", "Temperieranschluss", "Schieberkeil",
           "Formrahmen Oberteil", "Formrahmen Unterteil", "Konturplatte",
           "Düsenplatte", "Klemmplatte", "Auswerferplatte", "Zwischenplatte"]
PART_FR = ["Insert moule côté éjection", "Insert moule côté injection", "Tiroir gauche",
           "Tiroir droit", "Noyau mobile", "Broche d'éjection", "Douille d'éjection",
           "Douille de guidage", "Colonne de guidage", "Buse de carotte",
           "Buse canal chaud", "Insert de refroidissement", "Bague de centrage",
           "Plaque de pression", "Plaque de bridage", "Réglette d'appui",
           "Noyau de forme", "Guidage de tiroir", "Réglette entretoise",
           "Plaque de maintien", "Plaque isolante", "Vis de fermeture",
           "Cadre de moule supérieur", "Cadre de moule inférieur", "Plaque de buse"]
PART_ZH = ["顶出侧镶件", "注射侧镶件", "左滑块", "右滑块", "抽芯", "顶针", "顶管",
           "导套", "导柱", "浇口套", "热流道喷嘴", "冷却镶件", "定位圈", "压板",
           "夹板", "支撑条", "型芯", "滑块导向", "垫条", "固定板", "隔热板",
           "锁紧螺钉", "上模框", "下模框", "喷嘴板", "型腔镶件", "复位杆"]
DESC_YELLOW_DE = ["Bauteil lt. Zeichnung", "Sonderteil", "diverse Kleinteile",
                  "Pos. teilweise entfällt", "Baugruppe kompl."]
DESC_RED = ["XXX", "###", "?", "n.a."]
MFR = ["Böhler", "DEW", "Buderus", "Meusburger", "Hasco", "DME", "Eifeler", "Oerlikon"]
OBS_FR = ["", "", "à confirmer", "voir plan ind. C", "urgent", "matière client",
          "cf. mail 12/03", "contrôle 100%", ""]


def patt_partno(rng):
    return f"{rng.choice(['A','B','Z','ST','FE','KZ'])}-{rng.randint(1000,9999)}-{rng.randint(0,99):02d}"


def gdim(rng):
    return rng.choice([f"{rng.randint(20,1200)}", f"{rng.randint(20,1200)}.0",
                       f"{rng.randint(20,400)}.{rng.choice([0,5])}"])


def gdim_combo(rng):
    return f"{rng.randint(40,800)} x {rng.randint(40,600)} x {rng.randint(10,200)}"


def gen(concept, cls, rng, lang="de"):
    if concept == "partno":
        if cls == "green":
            return patt_partno(rng)
        if cls == "yellow":
            return patt_partno(rng) + rng.choice([" (Rev. B)", " *", " /1"])
        return rng.choice(["", "?", "—"])
    if concept == "desc":
        pool = {"de": PART_DE, "fr": PART_FR, "zh": PART_ZH}[lang]
        if cls == "green":
            return rng.choice(pool)
        if cls == "yellow":
            if lang == "fr":
                return rng.choice(["Pièce selon plan", "Sous-ensemble compl.",
                                   "Petites pièces diverses", "Pièce spéciale"])
            if lang == "zh":
                return rng.choice(["按图纸", "特殊件", "组件", "零散小件"])
            return rng.choice(DESC_YELLOW_DE)
        return rng.choice(DESC_RED)
    if concept == "qty":
        if cls == "green":
            return str(rng.randint(1, 8))
        if cls == "yellow":
            return rng.choice(["2-4", "Satz", "n. Bedarf", "1 Satz"])
        return rng.choice(["-", "?", "0"])
    if concept == "material":
        if cls == "green":
            return rng.choice(GREEN_MAT)
        if cls == "yellow":
            return rng.choice(YELLOW_MAT)
        return rng.choice(RED_MAT)
    if concept in ("dimx", "dimy", "dimz"):
        if cls == "green":
            return gdim(rng)
        if cls == "yellow":
            return rng.choice([f"{rng.randint(20,400)} ±0,1", f"Ø{rng.randint(8,120)}",
                               f"{rng.randint(20,400)},{rng.randint(0,9)}"])
        return rng.choice(["?", "n. Z.", "-"])
    if concept == "dimcombo":
        if cls == "green":
            return gdim_combo(rng)
        if cls == "yellow":
            return rng.choice([f"Ø{rng.randint(20,150)} x {rng.randint(40,300)}",
                               gdim_combo(rng) + " ±0,1", f"L={rng.randint(100,600)}"])
        return rng.choice(["n. Zeichnung", "?", "selon plan", "按图"])
    if concept == "hardness":
        if cls == "green":
            return rng.choice(GREEN_HARD)
        if cls == "yellow":
            return rng.choice(YELLOW_HARD)
        return rng.choice(RED_HARD)
    if concept == "nittype":
        if cls == "green":
            return rng.choice(GREEN_NITTYPE)
        if cls == "yellow":
            return rng.choice(YELLOW_NITTYPE)
        return rng.choice(["?", "ja"])
    if concept == "coating":
        if cls == "green":
            return rng.choice(GREEN_COAT)
        if cls == "yellow":
            return rng.choice(YELLOW_COAT)
        return rng.choice(["?", "x"])
    if concept == "partsgroup":
        if cls == "green":
            return rng.choice(GREEN_PG)
        if cls == "yellow":
            return rng.choice(YELLOW_PG)
        return rng.choice(RED_PG)
    if concept == "mfr":
        if cls == "green":
            return rng.choice(MFR)
        if cls == "yellow":
            return rng.choice(["n. Wahl", "freigegeben", "div."])
        return "?"
    return ""


def weighted_class(rng, w):
    r = rng.random()
    if r < w["green"]:
        return "green"
    if r < w["green"] + w["yellow"]:
        return "yellow"
    return "red"


# concepts that are decorative / not scored
DECORATIVE = {"pos", "obs"}


def gen_rows(cols, weights, n_rows, lang, rng, counts):
    """Return list of rows; each row is list of dicts {text, cls, concept}."""
    rows = []
    pos = 1000
    for _ in range(n_rows):
        row = []
        for (concept, _h, _w, _align) in cols:
            if concept == "pos":
                row.append({"text": str(pos), "cls": "pos", "concept": concept})
                continue
            if concept == "obs":
                row.append({"text": rng.choice(OBS_FR), "cls": "obs", "concept": concept})
                continue
            cls = weighted_class(rng, weights)
            txt = gen(concept, cls, rng, lang=lang) or ""
            if txt.strip():
                counts[cls] += 1
            row.append({"text": txt, "cls": cls, "concept": concept})
        rows.append(row)
        pos += rng.choice([10, 10, 10, 20])
    return rows


# ===========================================================================
# Numbered canvas (draws "x / y" once total page count is known)
# ===========================================================================
def numbered_canvas(label, font, color, mono=False):
    class _C(canvas.Canvas):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self._saved = []

        def showPage(self):
            self._saved.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved)
            for st in self._saved:
                self.__dict__.update(st)
                w, h = self._pagesize
                self.setFont(font, 7)
                self.setFillColor(color)
                self.drawRightString(w - 1.3 * cm, 0.75 * cm,
                                     f"{label} {self._pageNumber} / {total}")
                super().showPage()
            super().save()
    return _C


# ===========================================================================
# DOC 1 — Modern ERP export (clean, blue banner, zebra)
# ===========================================================================
COLS_CLEAN = [
    ("pos", "Pos.", 1.0, "L"), ("partno", "Teile-Nr. / Part No.", 2.7, "L"),
    ("desc", "Benennung / Description", 4.6, "L"), ("qty", "Menge", 1.0, "R"),
    ("material", "Werkstoff / Material", 2.6, "L"), ("dimx", "Maß X", 1.5, "R"),
    ("dimy", "Maß Y", 1.5, "R"), ("dimz", "Maß Z", 1.4, "R"),
    ("hardness", "Härte / Hardness", 2.2, "L"), ("nittype", "Wärmebehandlung", 2.6, "L"),
    ("coating", "Beschichtung", 2.2, "L"), ("partsgroup", "Tgr.", 1.0, "L"),
]


def render_clean(rows, cols):
    ACC = colors.HexColor("#1f3a5f")
    SHADE = colors.HexColor("#eef2f6")
    RULE = colors.HexColor("#9aa5b1")
    cell = ParagraphStyle("c", fontName="Helvetica", fontSize=7.6, leading=9.4, textColor=INK)
    cellr = ParagraphStyle("cr", parent=cell, alignment=2)
    headc = ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=7.6, leading=9.4,
                           textColor=colors.white)
    data = [[Paragraph(c[1], headc) for c in cols]]
    for row in rows:
        line = []
        for i, celld in enumerate(row):
            st = cellr if cols[i][3] == "R" else cell
            line.append(Paragraph(celld["text"] or "&nbsp;", st))
        data.append(line)
    t = Table(data, colWidths=[c[2] * cm for c in cols], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), ACC),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, SHADE]),
        ("GRID", (0, 1), (-1, -1), 0.4, RULE),
        ("LINEBELOW", (0, 0), (-1, 0), 1.0, ACC),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 2.4), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.4),
    ]))

    def onpage(cv, doc):
        w, h = A4
        cv.saveState()
        cv.setFillColor(ACC)
        cv.rect(1.3 * cm, h - 2.35 * cm, w - 2.6 * cm, 1.15 * cm, fill=1, stroke=0)
        # logo box
        cv.setFillColor(colors.HexColor("#3d6aa3"))
        cv.rect(1.5 * cm, h - 2.22 * cm, 0.9 * cm, 0.9 * cm, fill=1, stroke=0)
        cv.setFillColor(colors.white)
        cv.setFont("Helvetica-Bold", 13)
        cv.drawCentredString(1.95 * cm, h - 1.95 * cm, "MG")
        cv.setFont("Helvetica-Bold", 12)
        cv.drawString(2.7 * cm, h - 1.78 * cm, "STÜCKLISTE — Druckgusswerkzeug / Bill of Materials")
        cv.setFont("Helvetica", 7)
        cv.drawString(2.7 * cm, h - 2.12 * cm,
                      "Auftrag DEMO-2026-0815   ·   Kunde: MUSTER GmbH (Demo)   ·   Werkzeug DGW-4127")
        # right meta box
        bx = w - 5.6 * cm
        cv.setStrokeColor(colors.white)
        cv.setFont("Helvetica", 6.6)
        for i, (k, v) in enumerate([("Zeichn.-Nr.", "Z-4127-000"), ("Rev.", "C"),
                                    ("Datum", "2026-06-04")]):
            cv.drawString(bx, h - 1.7 * cm - i * 0.3 * cm, f"{k}: {v}")
        cv.setStrokeColor(RULE)
        cv.line(1.3 * cm, 1.15 * cm, w - 1.3 * cm, 1.15 * cm)
        cv.setFont("Helvetica", 6.6)
        cv.setFillColor(colors.HexColor("#33475b"))
        cv.drawString(1.3 * cm, 0.75 * cm, "Synthetische Demodaten — keine realen Kundendaten")
        cv.restoreState()

    return _build("demo_clean_de_en.pdf", A4, t, onpage, 3.0 * cm,
                  numbered_canvas("Seite", "Helvetica", colors.HexColor("#33475b")))


# ===========================================================================
# DOC 2 — Hand-maintained Excel (landscape, customer highlights, footnotes)
# ===========================================================================
COLS_FR = [
    ("pos", "N°", 0.9, "L"), ("partno", "Référence", 2.6, "L"),
    ("desc", "Désignation", 5.2, "L"), ("qty", "Qté", 0.8, "R"),
    ("material", "Matière", 2.7, "L"), ("dimx", "Long.", 1.4, "R"),
    ("dimy", "Larg.", 1.4, "R"), ("dimz", "Haut.", 1.3, "R"),
    ("hardness", "Dureté", 2.1, "L"), ("nittype", "Traitement", 2.6, "L"),
    ("coating", "Revêt.", 2.1, "L"), ("mfr", "Fourn.", 1.8, "L"),
    ("obs", "Observations", 3.0, "L"),
]


def render_excel_fr(rows, cols, rng):
    GRID = colors.HexColor("#7f8c8d")
    BANNER = colors.HexColor("#d9d2c5")
    SEC = colors.HexColor("#cfe2d4")
    HY = colors.HexColor("#fff2a8")
    HG = colors.HexColor("#c9e7c2")
    HR = colors.HexColor("#f3c6c0")
    cell = ParagraphStyle("c", fontName="Helvetica", fontSize=6.8, leading=8.2, textColor=INK)
    cellr = ParagraphStyle("cr", parent=cell, alignment=2)
    cellb = ParagraphStyle("cb", parent=cell, fontName="Helvetica-Bold")
    note = ParagraphStyle("nt", fontName="Times-Italic", fontSize=6.4, leading=7.8,
                          textColor=colors.HexColor("#444"))
    headc = ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=6.9, leading=8.4,
                           textColor=colors.black)
    ncol = len(cols)

    data, styles = [], []
    # Excel-like merged title rows
    data.append([Paragraph("<b>SOCIÉTÉ EXEMPLE SA — Nomenclature Outillage (Démo)</b>",
                           ParagraphStyle("t", fontName="Helvetica-Bold", fontSize=9,
                                          textColor=colors.black))] + [""] * (ncol - 1))
    data.append([Paragraph("Cmd: DEMO-FR-3391   Client: Sté Exemple SA   "
                           "Plan: PL-3391   Ind: B   Date: 04/06/2026", note)] + [""] * (ncol - 1))
    styles += [("SPAN", (0, 0), (-1, 0)), ("SPAN", (0, 1), (-1, 1)),
               ("BACKGROUND", (0, 0), (-1, 1), BANNER)]
    data.append([Paragraph(c[1], headc) for c in cols])
    hdr_row = 2

    r = hdr_row + 1
    fn = 1
    sec_titles = ["ENSEMBLE 100 — INSERTS", "ENSEMBLE 200 — TIROIRS",
                  "ENSEMBLE 300 — ÉJECTION", "ENSEMBLE 400 — STANDARDS",
                  "ENSEMBLE 500 — RÉGULATION"]
    sec_i = 0
    notes_pool = ["matière à confirmer", "voir plan indice C", "fourni par client",
                  "contrôle dimensionnel 100%", "délai critique"]
    notes_used = []
    for idx, row in enumerate(rows):
        if idx and idx % 26 == 0 and sec_i < len(sec_titles):
            data.append([Paragraph(sec_titles[sec_i], cellb)] + [""] * (ncol - 1))
            styles += [("SPAN", (0, r), (-1, r)), ("BACKGROUND", (0, r), (-1, r), SEC)]
            sec_i += 1
            r += 1
            if rng.random() < 0.5:  # random blank separator row
                data.append([""] * ncol)
                r += 1
        line = []
        for i, cd in enumerate(row):
            txt = cd["text"]
            if cols[i][0] in ("dimx", "dimy", "dimz") and "." in txt:
                txt = txt.replace(".", ",")  # comma decimals
            st = cellr if cols[i][3] == "R" else cell
            if cd["cls"] == "yellow" and cols[i][0] == "material" and rng.random() < 0.4:
                txt = f"{txt} ({fn})"
                notes_used.append((fn, rng.choice(notes_pool)))
                fn += 1
            if rng.random() < 0.06:
                st = cellb  # random bold noise
            line.append(Paragraph(txt or "&nbsp;", st))
            # customer's own colour highlights (independent of our scoring)
            rr = rng.random()
            if rr < 0.05:
                styles.append(("BACKGROUND", (i, r), (i, r), HR))
            elif rr < 0.13:
                styles.append(("BACKGROUND", (i, r), (i, r), HY))
            elif rr < 0.18:
                styles.append(("BACKGROUND", (i, r), (i, r), HG))
        data.append(line)
        r += 1

    # total + notes block
    data.append([Paragraph("<b>TOTAL pièces</b>", cellb)]
                + [""] * (ncol - 2) + [Paragraph(f"<b>{len(rows)}</b>", cellr)])
    styles += [("SPAN", (0, r), (-2, r)), ("BACKGROUND", (0, r), (-1, r), BANNER)]
    r += 1
    seen = set()
    for nfn, ntxt in notes_used[:6]:
        if nfn in seen:
            continue
        seen.add(nfn)
        data.append([Paragraph(f"({nfn}) {ntxt}", note)] + [""] * (ncol - 1))
        styles.append(("SPAN", (0, r), (-1, r)))
        r += 1

    t = Table(data, colWidths=[c[2] * cm for c in cols], repeatRows=hdr_row + 1)
    base = [
        ("BACKGROUND", (0, hdr_row), (-1, hdr_row), colors.HexColor("#bfc9d1")),
        ("GRID", (0, 0), (-1, -1), 0.4, GRID),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2.5), ("RIGHTPADDING", (0, 0), (-1, -1), 2.5),
        ("TOPPADDING", (0, 0), (-1, -1), 1.6), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.6),
    ]
    t.setStyle(TableStyle(base + styles))

    def onpage(cv, doc):
        w, h = landscape(A4)
        cv.saveState()
        cv.setStrokeColor(colors.HexColor("#7f8c8d"))
        cv.line(1.3 * cm, 0.95 * cm, w - 1.3 * cm, 0.95 * cm)
        cv.setFont("Helvetica", 6.4)
        cv.setFillColor(colors.HexColor("#555"))
        cv.drawString(1.3 * cm, 0.6 * cm,
                      r"Imprimé le 04/06/2026 14:52   —   C:\BOM\Outillage\M-3391_nomenclature.xlsx")
        cv.restoreState()

    return _build("demo_messy_fr.pdf", landscape(A4), t, onpage, 0.7 * cm,
                  numbered_canvas("Page", "Helvetica", colors.HexColor("#555")))


# ===========================================================================
# DOC 3 — Old controlled-copy fax/scan (monochrome, Courier digits, grain)
# ===========================================================================
COLS_ZH = [
    ("pos", "序号", 1.0, "L"), ("partno", "图号", 2.7, "L"),
    ("desc", "名称", 4.4, "L"), ("qty", "数量", 1.0, "R"),
    ("material", "材料", 2.7, "L"), ("dimcombo", "尺寸 (mm)", 3.6, "L"),
    ("hardness", "硬度", 2.3, "L"), ("nittype", "表面处理", 2.8, "L"),
]


def _zfont(text):
    return "Courier" if text.isascii() else "STSong-Light"


def render_scan_zh(rows, cols, rng):
    BLK = colors.black
    GRY = colors.HexColor("#3a3a3a")
    SEC = colors.HexColor("#cfcfcf")
    headc = ParagraphStyle("h", fontName="STSong-Light", fontSize=8, leading=10,
                           textColor=colors.white)
    data, styles = [], []
    data.append([Paragraph(c[1], headc) for c in cols])
    sec_titles = ["部件 100 — 镶件", "部件 200 — 滑块", "部件 300 — 顶出", "部件 400 — 标准件"]
    sec_i = 0
    r = 1
    for idx, row in enumerate(rows):
        if idx and idx % 24 == 0 and sec_i < len(sec_titles):
            data.append([Paragraph(sec_titles[sec_i],
                        ParagraphStyle("s", fontName="STSong-Light", fontSize=7.6,
                                       leading=9, textColor=BLK))] + [""] * (len(cols) - 1))
            styles += [("SPAN", (0, r), (-1, r)), ("BACKGROUND", (0, r), (-1, r), SEC)]
            sec_i += 1
            r += 1
        line = []
        for i, cd in enumerate(row):
            txt = cd["text"] or "　"
            al = 2 if cols[i][3] == "R" else 0
            st = ParagraphStyle(f"z{i}", fontName=_zfont(txt), fontSize=7.4,
                                leading=9, textColor=GRY, alignment=al)
            line.append(Paragraph(txt, st))
        data.append(line)
        r += 1

    t = Table(data, colWidths=[c[2] * cm for c in cols], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BLK),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, BLK),
        # heavy, uneven outer frame (scan/photocopy feel)
        ("LINEABOVE", (0, 0), (-1, 0), 2.0, BLK),
        ("LINEBELOW", (0, -1), (-1, -1), 1.6, BLK),
        ("LINEBEFORE", (0, 0), (0, -1), 1.8, BLK),
        ("LINEAFTER", (-1, 0), (-1, -1), 0.9, BLK),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 2.6), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.6),
    ]))

    def onpage(cv, doc):
        w, h = A4
        cv.saveState()
        # scan grain
        gr = random.Random(1000 + doc.page)
        cv.setFillColor(colors.HexColor("#c8c8c8"))
        for _ in range(260):
            cv.circle(gr.uniform(1 * cm, w - 1 * cm), gr.uniform(1 * cm, h - 1 * cm),
                      gr.uniform(0.1, 0.45), fill=1, stroke=0)
        # diagonal watermark
        cv.saveState()
        cv.translate(w / 2, h / 2)
        cv.rotate(32)
        cv.setFont("STSong-Light", 46)
        cv.setFillColor(colors.HexColor("#e2e2e2"))
        cv.drawCentredString(0, 0, "受控文件  COPY  副本")
        cv.restoreState()
        # title banner box
        cv.setStrokeColor(BLK)
        cv.setLineWidth(1.4)
        cv.rect(1.2 * cm, h - 2.4 * cm, w - 2.4 * cm, 1.3 * cm, fill=0, stroke=1)
        cv.setFont("STSong-Light", 11)
        cv.setFillColor(BLK)
        cv.drawString(1.5 * cm, h - 1.55 * cm, "物料清单 (BOM) — 压铸模具 (演示)")
        cv.setFont("Courier", 7.5)
        cv.drawString(1.5 * cm, h - 2.05 * cm,
                      "DWG TH-5560  ORDER DEMO-ZH-5560  REV A  2026-06-04")
        # controlled stamp (rotated, double border)
        cv.saveState()
        cv.translate(w - 3.6 * cm, h - 1.75 * cm)
        cv.rotate(-8)
        cv.setLineWidth(1.2)
        cv.rect(-1.4 * cm, -0.55 * cm, 2.8 * cm, 1.1 * cm, fill=0, stroke=1)
        cv.rect(-1.28 * cm, -0.43 * cm, 2.56 * cm, 0.86 * cm, fill=0, stroke=1)
        cv.setFont("STSong-Light", 9)
        cv.drawCentredString(0, 0.08 * cm, "受 控")
        cv.setFont("Courier", 6)
        cv.drawCentredString(0, -0.32 * cm, "CONTROLLED")
        cv.restoreState()
        # uneven photocopy edge frame
        cv.setLineWidth(2.2)
        cv.line(0.7 * cm, 0.7 * cm, 0.7 * cm, h - 0.7 * cm)
        cv.setLineWidth(0.7)
        cv.line(w - 0.7 * cm, 0.7 * cm, w - 0.7 * cm, h - 0.7 * cm)
        cv.setFont("Courier", 6.5)
        cv.setFillColor(GRY)
        cv.drawString(1.2 * cm, 0.75 * cm, "FAX / SCAN  -  UNCONTROLLED IF PRINTED")
        cv.restoreState()

    return _build("demo_messy_zh.pdf", A4, t, onpage, 2.7 * cm,
                  numbered_canvas("第", "Courier", GRY))


# ===========================================================================
def _build(filename, pagesize, table, onpage, top_margin, canvasmaker):
    doc = BaseDocTemplate(
        str(OUTDIR / filename), pagesize=pagesize,
        leftMargin=1.3 * cm, rightMargin=1.3 * cm,
        topMargin=top_margin, bottomMargin=1.2 * cm,
        title=filename,
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="m")
    doc.addPageTemplates([PageTemplate(id="all", frames=[frame], onPage=onpage)])
    doc.build([table], canvasmaker=canvasmaker)


def main():
    summary = {}

    c1 = {"green": 0, "yellow": 0, "red": 0}
    rows1 = gen_rows(COLS_CLEAN, {"green": .75, "yellow": .20, "red": .05},
                     800, "de", random.Random(42), c1)
    render_clean(rows1, COLS_CLEAN)
    summary["demo_clean_de_en.pdf"] = c1

    c2 = {"green": 0, "yellow": 0, "red": 0}
    rows2 = gen_rows(COLS_FR, {"green": .60, "yellow": .35, "red": .05},
                     230, "fr", random.Random(7), c2)
    render_excel_fr(rows2, COLS_FR, random.Random(7))
    summary["demo_messy_fr.pdf"] = c2

    c3 = {"green": 0, "yellow": 0, "red": 0}
    rows3 = gen_rows(COLS_ZH, {"green": .60, "yellow": .35, "red": .05},
                     200, "zh", random.Random(99), c3)
    render_scan_zh(rows3, COLS_ZH, random.Random(99))
    summary["demo_messy_zh.pdf"] = c3

    lines = ["# Demo-Stücklisten — Answer Key (Design-Verteilung)\n",
             "Synthetische Mock-Daten. Werte aus dem echten Stammdaten-Vokabular.",
             "Drei unterschiedliche Layouts; die real gescorte Quote braucht einen",
             "Kalibrierungslauf durch die Pipeline.\n"]
    for fn, c in summary.items():
        tot = sum(c.values())
        lines += [f"## {fn}",
                  f"- scorebare Zellen: {tot}",
                  f"- GRÜN: {c['green']} ({100*c['green']/tot:.1f}%)",
                  f"- GELB: {c['yellow']} ({100*c['yellow']/tot:.1f}%)",
                  f"- ROT:  {c['red']} ({100*c['red']/tot:.1f}%)\n"]
    (OUTDIR / "answer_key.md").write_text("\n".join(lines), encoding="utf-8")
    for fn, c in summary.items():
        tot = sum(c.values())
        print(f"{fn}: G={100*c['green']/tot:.1f}% Y={100*c['yellow']/tot:.1f}% "
              f"R={100*c['red']/tot:.1f}% (cells={tot})")


if __name__ == "__main__":
    main()
