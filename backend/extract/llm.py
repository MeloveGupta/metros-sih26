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
VISION_MODEL = os.environ.get("METROS_LLM_VISION_MODEL", "claude-sonnet-5")

_SYSTEM = (
    "You extract mandatory declarations from an Indian packaged-commodity label "
    "for a Legal Metrology compliance tool. You ONLY detect and transcribe what "
    "is present; you never decide legal compliance. For each requested "
    "declaration report: present (was it found), value (verbatim text or null), "
    "format_pass (true/false only where a format is prescribed, else null), and "
    "applicable (false when the rule does not apply to this product, e.g. country "
    "of origin for a non-imported item, or best-before for a non-perishable). "
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


def _client():
    """Zero-arg Anthropic client. Raises ExtractionError if unusable."""
    try:
        import anthropic
    except Exception as exc:  # ImportError
        raise ExtractionError(
            "LLM extraction needs the `anthropic` SDK (pip install anthropic). "
            f"Underlying error: {exc}"
        ) from exc
    try:
        # No api_key argument: credentials come from `ant auth login` (OAuth
        # profile), or ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY if set.
        return anthropic.Anthropic()
    except Exception as exc:
        raise ExtractionError(
            "Could not construct the Anthropic client. Run `ant auth login` to "
            f"sign in (no API key required). Underlying error: {exc}"
        ) from exc


def llm_available() -> bool:
    """True if the SDK imports and a credential source is resolvable."""
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


def _create(client, model: str, content):
    try:
        return client.messages.create(
            model=model,
            max_tokens=2000,
            system=_SYSTEM,
            output_config={"format": {"type": "json_schema", "schema": _RESPONSE_SCHEMA}},
            messages=[{"role": "user", "content": content}],
        )
    except Exception as exc:
        raise ExtractionError(f"LLM request failed: {exc}") from exc


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


def _encode_jpeg(image) -> str:
    import cv2
    ok, buf = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    if not ok:
        raise ExtractionError("could not encode image for the vision model")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def extract_fields_from_image(
    image,
    catalog: RuleCatalog,
    declaration_ids: List[str],
    model: str = VISION_MODEL,
) -> List[FieldExtraction]:
    """Read the label directly from a BGR image via Claude vision (no OCR needed)."""
    client = _client()
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                      "data": _encode_jpeg(image)}},
        {"type": "text", "text": _declarations_block(catalog, declaration_ids)
         + "\n\nRead the packaged-product label in the image and extract the "
           "declarations. Transcribe values verbatim from the label."},
    ]
    return _parse_response(_create(client, model, content), declaration_ids)
