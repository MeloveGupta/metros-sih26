"""Physical geometry of the Metros calibration card.

Single source of truth for both the card generator
(`scripts/gen_calibration_card.py`) and the pipeline's card-text exclusion
(`pipeline._font_from_tokens`), so the two can't drift apart. The card is an
ID-1 / CR80 rectangle (ISO/IEC 7810) with the marker left-justified after a
quiet zone and vertically centred.
"""
from __future__ import annotations

from typing import List, Tuple

# ID-1 / CR80 card, ISO/IEC 7810.
CARD_W_MM = 85.60
CARD_H_MM = 53.98

# Quiet zone before the marker (left edge of the card to the marker's left edge).
QUIET_ZONE_MM = 6.0

# Default printed marker side length; kept in sync with the card generator's
# own --marker-mm default and core.config.Settings.marker_size_mm's default.
DEFAULT_MARKER_MM = 40.0


def card_outline_mm(marker_mm: float) -> List[Tuple[float, float]]:
    """Card outline (TL, TR, BR, BL) in mm, in the marker's own coordinate frame.

    Origin = the marker's top-left corner, x right, y down (image convention).
    The marker sits left-justified after a `QUIET_ZONE_MM` quiet zone and is
    vertically centred on the card, so:
        x ranges over [-QUIET_ZONE_MM, CARD_W_MM - QUIET_ZONE_MM]
        y ranges over [-(CARD_H_MM - marker_mm)/2, marker_mm + (CARD_H_MM - marker_mm)/2]
    """
    x0 = -QUIET_ZONE_MM
    x1 = CARD_W_MM - QUIET_ZONE_MM
    dy = (CARD_H_MM - marker_mm) / 2.0
    y0 = -dy
    y1 = marker_mm + dy
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
