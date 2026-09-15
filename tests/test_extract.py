"""Tests for offline field extraction."""
from __future__ import annotations

from backend.extract.fields import (
    extract_fields,
    parse_common_name,
    parse_consumer_care,
    parse_manufacturer,
    parse_mrp,
    parse_net_quantity,
    parse_unit_sale_price,
)


LABEL = """
Tasty Masala Chips
Manufactured by: FoodCo Pvt Ltd, Plot 12, Pune, Maharashtra 411001
Net Qty: 90 g
MRP Rs. 45.00 (incl. of all taxes)
Mfg: Aug 2026
Consumer care: FoodCo Care, 12 MG Road, Pune 411001, care@foodco.in, 1800-123-4567
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


def test_common_name_found_with_hint():
    f = parse_common_name(LABEL, hint="Tasty Masala Chips")
    assert f.present and f.value == "Tasty Masala Chips"
    assert f.needs_confirmation is False


def test_common_name_not_found_with_wrong_hint():
    f = parse_common_name(LABEL, hint="Chocolate Bar")
    assert f.present is False
    assert f.needs_confirmation is False  # not_detected, not unconfirmed


def test_common_name_without_hint_needs_confirmation():
    f = parse_common_name(LABEL)
    assert f.present is False
    assert f.needs_confirmation is True
    assert f.confirmation_reason == "generic name needs officer confirmation"


# --- 1.6: regex false positives / false passes ---

def test_net_quantity_ignores_nutrition_facts_figure():
    """"Protein 12 g per serving" must never be read as the net-quantity
    declaration -- it's a nutrition-facts figure, not Rule 6(1)(c)."""
    text = "Nutrition Facts\nProtein 12 g per serving\nFat 5 g\nEnergy 250 kcal"
    f = parse_net_quantity(text)
    assert f.present is False


def test_net_quantity_uncued_number_is_low_confidence_candidate():
    text = "Contains 250 g of real fruit"  # a number+unit, but no net-qty cue
    f = parse_net_quantity(text)
    assert f.present is True
    assert f.format_pass is False
    assert "no net-quantity cue" in f.format_detail


def test_consumer_care_phone_only_fails_and_lists_missing_parts():
    text = "Consumer care: 1800-123-4567"
    f = parse_consumer_care(text)
    assert f.present is True
    assert f.format_pass is False
    assert "name/address" in f.format_detail
    assert "e-mail" in f.format_detail
    assert "telephone" not in f.format_detail  # phone WAS given


def test_manufacturer_pin_scoped_to_manufacturer_block():
    """A PIN code in a distant consumer-care block must not make an
    address-less manufacturer line look compliant."""
    text = (
        "Manufactured by: FoodCo Pvt Ltd, Pune\n"
        + ("filler line to push the PIN below out of the manufacturer window\n" * 10)
        + "Consumer care: FoodCo Care, 12 MG Road, Pune 411001, care@foodco.in, 1800-123-4567"
    )
    f = parse_manufacturer(text)
    assert f.present is True
    assert f.format_pass is False
    assert "no PIN code" in f.format_detail


# --- 2.1: unit sale price, Rule 6(11) ---

def test_unit_sale_price_present_correct():
    text = "Net Qty 500 g\nMRP Rs. 45.00 (incl. of all taxes)\nUnit sale price: Rs. 0.09 per g"
    f = parse_unit_sale_price(text)
    assert f.present is True
    assert f.applicable is True
    assert f.format_pass is True


def test_unit_sale_price_wrong_unit_basis():
    # Net qty is under 1kg, so this should be declared per gram, not per kg.
    text = "Net Qty 500 g\nMRP Rs. 45.00 (incl. of all taxes)\nUnit sale price: Rs. 90.00 per kg"
    f = parse_unit_sale_price(text)
    assert f.present is True
    assert f.format_pass is False
    assert "per gram" in f.format_detail


def test_unit_sale_price_arithmetic_mismatch():
    # Correct basis (per gram) but the value doesn't match MRP / net quantity.
    text = "Net Qty 500 g\nMRP Rs. 45.00 (incl. of all taxes)\nUnit sale price: Rs. 0.50 per g"
    f = parse_unit_sale_price(text)
    assert f.present is True
    assert f.format_pass is False
    assert "0.09" in f.format_detail  # the correct computed figure


def test_unit_sale_price_missing():
    text = "Net Qty 500 g\nMRP Rs. 45.00 (incl. of all taxes)"
    f = parse_unit_sale_price(text)
    assert f.present is False
    assert f.applicable is True


def test_unit_sale_price_not_applicable_single_number_item():
    text = "Net Qty 1 N\nMRP Rs. 10.00 (incl. of all taxes)"
    f = parse_unit_sale_price(text)
    assert f.applicable is False


def test_unit_sale_price_not_applicable_when_equal_to_mrp():
    text = "Net Qty 1 kg\nMRP Rs. 200.00 (incl. of all taxes)\nUnit sale price: Rs. 200.00 per kg"
    f = parse_unit_sale_price(text)
    assert f.present is True
    assert f.applicable is False


def test_mrp_incl_taxes_scoped_to_mrp_line():
    """"inclusive of all taxes" printed far from the MRP figure must not make
    the MRP declaration pass."""
    text = (
        "MRP Rs. 45.00\n"
        + ("some unrelated marketing copy on the pack\n" * 10)
        + "All our products are sold inclusive of all taxes as a company policy."
    )
    f = parse_mrp(text)
    assert f.present is True
    assert f.format_pass is False
