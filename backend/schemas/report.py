"""Canonical report data model (see docs/report-spec.md).

These pydantic models are the single source of truth: the DB stores them, the
API serves them, and the PDF/DOCX renderers read them. Every millimetre value is
a `Measurement` (value + uncertainty), never a bare float.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Literal, Optional, Tuple

from pydantic import BaseModel, Field


class Status(str, Enum):
    """Per-declaration / per-check disposition. Never 'violation'."""

    COMPLIANT = "compliant"
    POTENTIAL_NON_COMPLIANCE = "potential_non_compliance"
    NOT_DETECTED = "not_detected"
    NOT_ASSESSABLE = "not_assessable"
    NOT_APPLICABLE = "not_applicable"
    NEEDS_OFFICER_REVIEW = "needs_officer_review"
    OFFICER_CONFIRMED = "officer_confirmed"
    OFFICER_OVERRIDDEN = "officer_overridden"


class CalibrationVerdict(str, Enum):
    CALIBRATED = "calibrated"
    REJECTED = "rejected_uncalibrated"


class Measurement(BaseModel):
    """A physical measurement with a symmetric uncertainty, in `unit`."""

    value: float
    uncertainty: float = 0.0
    unit: str = "mm"

    def __str__(self) -> str:  # renders as "1.90 ± 0.20 mm"
        return f"{self.value:.2f} ± {self.uncertainty:.2f} {self.unit}"

    @property
    def lower(self) -> float:
        return self.value - self.uncertainty

    @property
    def upper(self) -> float:
        return self.value + self.uncertainty


class ClauseRef(BaseModel):
    clause: str
    source_url: Optional[str] = None
    gazette: Optional[str] = None
    effective_from: Optional[str] = None


class FormatCheck(BaseModel):
    passed: bool
    pattern: Optional[str] = None
    detail: Optional[str] = None


class DeclarationFinding(BaseModel):
    """One row of the Rule 6 findings table."""

    id: str
    label: str
    clause_ref: ClauseRef
    extracted: Optional[str] = None
    bbox: Optional[Tuple[int, int, int, int]] = None  # x, y, w, h
    panel: Optional[str] = None                        # "PDP" | "other"
    ocr_confidence: Optional[float] = None
    format_check: Optional[FormatCheck] = None
    status: Status
    evidence_crop: Optional[str] = None
    note: Optional[str] = None


class TableIBand(BaseModel):
    area_band: str
    min_height_mm: float
    min_height_mm_molded: float


class FontItem(BaseModel):
    """Per-declaration Rule 7 letter-height finding."""

    declaration_id: str
    height_mm: Optional[Measurement] = None
    width_ratio: Optional[float] = None
    width_ratio_char: Optional[str] = None
    threshold_mm: Optional[float] = None
    molded: bool = False
    status: Status
    reason: Optional[str] = None


class PanelInput(BaseModel):
    """How the panel area was derived (Rule 7(4), as substituted by GSR 629(E))."""

    shape: Optional[str] = None  # "rectangular" | "cylindrical" | "other"
    height_cm: Optional[float] = None
    width_cm: Optional[float] = None
    circumference_cm: Optional[float] = None
    area_cm2_other: Optional[float] = None
    clause: str = "Rule 7(4)"


class FontAnalysis(BaseModel):
    panel_area_cm2: Optional[Measurement] = None
    panel_input: Optional[PanelInput] = None
    table_i_band: Optional[TableIBand] = None
    items: List[FontItem] = Field(default_factory=list)


class Calibration(BaseModel):
    model_config = {"populate_by_name": True}

    reference: str = "aruco_card"
    aruco_dict: Optional[str] = Field(default=None, alias="dict")
    marker_id: Optional[int] = None
    marker_mm: Optional[float] = None
    detection_confidence: Optional[float] = None
    mm_per_pixel: Optional[float] = None
    corner_jitter_px: Optional[float] = None
    verdict: CalibrationVerdict = CalibrationVerdict.REJECTED
    reason: Optional[str] = None


class Transformation(BaseModel):
    op: str
    params: dict = Field(default_factory=dict)


class OriginalImage(BaseModel):
    file: str                              # path relative to the uploads dir
    sha256: str                            # hash of the uploaded bytes, not re-encoded pixels
    role: str = "other"                    # "front" | "back" | "other" | "listing"
    captured_at: Optional[datetime] = None
    width: int = 0
    height: int = 0
    exif: dict = Field(default_factory=dict)


class Evidence(BaseModel):
    images: List[OriginalImage] = Field(default_factory=list)
    transformations: List[Transformation] = Field(default_factory=list)
    integrity_note: str = (
        "The SHA-256 hash proves the file is unaltered after capture. It does not "
        "by itself prove the subject or location, and is not a statement of "
        "court-admissibility."
    )

    @property
    def original(self) -> Optional[OriginalImage]:
        """The first uploaded image, for the handful of call sites (repository
        search index, report templates) that only need one representative file."""
        return self.images[0] if self.images else None


class Officer(BaseModel):
    id: str
    name: str
    role: str = "officer"


class Inspection(BaseModel):
    officer: Optional[Officer] = None
    jurisdiction: Optional[str] = None
    geo: Optional[dict] = None
    device: Optional[str] = None


class Product(BaseModel):
    name: Optional[str] = None
    brand: Optional[str] = None
    category: Optional[str] = None
    batch: Optional[str] = None
    source: Optional[str] = None          # "retail_pack" | "ecommerce_listing"
    barcode_text: Optional[str] = None


class Summary(BaseModel):
    checked: int = 0
    compliant: int = 0
    potential_non_compliance: int = 0
    not_detected: int = 0
    not_assessable: int = 0
    not_applicable: int = 0
    compliance_ratio: float = 0.0
    required_actions: List[str] = Field(default_factory=list)


class OfficerAction(BaseModel):
    declaration_id: str
    action: str                            # confirm | override | defer
    reason: Optional[str] = None
    officer_id: Optional[str] = None
    at: Optional[datetime] = None


class RuleCatalogInfo(BaseModel):
    version: str
    hash: Optional[str] = None


class Extraction(BaseModel):
    """How the label text was actually read for this report."""

    backend_used: Literal["llm", "ocr_regex", "label_text"] = "ocr_regex"
    llm_error: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)


LIMITATIONS_TEXT = (
    "Decision-support scope: this report flags POTENTIAL non-compliance for "
    "officer verification and does not make a final legal finding. "
    "'Not detected in the submitted image' is not the same as 'legally absent'. "
    "Millimetre measurement is reliable only for guided, planar captures with a "
    "valid calibration marker; it is degraded for curved, shiny, crumpled, "
    "transparent or steeply angled packages. It also assumes the measured text "
    "lies in the same plane as the calibration card; text noticeably closer to "
    "or further from the camera than the card (out-of-plane offset) is not "
    "detected by a single marker and can bias the result. Letter height and "
    "width-ratio measurement uses individual glyph boxes only for digits and "
    "uppercase letters, which have an unambiguous cap height; lowercase "
    "x-height (ascenders/descenders) is not used, so a lowercase-only "
    "declaration falls back to a whole-word measurement. Contrast, blur and "
    "glare thresholds (Rule 9 readability) are heuristics chosen for this "
    "tool, not figures given in the Rules themselves. Authorised physical "
    "measurement remains necessary for any enforcement action."
)


class Report(BaseModel):
    """The complete compliance report."""

    report_id: str
    ref_no: Optional[str] = None
    generated_at: datetime
    app_version: str = "0.1.0"
    rule_catalog: RuleCatalogInfo
    disposition: Status = Status.POTENTIAL_NON_COMPLIANCE
    inspection: Inspection = Field(default_factory=Inspection)
    product: Product = Field(default_factory=Product)
    evidence: Evidence
    calibration: Calibration = Field(default_factory=Calibration)
    extraction: Extraction = Field(default_factory=Extraction)
    summary: Summary = Field(default_factory=Summary)
    declarations: List[DeclarationFinding] = Field(default_factory=list)
    placement: List[DeclarationFinding] = Field(default_factory=list)
    readability: List[DeclarationFinding] = Field(default_factory=list)
    font_analysis: FontAnalysis = Field(default_factory=FontAnalysis)
    legal_basis: dict = Field(default_factory=dict)
    officer_actions: List[OfficerAction] = Field(default_factory=list)
    finalized_at: Optional[datetime] = None
    finalized_by: Optional[str] = None
    limitations: str = LIMITATIONS_TEXT
