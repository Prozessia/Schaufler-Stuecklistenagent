# MATERIAL_MATCHING_PLAN — Werkstoff-Erkennung ohne Katalog-Wildwuchs

**Stand:** 2026-06-01
**Scope:** Material-/Werkstoff-Feld zuverlässiger GRÜN machen — format-agnostisch,
ohne den Katalog pro Kunde aufzublähen, ohne ein einziges False-Grün.
**Verdikt:** Das Problem ist **Abdeckung + Format**, kein Fuzzy-Problem. Es wird
über die **Architektur** gelöst (Werkstoffnummer-Format als gültige ID anerkennen),
**nicht** über manuelle Katalogpflege.

---

## 0. Leitentscheidung (warum kein Katalog-Wachstum)

Der Katalog stammt aus den Schaufler-Stammdaten. Kunden schicken **unendlich**
verschiedene Werkstoffe. Den Katalog jedem Kunden nachzuziehen wäre nie fertig und
bräche das Kernprinzip (CLAUDE.md): *„Kein Hardcoding von Kunden-Formaten",
„Neue Kunden = 0 Code-Änderungen"*.

**Rollen sauber getrennt:**
- **Katalog** = Kanonisierung von *Schauflers eigenen* Werkstoffen (begrenzt, von
  Schaufler besessen). Beantwortet „wie nennt Schaufler dieses Material?", nicht
  „ist das ein gültiges Material?". Wächst nur mit Schauflers Vokabular.
- **Format-Anerkennung (neu)** = der katalog-**unabhängige** Long-Tail. `1.0037`
  wird GRÜN, weil es eine korrekt gelesene, gültige DIN-Werkstoffnummer ist —
  nicht weil es im Katalog steht. 0 Pflege pro Kunde.
- **Feedback-Loop** (`corrections.jsonl`, vorhanden) = der *einzige* legitime
  Lern-/Wachstums-Mechanismus, falls Schaufler einen eigenen Werkstoff dauerhaft
  anders benannt haben will.

**Hebel D (Katalog pro Kunde erweitern) ist gestrichen.**

---

## 1. Empirische Befunde (ZF + TCG, gemessen)

| Datei | Material-Werte | gematcht | no_match |
|---|---|---|---|
| ZF (`Vergütung`) | 526 | 254 (48%) | **272 (52%)** |
| TCG (`WERKST`) | 262 | 148 (56%) | **114 (44%)** |

Die No-Matches zerfallen 
in **drei** Klassen:

**Klasse A — echte Werkstoffnummern, nicht im Katalog (größter, sicherster Hebel)**
- ZF: `1.0037`×53, `1.7225`×11, `1.4310 2H`×4, `1.0501`×3, `1.0580`×2 → ≈ 80
- TCG: `1-0736`×14, `1.1141`/`1-1141`/`11141`×17, `11730`×3, `1-2161`, `ST-37`, `ST-52` → ≈ 90
- Gültige DIN-Nummern `\d.\d{4}`, korrekt extrahiert, nur nicht im 14er-Katalog.

**Klasse B — Format-Varianten echter Nummern (Normalisierungs-Lücke)**
- `10116G`×45 = `1.0116` + G-Suffix; `11141` = Punkt verschluckt; `1-0736` = Bindestrich
- Matcher kann Bindestrich, **nicht** punktverschluckt/G-Suffix.

**Klasse C — gar keine Werkstoffe (müssen no_match bleiben → korrekt gelb)**
- Normteile `DIN912-12.9`×87, `DIN 7603`, `FKL 5.8`; Junk `g=gasnitr.; …`×19,
  `F156900400_STL`×19, `Zeichn.-Nr.`, `-`; generisch `Stahl blank`, `NIRO-BLECH`.
- **Hier ist nichts kaputt.** Sie gehören nicht ins Werkstoff-Feld.

**Erkenntnis:** ~170 der ~386 No-Matches sind echte, korrekt gelesene
Werkstoffnummern (Klasse A+B). Der strikte Regex `\d\.\d{4}` trennt A/B sauber von
C (`912-12.9`, `5.8`, `F156…` matchen ihn empirisch **nicht**).

---

## 2. Green-Pfad-Fakten (verifiziert)

