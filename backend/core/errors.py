"""Typed errors for the MetroScan pipeline.

Every failure path raises one of these with context, so callers can distinguish
"the image could not be calibrated" from "the rule catalog is malformed" and
surface the right message to an officer.
"""
from __future__ import annotations


class MetroScanError(Exception):
    """Base class for all MetroScan errors."""


class CalibrationError(MetroScanError):
    """Scale reference could not be recovered from the image.

    Raised when no calibration marker is found, or when its geometry is too
    degraded to yield a trustworthy mm-per-pixel. The pipeline treats this as
    'uncalibrated' -> no millimetre verdicts are produced.
    """


class MeasurementError(MetroScanError):
    """A geometric measurement could not be completed."""


class OcrError(MetroScanError):
    """The OCR backend failed or is unavailable."""


class RuleCatalogError(MetroScanError):
    """The YAML rule catalog is missing, malformed, or internally inconsistent."""


class ExtractionError(MetroScanError):
    """A declaration field could not be parsed from OCR text."""
