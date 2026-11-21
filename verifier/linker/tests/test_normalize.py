from datetime import datetime

import pytest

from verifier.linker.normalize import (
    SupplierResolver,
    clean,
    parse_date,
    parse_number,
    po_line,
    po_line_state,
    split_list,
    to_utc_iso,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("00010", "1"),
        ("00020", "2"),
        ("00510", "51"),
        ("1", "1"),
        ("10", "10"),
        (" 3 ", "3"),
        (None, None),
        (float("nan"), None),
        ("", None),
    ],
)
def test_po_line(raw, expected):
    assert po_line(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("a|b", ["a", "b"]),
        ("a, b", ["a", "b"]),
        ("a", ["a"]),
        ("a | b|", ["a", "b"]),
        (None, []),
        ("", []),
    ],
)
def test_split_list(raw, expected):
    assert split_list(raw) == expected


def test_parse_number():
    assert parse_number("1,5200", decimal_comma=True) == 1.52
    assert parse_number("1.234,5", decimal_comma=True) == 1234.5
    assert parse_number("44,000") == 44000
    assert parse_number("-23000") == -23000
    assert parse_number("n/a") is None
    assert parse_number(None) is None


def test_parse_date():
    assert parse_date("2026-11-20") == "2026-11-20"
    assert parse_date("08/12/2024", ("%m/%d/%Y",)) == "2024-08-12"
    assert parse_date("08/12/2024") is None  # US format only when the source says so
    assert parse_date(datetime(2026, 1, 2, 10, 0)) == "2026-01-02"
    assert parse_date("") is None


def test_clean():
    assert clean("  x ") == "x"
    assert clean("   ") is None
    assert clean(float("nan")) is None


def test_supplier_resolver_learns_every_name():
    resolver = SupplierResolver(
        [
            {
                "supplier_id": "SUP-0019",
                "name": "Dongguan Precision Housings",
                "legal_name": "Dongguan Precision Housings Co., Ltd.",
            },
            {"supplier_id": "SUP-0008", "name": "Beta Works", "vendor_no": "108"},
        ]
    )
    for spelling in (
        "SUP-0019",
        "19",
        "0019",
        "dongguan precision housings",
        "Dongguan Precision Housings Co., Ltd.",
        "SUP-0019_Dongguan_Precision_Housings",
    ):
        assert resolver.resolve(spelling) == "SUP-0019", spelling
    assert resolver.resolve("108") == "SUP-0008"  # vendor number from the JSON export
    assert resolver.resolve("Unknown GmbH") is None
    assert resolver.resolve("99") is None
    assert resolver.resolve(None) is None


def test_po_line_state_comes_from_facts():
    assert po_line_state(-100.0, None) == "cancellation"
    assert po_line_state(-100.0, "2026-01-01") == "cancellation"
    assert po_line_state(500.0, "2026-01-01") == "delivered"
    assert po_line_state(500.0, None) == "open"
    assert po_line_state(None, None) == "open"


def test_to_utc_iso_puts_every_format_in_one_form():
    assert to_utc_iso("2026-11-20") == "2026-11-20T00:00:00Z"
    assert to_utc_iso("2026-11-20T10:00:00+01:00") == "2026-11-20T09:00:00Z"
    assert to_utc_iso("2026-11-21T17:12:00") == "2026-11-21T17:12:00Z"  # naive: read as UTC
    assert to_utc_iso("2026-11-21T16:12:00Z") == "2026-11-21T16:12:00Z"
    assert to_utc_iso("yesterday") is None
    assert to_utc_iso(None) is None


def test_count_number_reads_thousands_from_the_raw_text():
    from verifier.linker.normalize import count_number

    assert count_number("quantity_delivered", "19.000", 19) == 19000
    assert count_number("quantity_delivered", "14.600", 14.6) == 14600
    assert count_number("quantity_delivered", "8 000", 8000) == 8000
    assert count_number("quantity_ordered", "48,000", 48000) == 48000
    assert count_number("quantity_delivered", "5.000 PCS", 5) == 5000
    assert count_number("quantity_delivered", "12", 12) == 12
    assert count_number("unit_price", "1.520", 1.52) == 1.52  # prices are not counts
    assert count_number("weight", "1.500 kg", 1.5) == 1.5
