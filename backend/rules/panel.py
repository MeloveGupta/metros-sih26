"""Principal display panel area, Rule 7(4) (as substituted by GSR 629(E)).

Automatic panel-area detection is out of scope (a human must say what shape
the package is); the officer enters dimensions and this computes the area the
Rule 7 Table-I band is selected from.
"""
from __future__ import annotations

from typing import Optional

PANEL_SHAPES = ("rectangular", "cylindrical", "other")


def compute_panel_area_cm2(
    shape: str,
    height_cm: Optional[float] = None,
    width_cm: Optional[float] = None,
    circumference_cm: Optional[float] = None,
    area_cm2_other: Optional[float] = None,
) -> float:
    """Compute the principal-display-panel area (cm^2) per Rule 7(4).

    - rectangular: height x width of the principal display panel side.
    - cylindrical: 40% of (height x circumference).
    - other: the officer-entered area directly (the rule's "an area
      considered to be a principal display panel" alternative).
    """
    if shape == "rectangular":
        if height_cm is None or width_cm is None:
            raise ValueError("rectangular panel needs height_cm and width_cm")
        return height_cm * width_cm
    if shape == "cylindrical":
        if height_cm is None or circumference_cm is None:
            raise ValueError("cylindrical panel needs height_cm and circumference_cm")
        return 0.4 * height_cm * circumference_cm
    if shape == "other":
        if area_cm2_other is None:
            raise ValueError("'other' panel shape needs area_cm2_other")
        return area_cm2_other
    raise ValueError(f"unknown panel_shape {shape!r}; must be one of {PANEL_SHAPES}")