- **Material = nicht-Pflichtfeld → Kategorie C**, nicht die strenge Kategorie A.
  ⇒ Der Green-Gate verlangt für Material **keinen** `strict_exact_match`
  ([green_gate.py:129-133](src/scoring/green_gate.py#L129-L133), greift nur für A).
- Text-Pfad-Grün hängt an `transform_method ∈ _TEXT_PATH_METHODS`
  ([green_gate.py:13-25](src/scoring/green_gate.py#L13-L25)) **oder** `transform_confidence ≥ 0.95`.
- Transform setzt die Methode als `master_data:{method}`
  ([pipeline.py:347-360](src/transform/pipeline.py#L347-L360)).
- Check-2 (Wert im Text-Layer) ist auf dem deterministischen Pfad erfüllt
  (RB-1 `source_locations`), `value_match = MATCH`.

⇒ Eine neue Methode `werkstoff_nr_format` wird grün-fähig, sobald
`"master_data:werkstoff_nr_format"` in `_TEXT_PATH_METHODS` steht — und **nur** auf
dem Text-Pfad (Vision hat kein Text-Layer-Gate → bleibt gelb).

---

## 3. Phasenplan (rein additiv, jede Phase einzeln test- & committbar)

> **Invariante über alle Phasen:** bestehende Pfade (exact_alias, werkstoff_nr_extract,
> fuzzy) bleiben **unverändert**. Neue Logik greift ausschließlich, wo bisher
> `no_match` zurückkam. `test_zero_false_positive` + `test_false_green_vision`
> müssen nach **jeder** Phase grün sein.

### M1 — `din_name` automatisch indexieren (billig, risikoarm)
- **Datei:** [master_data_matcher.py:88-104](src/transform/master_data_matcher.py#L88-L104)
- **Änderung:** beim Katalog-Aufbau jede `din_name` zusätzlich in `alias_map`
  aufnehmen (heute nur manuell in `aliases`). Bei Kollision: Log-Warnung, erste
  Quelle gewinnt.
- **Risiko:** zwei gleiche din_names → niedrig (Log + deterministische Auflösung).
- **Test:** `test_din_name_auto_indexed` — jede `din_name` matcht ihren Canonical.
- **Akzeptanz:** keine Regression; ≥ alle bisherigen Matches bleiben.

### M2 — Format-Normalisierung (konservativ)
- **Datei:** [master_data_matcher.py:294-321](src/transform/master_data_matcher.py#L294-L321)
  (`_extract_werkstoff_candidates`) — nur **erweitern**.
- **Änderung:** neue `_normalize_werkstoff_format(value)`:
  - Bindestrich `\d-\d{4}` → `\d.\d{4}` (vorhanden, behalten)
  - **5 Ziffern** `\b([12])(\d{4})\b` → `\1.\2` (Punkt verschluckt) — **nur** wenn
    erste Ziffer ∈ {1,2} (DIN-Hauptgruppen Stahl)
  - Suffix `G`/`H`/`+…` nach gültiger Nummer abtrennen (`10116G` → `1.0116` + Zusatz)
- **Risiko (mittel):** `11141` ist mehrdeutig (`1.1141` vs `11.141`). **Mitigation:**
  Konversion nur wenn Ergebnis strukturell `\d.\d{4}` **und** (Katalog-Treffer ODER
  Phase-M3-anerkennbar). Bei Zweifel **keine** Konversion → bleibt gelb.
- **Test:** `test_werkstoff_format_normalization` — Positiv: `10116G`,`11141`,`1-0736`;
  **Negativ (kritisch):** `12.9`, `5.8`, `F156900400`, `912-12.9` dürfen NICHT
  zu einer Werkstoffnummer werden.
- **Akzeptanz:** alle Negativfälle bleiben no_match.

### M3 — Werkstoffnummer-Format anerkennen (der große, sichere Gewinn)
- **Datei:** [master_data_matcher.py:141-156](src/transform/master_data_matcher.py#L141-L156)
  (in `MaterialCatalog.match`, **nach** Fuzzy, **vor** finalem `no_match`).
- **Änderung:**
  ```
  primary = _extract_primary_material_number(cleaned)   # liefert \d.\d{4} oder None
  if primary and not _families_conflict(_material_family_from_text(cleaned), "steel"):
      return MatchResult(primary, 0.92, "werkstoff_nr_format")
  ```
  Confidence **0.92** (bewusst < 1.0 exact) → im Audit unterscheidbar.
- **Green-Gate:** `"master_data:werkstoff_nr_format"` zu `_TEXT_PATH_METHODS`
  ([green_gate.py:13-25](src/scoring/green_gate.py#L13-L25)).
- **Sicherheits-Gating (zentral):**
  1. nur strukturell gültige `\d.\d{4}` (Regex trennt Klasse C empirisch sauber ab),
  2. grün **nur** auf dem Text-Pfad (Wert exakt gelesen — kein OCR-Misread),
  3. `_families_conflict` bleibt aktiv (kein Stahl↔Alu↔Kunststoff).
- **Warum sicher:** GRÜN = „Wert korrekt aus Quelle übernommen", nicht „Material
  im Katalog". Auf dem deterministischen Pfad **ist** der Wert exakt der
  Text-Layer-Inhalt. `1.0037` korrekt zu übernehmen ist legitimes Grün, auch ohne
  Katalog-Eintrag.
- **Risiko (niedrig):** ein im PDF *falsch gedrucktes* `\d.\d{4}` würde grün —
  aber das ist ein Quell-Fehler, nicht unserer (out of scope laut CLAUDE.md:
  „Fehler in den Quell-Stücklisten erkennen" ist NICHT im Scope).
- **Test:** `test_werkstoff_nr_format_recognized_green` (1.0037 → method
  werkstoff_nr_format, grün-fähig auf Text-Pfad) + `test_format_not_green_on_vision`
  (gleicher Wert, Vision-Pfad → nicht grün) + `test_class_c_never_format_matched`
  (DIN912/FKL/Junk → no_match).

### M4 — Messung & Härtung (Pflicht vor Merge)
- **Skript:** `scripts/diag_material_match.py` über **alle 18 POC-Dateien**:
  matched/no_match vorher↔nachher, Liste der **neu-grünen** Werte.
- **Harte Akzeptanz:**
  1. `test_zero_false_positive` + `test_false_green_vision` grün.
  2. **Stichprobe der neu-grünen Werte = 100% echte Werkstoffnummern** (manuell
     gegengeprüft an 2-3 Dateien).
  3. **Kein** Klasse-C-Wert (DIN/FKL/Junk) wird grün.
  4. Material-No-Match sinkt deutlich (Erwartung ZF 272→~115, TCG 114→~25).

---

## 4. Risiko-Register & Schutz des Bestandssystems

| Risiko | Schutz |
|---|---|
| False-Grün durch Format-Anerkennung | strikt `\d.\d{4}` + **nur Text-Pfad** (exakt gelesen) + Familien-Veto |
| Mehrdeutige Normalisierung (`11141`) | konservativ: bei Zweifel keine Konversion → gelb |
| Klasse C (Normteile/Junk) wird grün | Regex schließt sie empirisch aus; Negativtests als Netz |
| Regression bestehender Matches | rein additiv, nur im `no_match`-Zweig; Vertragstests |
| Vision-Misread wird grün | Text-Pfad-Gating (Vision bleibt katalogpflichtig) |
| Quell-PDF-Druckfehler | out of scope (CLAUDE.md); GRÜN = korrekte Übernahme, nicht Quell-Validierung |
| Audit-Intransparenz | eigene Methode `werkstoff_nr_format` + conf 0.92 sichtbar im Trail |

---

## 5. Explizit NICHT im Scope
- Katalog pro Kunde erweitern (Hebel D — gestrichen, s. §0).
- Format-Grün auf dem Vision-Pfad.
- Normteile (DIN/FKL) ins richtige Zielfeld umrouten (separates Mapping-Thema).
- Quell-Stücklisten-Fehler erkennen (CLAUDE.md: nicht im Scope).

---

## 6. Breiterer Fahrplan (Kontext: was nach Material)
1. **Material-Matching** (dieser Plan) — höchster Geschäftswert (Grün-Rate ↑).
2. **Junk-Markierung statt -Löschung** (optional, UX): Nicht-Tabellen-Bänder
   sichtbar kennzeichnen, ohne je zu droppen (Zero-Loss bleibt). Nur falls das
   Dashboard es braucht.
3. **Toten Code löschen** (`parse_pdf_text`), sobald der neue Pfad live bewiesen ist.
4. **Scan-Pfad** (kein Text-Layer) bleibt Vision mit ehrlichem „nicht garantiert" —
   nur relevant, falls ein Kunde gescannte PDFs schickt (alle 18 POC born-digital).

---

## 7. Definition of Done (Material)
- [ ] M1–M3 umgesetzt, rein additiv.
- [ ] Neue Tests grün; `test_zero_false_positive` + `test_false_green_vision` grün.
- [ ] `diag_material_match.py`: Material-No-Match deutlich gesenkt, **0** Klasse-C-Grün.
- [ ] Stichprobe neu-grüner Werte = 100% echte Werkstoffnummern.
- [ ] ruff sauber; ein klar beschriebener Commit pro Phase.
