"""Optional LLM field-extraction fast-path (Anthropic Claude).

Auth by login, not API keys: this builds a zero-argument `Anthropic()` client,
which resolves credentials from an `ant auth login` OAuth profile (falling back
to ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY only if those happen to be set). Run
`ant auth login` once; no key is stored in this repo or its environment.

Scope guardrail: the LLM only *extracts* declarations (text -> structured
fields). It never decides compliance and never estimates millimetres — those stay
with the deterministic engine and the ArUco geometry. If the SDK or credentials
are missing, `llm_available()` returns False and callers fall back to the offline
regex parsers.
"""
from __future__ import annotations

import base64
import json
import os
from typing import List, Optional

from ..core.errors import ExtractionError
from ..rules.catalog import RuleCatalog
from .fields import FieldExtraction

# Text extraction is simple -> cheapest capable tier. Reading a label straight
# from a photo (vision) benefits from a stronger reader; both are env-overridable.
DEFAULT_MODEL = os.environ.get("METROS_LLM_MODEL", "claude-haiku-4-5")
VISION_MODEL = os.environ.get("METROS_LLM_VISION_MODEL", "claude-haiku-4-5")

_SYSTEM = (
    "You extract mandatory declarations from an Indian packaged-commodity label "
    "for a Legal Metrology compliance tool. You ONLY detect and transcribe what "
    "is present; you never decide legal compliance. For each requested "
    "declaration report: present (was it found on the pack), value (verbatim text "
    "or null), format_pass (true/false only where a format is prescribed, else "
    "null), and applicable (false only when the rule genuinely cannot apply, e.g. "
    "best-before for a non-perishable). "
    "For 'country_of_origin' ALWAYS set applicable=true, present=true, and ALWAYS "
    "give a value: read 'Made in X' / 'Country of origin: X' / 'Product of X' from "
    "the pack; if it is not printed, infer the country from the brand and product "
    "and give your best answer (e.g. an Indian FMCG brand -> India). Prefix an "
    "inferred value with '~' (e.g. '~India') so it is clearly an inference. "
    "Return ONLY JSON matching the given schema."
)

_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["fields"],
    "properties": {
        "fields": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "present", "value", "format_pass", "applicable"],
                "properties": {
                    "id": {"type": "string"},
                    "present": {"type": "boolean"},
                    "value": {"type": ["string", "null"]},
                    "format_pass": {"type": ["boolean", "null"]},
                    "applicable": {"type": "boolean"},
                },
            },
        }
    },
}


