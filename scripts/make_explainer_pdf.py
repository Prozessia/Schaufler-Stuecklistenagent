"""Generate a from-zero explainer PDF for the BOM-Mapper system.

Audience: anyone (developer, manager, new team member) with no prior knowledge
of the tooling / die-casting domain. Explains the construction process, the
pain points, how the system solves them, how to use it, when it makes sense,
the prerequisites, and what a new-customer onboarding needs.

Run:  python scripts/make_explainer_pdf.py
Output: docs/BOM-Mapper_Erklaerung.pdf
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    ListFlowable,
    ListItem,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

# --- Palette -----------------------------------------------------------------
INK = colors.HexColor("#1f2933")
ACCENT = colors.HexColor("#1d4e89")
ACCENT_LIGHT = colors.HexColor("#e8eef6")
MUTED = colors.HexColor("#52606d")
GREEN = colors.HexColor("#2e8540")
YELLOW = colors.HexColor("#e0a800")
RED = colors.HexColor("#c0392b")
RULE = colors.HexColor("#cbd2d9")

OUT = Path(__file__).resolve().parents[1] / "docs" / "BOM-Mapper_Erklaerung.pdf"
OUT.parent.mkdir(parents=True, exist_ok=True)

# --- Styles ------------------------------------------------------------------
ss = getSampleStyleSheet()


def style(name, **kw):
    base = kw.pop("parent", ss["Normal"])
    return ParagraphStyle(name, parent=base, **kw)


S = {
    "title": style("title", fontName="Helvetica-Bold", fontSize=26, leading=30,
                   textColor=ACCENT, spaceAfter=6),
    "subtitle": style("subtitle", fontName="Helvetica", fontSize=13, leading=18,
                      textColor=MUTED, spaceAfter=4),
    "h1": style("h1", fontName="Helvetica-Bold", fontSize=17, leading=21,
                textColor=ACCENT, spaceBefore=18, spaceAfter=8),
    "h2": style("h2", fontName="Helvetica-Bold", fontSize=13, leading=17,
                textColor=INK, spaceBefore=12, spaceAfter=5),
    "body": style("body", fontName="Helvetica", fontSize=10.5, leading=15.5,
                  textColor=INK, alignment=TA_JUSTIFY, spaceAfter=7),
    "bullet": style("bullet", fontName="Helvetica", fontSize=10.5, leading=15,
                    textColor=INK, alignment=TA_LEFT),
    "small": style("small", fontName="Helvetica", fontSize=9, leading=12.5,
                   textColor=MUTED),
    "callout": style("callout", fontName="Helvetica", fontSize=10.5, leading=15,
                     textColor=INK, alignment=TA_LEFT),
    "cell": style("cell", fontName="Helvetica", fontSize=9.5, leading=13,
                  textColor=INK),
    "cellb": style("cellb", fontName="Helvetica-Bold", fontSize=9.5, leading=13,
                   textColor=colors.white),
    "tocitem": style("tocitem", fontName="Helvetica", fontSize=11, leading=20,
                     textColor=INK),
}

story = []


def P(text, st="body"):
    story.append(Paragraph(text, S[st]))


def H1(text):
    story.append(Paragraph(text, S["h1"]))


def H2(text):
    story.append(Paragraph(text, S["h2"]))


def SP(h=6):
    story.append(Spacer(1, h))


def bullets(items, st="bullet"):
    flow = ListFlowable(
        [ListItem(Paragraph(t, S[st]), leftIndent=6, value="•") for t in items],
        bulletType="bullet", start="•", leftIndent=14, bulletColor=ACCENT,
        bulletFontSize=10,
    )
    story.append(flow)
    SP(7)


def callout(title, text, bar=ACCENT, bg=ACCENT_LIGHT):
    inner = []
    if title:
        inner.append(Paragraph(f"<b>{title}</b>", S["callout"]))
        inner.append(Spacer(1, 2))
    inner.append(Paragraph(text, S["callout"]))
    t = Table([[inner]], colWidths=[16.0 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("LINEBEFORE", (0, 0), (0, -1), 3, bar),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(t)
    SP(9)


def table(rows, col_widths, header_bg=ACCENT, body_styles=None):
    data = []
    for r, row in enumerate(rows):
        line = []
        for c, val in enumerate(row):
            if r == 0:
                line.append(Paragraph(str(val), S["cellb"]))
            else:
                line.append(Paragraph(str(val), S["cell"]))
        data.append(line)
    t = Table(data, colWidths=col_widths, repeatRows=1)
    st = [
        ("BACKGROUND", (0, 0), (-1, 0), header_bg),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, RULE),
        ("LINEAFTER", (0, 0), (-2, -1), 0.4, RULE),
        ("BOX", (0, 0), (-1, -1), 0.6, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f6f9")]),
    ]
    if body_styles:
        st.extend(body_styles)
    t.setStyle(TableStyle(st))
    story.append(t)
    SP(9)


# =============================================================================
# COVER
# =============================================================================
story.append(Spacer(1, 3.2 * cm))
P("BOM-Mapper", "title")
P("KI-gestütztes Stücklisten-Mapping für den Werkzeugbau", "subtitle")
SP(10)
P("Von Null erklärt: Der Konstruktionsprozess, seine Probleme, "
  "wie das System hilft, wie man es benutzt, wann es sinnvoll ist "
  "und was man für einen neuen Kunden braucht.", "subtitle")
SP(26)
t = Table([[""]], colWidths=[16 * cm], rowHeights=[3])
t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), ACCENT)]))
story.append(t)
SP(14)
P("Dieses Dokument richtet sich an Personen ohne Vorwissen über Werkzeugbau "
  "oder Druckguss — z. B. Entwickler, Projektleiter oder neue Teammitglieder. "
  "Es setzt keine Branchenkenntnis voraus und erklärt jeden Fachbegriff.", "small")
story.append(PageBreak())

# =============================================================================
# INHALT
# =============================================================================
H1("Inhalt")
toc = [
    "1.  Worum geht es? (Die Kurzfassung)",
    "2.  Was macht ein Werkzeugbauer? — Die Branche von Null",
    "3.  Was ist eine Stückliste (BOM)?",
    "4.  Der heutige Prozess: Wie ein Konstrukteur arbeitet",
    "5.  Die Probleme und Pain Points",
    "6.  Was sind Stammdaten? (Der Wahrheitsanker)",
    "7.  Wie unser System das löst — die 5 Schichten",
    "8.  Das Ampel-System: Grün / Gelb / Rot",
    "9.  Wie man das System benutzt — Schritt für Schritt",
    "10. Wann ist das System sinnvoll — und wo sind die Grenzen?",
    "11. Voraussetzungen für den Einsatz",
    "12. Neuer Kunde: Was man alles braucht (Onboarding)",
    "13. Zusammenfassung",
]
for item in toc:
    P(item, "tocitem")
story.append(PageBreak())

# =============================================================================
# 1
# =============================================================================
H1("1. Worum geht es? (Die Kurzfassung)")
P("Ein Werkzeugbauer bekommt von seinen Kunden <b>Stücklisten</b> — also "
  "Bauteil-Listen — in den unterschiedlichsten Formaten, Sprachen und "
  "Strukturen. Jeder Kunde macht es anders. Damit der Werkzeugbauer arbeiten "
  "kann, muss er jede dieser Listen in seine <b>eigene, einheitliche Vorlage</b> "
  "übertragen. Das passiert heute von Hand und dauert im Schnitt "
  "<b>rund 5 Stunden pro Stückliste</b> — kurze Listen 1–2 Stunden, komplexe "
  "Werkzeuge auch 2–3 Tage.")
P("Unser System automatisiert diese Übertragung mit Künstlicher Intelligenz. "
  "Das Entscheidende dabei ist nicht „die KI macht alles”, sondern: <b>Das "
  "System sagt für jeden Wert ehrlich, wie sicher es ist</b> — über ein "
  "Ampel-System (Grün/Gelb/Rot). So weiß der Mitarbeiter genau, worauf er sich "
  "verlassen kann und was er prüfen muss. Genau daran sind bisherige Anbieter "
  "gescheitert: Sie lieferten hübsche, aber unzuverlässige Ergebnisse.")
callout("Der Kerngedanke in einem Satz",
        "Nicht den Menschen zu 100 % ersetzen, sondern seine stumpfe Arbeit "
        "drastisch reduzieren — und dabei absolut ehrlich sein, welchen "
        "Ergebnissen man trauen kann.")

# =============================================================================
# 2
# =============================================================================
H1("2. Was macht ein Werkzeugbauer? — Die Branche von Null")
P("Stell dir einen Autohersteller vor, der ein Aluminium-Getriebegehäuse in "
  "Millionenstückzahl produzieren will. Dafür braucht er eine <b>Gießform</b> "
  "— ein riesiges, tonnenschweres Stahlwerkzeug, in das flüssiges Aluminium "
  "unter hohem Druck gepresst wird (das nennt man <b>Druckguss</b>).")
P("Der <b>Werkzeugbauer baut genau diese Form</b> — nicht das fertige "
  "Aluminiumteil, sondern das Werkzeug, mit dem der Kunde das Teil später "
  "selbst produziert. Eine solche Form ist kein einzelnes Stück, sondern eine "
  "<b>Baugruppe aus hunderten Einzelteilen</b>.")
H2("Ein paar Bauteil-Begriffe (nur zum Einordnen)")
bullets([
    "<b>Formplatte:</b> große Stahlplatte, Grundgerüst der Form.",
    "<b>Schieber:</b> bewegliches Teil, das seitliche Konturen formt.",
    "<b>Kern / Einsatz:</b> formt Innenkonturen oder austauschbare Bereiche.",
    "<b>Auswerferstift:</b> stößt das fertige Teil aus der Form.",
    "<b>Normalien:</b> Standard-Katalogteile von Zulieferern (z. B. Federn, "
    "Stifte). Vergleichbar mit fertigen Bibliotheken statt Eigenbau.",
])
P("Wichtig ist nur die Erkenntnis: Ein Werkzeug besteht aus sehr vielen "
  "Teilen, und jedes Teil hat Eigenschaften wie Material, Maße, Härte und "
  "Oberflächenbehandlung. Genau diese Eigenschaften stehen in der Stückliste.")

# =============================================================================
# 3
# =============================================================================
H1("3. Was ist eine Stückliste (BOM)?")
P("<b>Stückliste = Bill of Materials (BOM).</b> Im Kern ist das eine "
  "<b>Tabelle</b>: Jede Zeile ist ein Bauteil, die Spalten beschreiben es. "
  "Ein vereinfachtes Beispiel:")
table(
    [
        ["Pos.", "Benennung", "Werkstoff", "Maße (mm)", "Härte", "Stück"],
        ["1000", "Formplatte", "1.2343", "800 × 600 × 120", "44–46 HRC", "1"],
        ["1010", "Auswerferstift", "1.2344", "Ø8 × 150", "50 HRC", "24"],
        ["1020", "Schieber", "1.2312", "210 × 95 × 60", "30 HRC", "2"],
    ],
    [1.4 * cm, 3.6 * cm, 2.3 * cm, 3.6 * cm, 2.6 * cm, 1.4 * cm],
)
H2("Begriffe in der Tabelle")
bullets([
    "<b>Werkstoff / Werkstoffnummer:</b> das Material, z. B. Stahl „1.2343”. "
    "Dasselbe Material hat oft mehrere Schreibweisen: „1.2343” = „H11” = "
    "„X38CrMoV5-1”. Für einen Menschen klar, für ein Programm eine Hürde.",
    "<b>Härte (HRC / HB):</b> wie hart der Stahl ist. HRC und HB sind zwei "
    "Mess-Skalen. „44–46 HRC” ist ein erlaubter Bereich.",
    "<b>Maße:</b> Länge × Breite × Höhe (oder Durchmesser Ø). Oft in einem "
    "einzigen Feld zusammengeschrieben und muss aufgeteilt werden.",
])
callout("Der eigentliche Knackpunkt",
        "Jeder Kunde liefert seine Stückliste anders: anderes Dateiformat "
        "(PDF, Excel, gescanntes Bild), andere Sprache (Deutsch, Englisch, "
        "Französisch, Chinesisch …), andere Spaltennamen, andere Schreibweisen. "
        "Der Werkzeugbauer hat aber nur EINE eigene Zielvorlage, in die alles "
        "hineinmuss.")
P("<b>Software-Analogie:</b> Das ist ein Schema-Migrations-/ETL-Problem "
  "zwischen vielen unbekannten Quellsystemen und einem festen Zielschema — "
  "nur dass die Quellen in natürlicher Sprache und unstrukturierten Formaten "
  "vorliegen. Genau hier sind klassische Regeln chancenlos und KI stark.")

# =============================================================================
# 4
# =============================================================================
H1("4. Der heutige Prozess: Wie ein Konstrukteur arbeitet")
P("Ohne unser System läuft die Übertragung manuell ab. Ein erfahrener "
  "Konstrukteur oder Sachbearbeiter:")
bullets([
    "öffnet die Kunden-Stückliste (z. B. ein 120-Positionen-PDF auf Englisch),",
    "öffnet daneben die eigene leere Zielvorlage,",
    "überträgt Position für Position von Hand,",
    "schlägt dabei jeden Werkstoff nach („ist H13 = 1.2344? ja”),",
    "teilt zusammengeschriebene Maße auf, rechnet Zoll in Millimeter um,",
    "prüft die Werte mit seinem Fachwissen auf Plausibilität.",
])
H2("Der Zeitaufwand")
P("Der Aufwand schwankt enorm mit der Komplexität des Werkzeugs:")
table(
    [
        ["Komplexität", "Typische Dauer manuell"],
        ["Kurze, einfache Stückliste", "1 – 2 Stunden"],
        ["Durchschnitt", "ca. 5 Stunden"],
        ["Komplexes Werkzeug, viele Positionen", "2 – 3 Tage"],
    ],
    [8.0 * cm, 8.0 * cm],
)
P("Wichtig: Das ist <b>kein stumpfes Abtippen</b>. Der Mitarbeiter bringt "
  "Fachwissen ein — er erkennt z. B., wenn ein Maß unrealistisch ist. Dieses "
  "Wissen ist Teil der Arbeit und lässt sich nicht einfach „wegautomatisieren”.")

# =============================================================================
# 5
# =============================================================================
H1("5. Die Probleme und Pain Points")
bullets([
    "<b>Zeit:</b> Im Schnitt 5 Stunden pro Stückliste — bei vielen Aufträgen "
    "ein erheblicher, teurer Engpass.",
    "<b>Format-Chaos:</b> Jeder Kunde ist anders; man kann sich auf nichts "
    "Festes verlassen. Neue Kunden bringen neue Formate.",
    "<b>Fehleranfälligkeit:</b> Stundenlanges Abtippen führt zu Tippfehlern; "
    "der Mensch wird „betriebsblind” für eigene Fehler.",
    "<b>Verlässlichkeit der bisherigen Tools:</b> Frühere KI-Anbieter "
    "lieferten Ergebnisse, die „augenscheinlich inhaltlich falsch” waren — "
    "ohne zu sagen, welchen Werten man trauen kann.",
    "<b>Kein Vertrauenssystem = kein Gewinn:</b> Wenn der Mitarbeiter am Ende "
    "trotzdem ALLES nachprüfen muss, hat er mehr Arbeit als vorher. Das ist "
    "der wichtigste Pain Point überhaupt.",
])
callout("Was Kunden wirklich erwarten",
        "Eine ehrliche, realistische Lösung. Selbst wenn nur ein Teil "
        "automatisch zuverlässig läuft, ist das ein Gewinn — ABER nur, wenn "
        "man sich auf diesen Teil wirklich verlassen kann. „Mensch im "
        "Prozess” (Human-in-the-Loop) ist ausdrücklich akzeptiert und "
        "erwartet. Niemand verlangt 100 % Automatik.")

# =============================================================================
# 6
# =============================================================================
H1("6. Was sind Stammdaten? (Der Wahrheitsanker)")
P("<b>Stammdaten</b> sind die relativ festen Referenzdaten eines Unternehmens "
  "— Daten, die sich selten ändern und überall nachgeschlagen werden. Das "
  "Gegenteil sind <b>Bewegungsdaten</b> (die täglich entstehen, z. B. einzelne "
  "Aufträge).")
table(
    [
        ["Stammdaten (fest)", "Bewegungsdaten (laufend)"],
        ["Werkstoff-Katalog", "Einzelne Kundenaufträge"],
        ["Einheiten-Tabelle", "Konkrete Stücklisten"],
        ["Teilegruppen, Beschichtungen", "Bestellungen"],
        ["Artikelstamm (alle bekannten Teile)", "—"],
    ],
    [8.0 * cm, 8.0 * cm],
)
P("<b>Software-Analogie:</b> Stammdaten sind wie Referenz- bzw. "
  "Lookup-Tabellen, Enums oder Konstanten im Code. Bewegungsdaten sind die "
  "Zeilen in einer „orders”-Tabelle.")
callout("Warum Stammdaten so wichtig sind",
        "Sie sind der Wahrheitsanker. Wenn der Kunde „H11” schreibt und das "
        "System im Werkstoff-Katalog nachschlägt und findet „H11 = 1.2343, "
        "gültiger Warmarbeitsstahl”, dann WEISS es, dass der Wert echt ist — "
        "statt nur zu raten. Das ist der Unterschied zwischen „abgesichert” "
        "und „halluziniert”.", bar=GREEN, bg=colors.HexColor("#eaf4ec"))

# =============================================================================
# 7
# =============================================================================
H1("7. Wie unser System das löst — die 5 Schichten")
P("Das System verarbeitet jede Stückliste in fünf aufeinander aufbauenden "
  "Schichten:")
table(
    [
        ["Schicht", "Aufgabe", "Kurz erklärt"],
        ["1. Einlesen", "Datei → Tabelle",
         "Liest PDF, Excel, CSV — auch gescannte PDFs (Bild-Erkennung als "
         "Rückfallebene). Format-unabhängig."],
        ["2. Mapping", "Spalten zuordnen",
         "Die KI erkennt, welche Quellspalte welchem Zielfeld entspricht — "
         "egal in welcher Sprache."],
        ["3. Transformation", "Werte normalisieren",
         "Maße aufteilen, Zoll→mm, Material gegen Stammdaten abgleichen, "
         "Texte bereinigen."],
        ["4. Confidence", "Sicherheit bewerten",
         "Mehrere unabhängige Prüfungen ergeben pro Wert eine Ampelfarbe "
         "(Grün/Gelb/Rot)."],
        ["5. Review & Export", "Prüfen & ausgeben",
         "Mitarbeiter prüft im Dashboard, korrigiert, exportiert in die "
         "Zielvorlage."],
    ],
    [2.7 * cm, 3.0 * cm, 10.3 * cm],
)
P("Die Schichten 1–3 lösen das <b>Struktur-Problem</b> (welche Spalte ist "
  "was, welcher Wert gehört wohin). Schicht 4 löst das <b>Vertrauens-Problem</b> "
  "— und das ist der eigentliche Unterschied zur gescheiterten Konkurrenz.")

# =============================================================================
# 8
# =============================================================================
H1("8. Das Ampel-System: Grün / Gelb / Rot")
P("Für jeden einzelnen Wert entscheidet das System, wie sicher es ist, und "
  "vergibt eine Farbe:")
table(
    [
        ["Farbe", "Bedeutung", "Was der Mitarbeiter tut"],
        ["GRÜN", "Sehr sicher — mehrere Prüfungen bestätigen den Wert.",
         "Nicht anfassen. Vertrauen."],
        ["GELB", "Vorschlag vorhanden, aber bitte kurz bestätigen.",
         "Wert ist vorausgefüllt → mit einem Blick bestätigen oder korrigieren."],
        ["ROT", "Unsicher — das System ist ehrlich „weiß nicht”.",
         "Manuell ausfüllen, wie früher (selten)."],
    ],
    [2.2 * cm, 7.3 * cm, 6.5 * cm],
    body_styles=[
        ("TEXTCOLOR", (0, 1), (0, 1), GREEN),
        ("TEXTCOLOR", (0, 2), (0, 2), YELLOW),
        ("TEXTCOLOR", (0, 3), (0, 3), RED),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
    ],
)
callout("Das wichtigste Designprinzip: lieber Gelb als falsches Grün",
        "Ein falsches „Grün” ist Gift: Erlebt der Mitarbeiter EIN falsches "
        "Grün, traut er ab dann gar keinem Grün mehr und prüft wieder alles. "
        "Deshalb ist das System bewusst vorsichtig — im Zweifel wird ein Feld "
        "lieber gelb (wird ohnehin angeschaut) als fälschlich grün.")
H2("Was die echten Zahlen bedeuten")
P("In Tests wurden rund <b>35,5 % der Werte Grün</b>, <b>sehr viel Gelb</b> "
  "und <b>nur ganz wenig Rot</b> erreicht. Das klingt zunächst nach „nur ein "
  "Drittel automatisch” — ist aber die <b>gesunde</b> Verteilung:")
bullets([
    "<b>Wenig Rot</b> heißt: Das System wirft fast nie ganz das Handtuch.",
    "<b>Viel Gelb</b> heißt: Es macht fast überall einen brauchbaren "
    "Vorschlag, den man nur noch bestätigen muss — nicht neu tippen.",
    "<b>Verlässliches Grün</b> heißt: Dieser Teil kostet null Zeit.",
])
callout("Die richtige Erfolgs-Messgröße",
        "Nicht „wie viel Prozent Grün?”, sondern „wie viel schneller bin ich "
        "als die 5 Stunden von Hand?”. Entscheidend ist, wie BILLIG eine "
        "Gelb-Bestätigung ist. Mit einem guten Review-Dashboard dauert ein "
        "Gelb-Klick Sekunden statt Minuten — und genau dadurch entsteht der "
        "Zeitgewinn, nicht allein durch das Grün.",
        bar=YELLOW, bg=colors.HexColor("#fbf4dd"))

# =============================================================================
# 9
# =============================================================================
H1("9. Wie man das System benutzt — Schritt für Schritt")
steps = [
    ("Hochladen", "Die Kunden-Stückliste (PDF/Excel) per Drag & Drop "
     "hochladen. Kein Vorbereiten der Datei nötig."),
    ("Verarbeiten lassen", "Das System liest, mappt, transformiert und "
     "bewertet automatisch. Das Ergebnis erscheint als Tabelle in der "
     "fertigen Zielstruktur."),
    ("Ampel ansehen", "Auf einen Blick sieht man, was grün, gelb und rot ist. "
     "Die gesamte Spaltenstruktur ist bereits korrekt — die „Gerüst-Arbeit” "
     "entfällt komplett."),
    ("Gelb bestätigen", "Auf eine gelbe Zelle klicken: Das Detail-Panel zeigt "
     "die Originalstelle im PDF MARKIERT an (Quell-Verlinkung). Ein Blick "
     "genügt → bestätigen oder korrigieren. Hier verbringt man die meiste "
     "Zeit — und hier entscheidet sich der Zeitgewinn."),
    ("Rot bearbeiten", "Die wenigen roten Felder manuell ausfüllen."),
    ("Vollständigkeit prüfen", "Sicherstellen, dass alle Positionen erfasst "
     "sind. Das System ist auf „kein Datenverlust” ausgelegt — jede "
     "Quellzeile taucht im Ergebnis auf."),
    ("Exportieren", "Mit einem Klick in die echte, formatierte Zielvorlage "
     "(Excel) exportieren. Geht weiter in den Werkzeugbau / nachgelagerte "
     "Systeme."),
    ("Lernen lassen", "Korrekturen werden gespeichert und verbessern das "
     "Mapping beim nächsten Auftrag desselben Kunden."),
]
flow = ListFlowable(
    [ListItem(Paragraph(f"<b>{t}.</b> {d}", S["bullet"]), leftIndent=4)
     for t, d in steps],
    bulletType="1", leftIndent=18, bulletFormat="%s.",
    bulletFontName="Helvetica-Bold", bulletColor=ACCENT,
)
story.append(flow)
SP(8)
callout("Die Vertrauens-Aufbauphase",
        "In den ersten 3–5 Aufträgen prüfen Mitarbeiter erfahrungsgemäß auch "
        "das Grün — das ist normal. Das System muss sich Vertrauen erst "
        "verdienen. Der volle Zeitgewinn entsteht DANACH, wenn man dem Grün "
        "ungeprüft vertraut. Das sollte man dem Kunden vorab sagen.")

# =============================================================================
# 10
# =============================================================================
H1("10. Wann ist das System sinnvoll — und wo sind die Grenzen?")
H2("Sinnvoll, wenn …")
bullets([
    "… regelmäßig Stücklisten in wechselnden Formaten übertragen werden müssen,",
    "… brauchbare Stammdaten (Werkstoff-Katalog usw.) vorhanden sind,",
    "… Mitarbeiter für die Gelb-Prüfung eingeplant werden können,",
    "… der Kunde Human-in-the-Loop akzeptiert (kein 100-%-Automatik-Wunsch).",
])
H2("Grenzen — was das System bewusst NICHT kann")
P("Das System arbeitet aus der Stückliste heraus. Es kann <b>nicht</b> "
  "erkennen, ob die Stückliste dem CAD-Modell oder der technischen Zeichnung "
  "widerspricht.")
P("Beispiel: Der Kunde gibt für einen Block Länge/Breite/Höhe an, die nicht "
  "zum 3D-Modell passen („shit in, shit out”). Solche Widersprüche erkennt nur "
  "ein Mensch mit Fachwissen und Zugriff auf CAD/Zeichnung — kein ehrliches "
  "Tool kann das aus der Stückliste allein. Das ist <b>keine Schwäche der "
  "Umsetzung</b>, sondern eine grundsätzliche Grenze, die man offen "
  "kommuniziert.")
callout("Faustregel",
        "Das System löst das Übertragungs- und Vertrauensproblem hervorragend. "
        "Es löst NICHT das fachliche Fehler-Erkennungsproblem gegenüber "
        "CAD/Zeichnung. Beides offen zu trennen, schafft Vertrauen statt es zu "
        "zerstören.")

# =============================================================================
# 11
# =============================================================================
H1("11. Voraussetzungen für den Einsatz")
H2("Fachlich / organisatorisch")
bullets([
    "<b>Stammdaten:</b> ein brauchbarer Werkstoff-Katalog, Einheiten, "
    "Teilegruppen. Fehlen diese, ist deren Aufbau ein Vor-Projekt.",
    "<b>Eine feste Zielvorlage</b> (die einheitliche Excel-Vorlage des "
    "Unternehmens).",
    "<b>Review-Personen:</b> Mitarbeiter, die das Gelb prüfen.",
    "<b>Echte Beispiel-Stücklisten</b> in voller Varianz (kein Cherry-Picking).",
])
H2("Technisch")
bullets([
    "<b>KI-Backend (DSGVO-konform):</b> Azure OpenAI in einer EU-Region — die "
    "Daten verlassen die EU nicht. Für deutsche Industriekunden oft ein "
    "Kaufkriterium.",
    "<b>Server / Deployment:</b> eine eigene Instanz pro Unternehmen "
    "(kein gemeinsam genutztes System).",
    "<b>Sicheres Secrets-Handling:</b> API-Schlüssel gehören in einen "
    "Secret-Store, niemals in eingecheckte Dateien.",
    "<b>Zugriffsschutz:</b> Login/API-Key, sichere Sessions, korrekt "
    "konfigurierte erlaubte Domains (CORS).",
])

# =============================================================================
# 12
# =============================================================================
H1("12. Neuer Kunde: Was man alles braucht (Onboarding)")
P("Wichtigster Grundsatz: <b>Der Code ändert sich nicht.</b> Pro Unternehmen "
  "wird nur konfiguriert und mit Daten befüllt. Der eigentliche Aufwand liegt "
  "nicht im Programmieren, sondern im Erheben von Domänenwissen, Stammdaten "
  "und einem ehrlichen Test.")
H2("A — Konfiguration (technisch, überschaubar)")
bullets([
    "Firmenkonfiguration: Name, Branche, Fachkontext für die KI, "
    "Vorlagen-Einstellungen (Blattname, Kopfzeile, erste Datenzeile).",
    "Die echte Excel-Zielvorlage des Kunden.",
    "Das Zielschema: jedes Feld mit Name, Spalte, Typ, Pflicht/optional, "
    "Synonymen — passend zur Vorlage.",
    "Stammdaten-Dateien: Werkstoffe, Einheiten, Validierungsregeln.",
])
H2("B — Domänenwissen erheben (der erste echte Aufwand)")
P("Mit den Konstrukteuren des Kunden zusammensetzen: Welche Felder, welche "
  "Bedeutung, welche Pflichtangaben, welche plausiblen Wertebereiche, welche "
  "Fachsprache? Das ist Anforderungsanalyse in einer fremden Domäne.")
H2("C — Stammdaten klären (der kritischste Punkt)")
callout("Die erste Frage an jeden neuen Kunden",
        "„Habt ihr einen sauberen, gepflegten Werkstoff-Katalog / Artikelstamm "
        "— oder müsst ihr den erst aufbauen?” Ohne Stammdaten als Anker liefert "
        "das System viel mehr Gelb/Rot. Das ist das Haupt-Risiko jeder "
        "Einführung — und es liegt nicht im Code.",
        bar=RED, bg=colors.HexColor("#fbecea"))
H2("D — Ehrlicher Benchmark")
bullets([
    "Echte Eingangs-Stücklisten des Kunden sammeln (volle Varianz).",
    "Dazu die von Hand korrekt übertragenen Soll-Ergebnisse.",
    "Damit messen: „Bei euch erreichen wir X % verlässliches Grün.” Das ist "
    "die ehrliche Zahl, die Vertrauen schafft.",
])
H2("E — Schwellwerte kalibrieren")
P("Die Ampel-Schwellwerte pro Kunde einstellen — bei einem "
  "vertrauensempfindlichen Neukunden eher vorsichtig starten (lieber mehr "
  "Gelb), bis Vertrauen aufgebaut ist.")
H2("F — Integrationen")
bullets([
    "Woher kommen die Eingangslisten? (Mail, Netzlaufwerk, Dokumentensystem)",
    "Wohin geht das Ergebnis? (ERP, PLM, Archivsystem)",
])
H2("G — Menschen & Prozess")
bullets([
    "Benannte Review-Mitarbeiter.",
    "Vertrauens-Aufbauphase einplanen (erste Aufträge).",
    "Verantwortliche für Korrekturen und Stammdaten-Pflege.",
    "Schulung und fester Ansprechpartner.",
])
callout("Was über Erfolg oder Misserfolg entscheidet",
        "Drei Dinge — und keines davon ist Code: (1) Hat der Kunde brauchbare "
        "Stammdaten? (2) Gibt es echte Stücklisten + Soll-Ergebnisse für einen "
        "ehrlichen Benchmark? (3) Sind Menschen und Prozess für den Review "
        "eingeplant? Der technische Teil ist an einem Tag erledigt.")

# =============================================================================
# 13
# =============================================================================
H1("13. Zusammenfassung")
bullets([
    "Werkzeugbauer müssen Kunden-Stücklisten in eine einheitliche Vorlage "
    "übertragen — heute manuell, im Schnitt ~5 h (1–2 h bis 2–3 Tage).",
    "Das ist im Kern ein Schema-Migrations-Problem mit unbekannten, "
    "mehrsprachigen, unstrukturierten Quellen — ideal für KI.",
    "Unser System automatisiert das in 5 Schichten und bewertet JEDEN Wert "
    "mit einer Ampel (Grün/Gelb/Rot).",
    "Der eigentliche Mehrwert ist die Ehrlichkeit: Grün ist verlässlich, Gelb "
    "ist ein schnell zu bestätigender Vorschlag, Rot ist selten.",
    "~35,5 % Grün, viel Gelb, wenig Rot ist eine gesunde Verteilung — der "
    "Zeitgewinn kommt vor allem aus schnellem Gelb-Bestätigen.",
    "Grenze: Widersprüche zur CAD/Zeichnung erkennt nur der Mensch.",
    "Neuer Kunde = kein neuer Code, sondern Konfiguration, Stammdaten, "
    "Benchmark und eingeplante Review-Menschen.",
])
SP(6)
P("Stand: dieses Dokument fasst den aktuellen Projekt- und Wissensstand "
  "zusammen und dient als Einstieg ohne Vorwissen.", "small")


# =============================================================================
# BUILD with footer
# =============================================================================
def footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.5)
    canvas.line(2 * cm, 1.5 * cm, A4[0] - 2 * cm, 1.5 * cm)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(2 * cm, 1.1 * cm, "BOM-Mapper — Stücklisten-Mapping für den Werkzeugbau")
    canvas.drawRightString(A4[0] - 2 * cm, 1.1 * cm, f"Seite {doc.page}")
    canvas.restoreState()


doc = BaseDocTemplate(
    str(OUT), pagesize=A4,
    leftMargin=2 * cm, rightMargin=2 * cm,
    topMargin=1.8 * cm, bottomMargin=2 * cm,
    title="BOM-Mapper — Von Null erklärt",
    author="Prozessia",
)
frame = Frame(doc.leftMargin, doc.bottomMargin,
              doc.width, doc.height, id="main")
doc.addPageTemplates([PageTemplate(id="all", frames=[frame], onPage=footer)])
doc.build(story)
print(f"PDF geschrieben: {OUT}")
