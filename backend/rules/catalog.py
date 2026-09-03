"""Load and validate the versioned YAML rule catalog.

The catalog is data, not code: clause numbers, source provenance, and Rule 7
thresholds live in `rules/lmpc-2011.yaml`. This module parses it into typed
objects and computes a content hash so every report can cite exactly which
catalog version produced it.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import yaml

from ..core.config import get_settings
from ..core.errors import RuleCatalogError


@dataclass
class DeclarationRule:
    id: str
    label: str
    clause: str
    checks: List[str]
    source_url: Optional[str] = None
    gazette: Optional[str] = None
    effective_from: Optional[str] = None


@dataclass
class FontBand:
    area_cm2_lt: Optional[float]        # upper bound (exclusive); None = open top
    min_height_mm: float
    min_height_mm_molded: float


@dataclass
class RuleCatalog:
    version: str
    hash: str
    declarations: List[DeclarationRule]
    font_bands: List[FontBand]
    font_clause: str
    font_absolute: dict = field(default_factory=dict)
    statute: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    def declaration(self, decl_id: str) -> DeclarationRule:
        for d in self.declarations:
            if d.id == decl_id:
                return d
        raise RuleCatalogError(f"no declaration rule with id {decl_id!r}")

    def select_band(self, area_cm2: float) -> FontBand:
        """Pick the Rule 7 Table-I band for a principal-display-panel area."""
        for band in self.font_bands:
            if band.area_cm2_lt is None or area_cm2 < band.area_cm2_lt:
                return band
        # Open-top band should always match; guard anyway.
        return self.font_bands[-1]


def _require(mapping: dict, key: str, ctx: str):
    if key not in mapping:
        raise RuleCatalogError(f"missing {key!r} in {ctx}")
    return mapping[key]


def load_catalog(path: Optional[Path] = None) -> RuleCatalog:
    """Parse the YAML catalog at `path` (defaults to settings.rule_catalog_path)."""
    path = path or get_settings().rule_catalog_path
    if not path.exists():
        raise RuleCatalogError(f"rule catalog not found: {path}")

    raw_bytes = path.read_bytes()
    catalog_hash = "sha256:" + hashlib.sha256(raw_bytes).hexdigest()
    try:
        data = yaml.safe_load(raw_bytes)
    except yaml.YAMLError as exc:
        raise RuleCatalogError(f"malformed YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuleCatalogError(f"catalog root must be a mapping, got {type(data).__name__}")

    meta = data.get("meta", {}) or {}
    default_source = meta.get("source_url")

    declarations: List[DeclarationRule] = []
    for entry in _require(data, "declarations", "catalog"):
        declarations.append(
            DeclarationRule(
                id=_require(entry, "id", "declaration"),
                label=_require(entry, "label", "declaration"),
                clause=_require(entry, "clause", "declaration"),
                checks=list(entry.get("checks", [])),
                source_url=entry.get("source_url", default_source),
                gazette=entry.get("gazette"),
                effective_from=entry.get("effective_from"),
            )
        )
    if not declarations:
        raise RuleCatalogError("catalog has no declarations")

    font = _require(data, "font_height_mm", "catalog")
    bands: List[FontBand] = []
    for b in _require(font, "bands", "font_height_mm"):
        bands.append(
            FontBand(
                area_cm2_lt=b.get("area_cm2_lt"),
                min_height_mm=float(_require(b, "min_height_mm", "font band")),
                min_height_mm_molded=float(
                    b.get("min_height_mm_molded", b["min_height_mm"])
                ),
            )
        )
    if not bands:
        raise RuleCatalogError("font_height_mm has no bands")
    # Sanity: exactly one open-top band, and it must be last.
    open_top = [i for i, b in enumerate(bands) if b.area_cm2_lt is None]
    if len(open_top) != 1 or open_top[0] != len(bands) - 1:
        raise RuleCatalogError("font bands must end with exactly one open-top (null) band")

    return RuleCatalog(
        version=str(data.get("version", "unknown")),
        hash=catalog_hash,
        declarations=declarations,
        font_bands=bands,
        font_clause=str(font.get("clause", "Rule 7")),
        font_absolute=data.get("font_absolute", {}) or {},
        statute=data.get("statute", {}) or {},
        meta=meta,
    )
