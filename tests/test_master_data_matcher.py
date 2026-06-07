from src.transform.master_data_matcher import MaterialCatalog


def test_every_din_name_resolves_to_its_canonical() -> None:
    """M1: every material's DIN name resolves to its canonical, auto-indexed."""
    catalog = MaterialCatalog()
    for material in catalog._materials:
        din = material.get("din_name")
        if not din:
            continue
        result = catalog.match(din)
        assert result.canonical == material["canonical"], (din, result.method)


def test_material_match_prefers_exact_werkstoff_number() -> None:
    catalog = MaterialCatalog()

    result = catalog.match("STAHL DIN 4957- 1.2343 V=1570N/mm2 GNT=0,3 tief")

    assert result.canonical == "1.2343 ESU"
    assert result.confidence == 1.0
    assert result.method in {"werkstoff_nr_extract", "werkstoff_nr_base"}


def test_steel_number_not_in_catalog_resolves_to_itself_not_wrong_material() -> None:
    """M3: a valid DIN steel number absent from the catalog is recognised by format
    AS ITSELF (1.0580) — never mis-mapped to a different (e.g. aluminium) material.

    Replaces the old assertion that this returned no_match: that was the coverage
    gap we are closing. The anti-false-positive intent is preserved — the result is
    the steel number itself, not a wrong-family canonical.
    """
    catalog = MaterialCatalog()

    result = catalog.match("STAHL EN 10305-2- 1.0580 +N")

    assert result.canonical == "1.0580"
    assert result.method == "werkstoff_nr_format"
    assert result.confidence == 0.92


def test_generic_material_text_without_number_stays_no_match() -> None:
    """M3 must NOT fire on free text that is not a DIN number — the format
    recognition only triggers on a structurally valid \\d.\\d{4}."""
    catalog = MaterialCatalog()

    result = catalog.match("Stahl blank")

    assert result.canonical is None
    assert result.method == "no_match"


def test_embedded_werkstoff_number_is_recognised() -> None:
    """M3: the Werkstoffnummer embedded in a norm-prefixed string is extracted."""
    catalog = MaterialCatalog()

    result = catalog.match("STAHLEN 10088-2-1.4301")

    assert result.canonical == "1.4301"
    assert result.method == "werkstoff_nr_format"


def test_class_c_values_never_format_matched() -> None:
    """Norm parts / strength classes / junk must never look like a Werkstoffnummer."""
    catalog = MaterialCatalog()
    for value in ("DIN912-12.9", "FKL 5.8", "F156900400_STL", "DIN 7603", "-"):
        result = catalog.match(value)
        assert result.method != "werkstoff_nr_format", (value, result.method)
        assert result.canonical is None, (value, result.canonical)


def test_m2_standalone_stripped_number_recognised() -> None:
    """M2: a standalone DIN number with the dot swallowed (+ optional suffix)."""
    catalog = MaterialCatalog()
    for value, expected in [("12343", "1.2343"), ("10116G", "1.0116"), ("11141", "1.1141")]:
        result = catalog.match(value)
        assert result.canonical == expected, (value, result.method, result.canonical)
        assert result.method == "werkstoff_nr_format"


def test_m2_never_converts_a_norm_number(  ) -> None:
    """CRITICAL: norm references (EN/DIN/ISO + number) must NEVER become a
    material — converting e.g. EN 10088 to 1.0088 would be a false-green disaster.
    These carry surrounding context, so the anchored standalone rule excludes them.
    """
    catalog = MaterialCatalog()
    for value in (
        "STAHL EN 10088-2-",
        "STAHL EN 10025-2",
        "STAHL EN 10305-1-",
        "DIN 16756",
        "AL.-LEG. ISO 18273-",
        "8 STAHL EN 10305-4",
    ):
        result = catalog.match(value)
        assert result.method != "werkstoff_nr_format", (value, result.method)
        assert result.canonical is None, (value, result.canonical)