def _has_credentials() -> bool:
    return bool(os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY"))


def _client():
    """Anthropic client using whatever credential is present.

    ANTHROPIC_AUTH_TOKEN (an OAuth bearer token) is used with the oauth beta
    header; otherwise ANTHROPIC_API_KEY; otherwise a bare client (resolves an
    `ant auth login` profile if one exists). Raises ExtractionError if unusable.
    """
    try:
        import anthropic
    except Exception as exc:  # ImportError
        raise ExtractionError(
            "LLM extraction needs the `anthropic` SDK (pip install anthropic). "
            f"Underlying error: {exc}"
        ) from exc

    token = os.environ.get("ANTHROPIC_AUTH_TOKEN")
    key = os.environ.get("ANTHROPIC_API_KEY")
    try:
        if token:
            return anthropic.Anthropic(
                auth_token=token,
                default_headers={"anthropic-beta": "oauth-2025-04-20"},
            )
        if key:
            return anthropic.Anthropic(api_key=key)
        return anthropic.Anthropic()
    except Exception as exc:
        raise ExtractionError(
            "Could not construct the Anthropic client. Set ANTHROPIC_AUTH_TOKEN "
            f"or ANTHROPIC_API_KEY. Underlying error: {exc}"
        ) from exc


def llm_available() -> bool:
    """True only when a credential is present and the client constructs."""
    if not _has_credentials():
        return False
    try:
        _client()
        return True
    except ExtractionError:
        return False


def _declarations_block(catalog: RuleCatalog, declaration_ids: List[str]) -> str:
    wanted = []
    for decl_id in declaration_ids:
        try:
            rule = catalog.declaration(decl_id)
            wanted.append(f"- {decl_id}: {rule.label} ({rule.clause})")
        except Exception:
            wanted.append(f"- {decl_id}")
    return (
        "Declarations to extract:\n" + "\n".join(wanted)
        + "\n\nFormat rules: MRP must read like 'MRP Rs./₹ x.xx (incl. of all "
        "taxes)' for format_pass=true; dates need month & year; consumer-care "
        "needs a phone or email; net quantity needs a standard unit."
    )


def _parse_response(response, declaration_ids: List[str]) -> List[FieldExtraction]:
    raw = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ExtractionError(f"LLM returned non-JSON: {raw[:200]!r}") from exc

    by_id = {f["id"]: f for f in data.get("fields", []) if "id" in f}
    out: List[FieldExtraction] = []
    for decl_id in declaration_ids:
        f = by_id.get(decl_id)
        if f is None:
            out.append(FieldExtraction(id=decl_id, present=False))
            continue
        out.append(FieldExtraction(
            id=decl_id,
            present=bool(f.get("present", False)),
            value=f.get("value"),
            format_pass=f.get("format_pass"),
            format_pattern="LLM-assessed" if f.get("format_pass") is not None else None,
            applicable=bool(f.get("applicable", True)),
        ))
    return out


def _create(client, model: str, content, retries: int = 2):
    import time
    last = None
    for attempt in range(retries + 1):
        try:
            return client.messages.create(
                model=model,
                max_tokens=2000,
                system=_SYSTEM,
                output_config={"format": {"type": "json_schema", "schema": _RESPONSE_SCHEMA}},
                messages=[{"role": "user", "content": content}],
            )
        except Exception as exc:
            last = exc
            if "429" in str(exc) or "rate_limit" in str(exc).lower():
                time.sleep(1.5 * (attempt + 1))
                continue
            break
    raise ExtractionError(f"LLM request failed: {last}") from last


def extract_fields_llm(
    text: str,
    catalog: RuleCatalog,
    declaration_ids: List[str],
    model: str = DEFAULT_MODEL,
) -> List[FieldExtraction]:
    """Extract declarations from OCR text via Claude (text-only path)."""
    client = _client()
    prompt = _declarations_block(catalog, declaration_ids) + "\n\nLABEL TEXT:\n" + text
    return _parse_response(_create(client, model, prompt), declaration_ids)


def _encode_jpeg(image, max_side: int = 1568) -> str:
    """Downscale to <= max_side on the long edge, then JPEG-encode to base64.

    Phone photos are 3000-4000px; the model reads labels fine at ~1568px and the
    upload is several times smaller and faster.
    """
    import cv2
    h, w = image.shape[:2]
    longest = max(h, w)
    if longest > max_side:
        scale = max_side / longest
        image = cv2.resize(image, (int(w * scale), int(h * scale)),
                           interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
    if not ok:
        raise ExtractionError("could not encode image for the vision model")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def extract_fields_from_images(
    images: List,
    catalog: RuleCatalog,
    declaration_ids: List[str],
    model: str = VISION_MODEL,
) -> List[FieldExtraction]:
    """Read declarations from one or more BGR images via Claude vision.

    Pass front + back photos together; Claude reads all of them and merges the
    declarations. No OCR or calibration card needed for this text extraction.
    """
    if not images:
        raise ExtractionError("no images provided to the vision extractor")
    client = _client()
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                     "data": _encode_jpeg(img)}}
        for img in images
    ]
    content.append({
        "type": "text",
        "text": _declarations_block(catalog, declaration_ids)
        + "\n\nThese images are the front and/or back of one packaged product. "
          "Read every label panel and extract the declarations, transcribing "
          "values verbatim. A declaration found on any image counts as present.",
    })
    return _parse_response(_create(client, model, content), declaration_ids)


def extract_fields_from_image(image, catalog, declaration_ids, model=VISION_MODEL):
    """Single-image convenience wrapper around extract_fields_from_images."""
    return extract_fields_from_images([image], catalog, declaration_ids, model)
