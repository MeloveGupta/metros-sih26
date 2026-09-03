"""Tests for offline field extraction."""
from __future__ import annotations

from backend.extract.fields import extract_fields, parse_mrp, parse_net_quantity


LABEL = """
Tasty Masala Chips
Manufactured by: FoodCo Pvt Ltd, Plot 12, Pune, Maharashtra 411001
Net Qty: 90 g
MRP Rs. 45.00 (incl. of all taxes)
Mfg: Aug 2026
Consumer care: care@foodco.in, 1800-123-4567
"""


def test_mrp_good_format():
    f = parse_mrp(LABEL)
    assert f.present and f.format_pass is True
    assert "45.00" in f.value


def test_mrp_missing_incl_taxes_flags_format():
    f = parse_mrp("MRP Rs. 20.00")
    assert f.present and f.format_pass is False


def test_net_quantity_unit():
    f = parse_net_quantity("Net Qty: 90 g")
    assert f.present and f.format_pass
    assert "90" in f.value and "g" in f.value.lower()


def test_extract_fields_full_label():
    ids = ["manufacturer", "net_quantity", "mrp", "mfg_date",
           "consumer_care", "country_of_origin"]
    fields = {f.id: f for f in extract_fields(LABEL, ids)}

    assert fields["manufacturer"].present and fields["manufacturer"].format_pass  # has PIN
    assert fields["net_quantity"].present
    assert fields["mrp"].format_pass is True
    assert fields["mfg_date"].present
    assert fields["consumer_care"].present and fields["consumer_care"].format_pass
    # No import cue -> country of origin not applicable.
    assert fields["country_of_origin"].applicable is False


def test_imported_product_country_of_origin_applies():
    text = "Imported by ABC. Country of Origin: China"
    fields = {f.id: f for f in extract_fields(text, ["country_of_origin"])}
    assert fields["country_of_origin"].present
    assert fields["country_of_origin"].applicable is True


def test_missing_fields_not_present():
    fields = {f.id: f for f in extract_fields("just a name", ["mrp", "net_quantity"])}
    assert not fields["mrp"].present
    assert not fields["net_quantity"].present
