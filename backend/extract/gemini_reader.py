"""Optional vision field-extraction fast-path (Google Gemini, free tier).

Auth is by API key only: set `GEMINI_API_KEY` (from Google AI Studio) in the
environment or `.env`. No other credential source is used, and the key is
never logged or included in any response.

Scope guardrail: Gemini only *extracts* declarations (photo -> structured
fields). It never decides compliance and never estimates millimetres -- those
stay with the deterministic engine and the ArUco geometry. If the SDK or the
key are missing, `gemini_available()` returns False and callers fall back to
Tesseract OCR + the offline regex parsers.

Uses the `google-genai` SDK's Interactions API (`client.interactions.create`),
confirmed against both ai.google.dev's docs and the installed SDK's own type
stubs (google-genai 2.24.0) -- notably the *older* "Generate Content API"
(`client.models.generate_content`) is a different, legacy surface. There is no
temperature control in this API at all (no such field in
`generation_config`); determinism instead comes from the JSON-schema-
constrained output plus a low `thinking_level`.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from ..core.config import get_settings
from ..core.errors import ExtractionError
from ..rules.catalog import RuleCatalog
from .fields import FieldExtraction

# The installed google-genai SDK's own model literal (google/genai/_gaos/types/
# interactions/model.py) is the ground truth here, not just the docs site:
# "gemini-3.5-flash-lite" does NOT exist there -- the flash-lite line currently
# tops out at "gemini-3.1-flash-lite" (plus the "gemini-flash-lite-latest"
# alias). "gemini-3.8-flash" IS present. Both env-overridable regardless.
DEFAULT_MODEL = os.environ.get("METROS_GEMINI_MODEL", "gemini-3.1-flash-lite")
FALLBACK_MODEL = os.environ.get("METROS_GEMINI_FALLBACK_MODEL", "gemini-3.8-flash")

_MAX_OUTPUT_TOKENS = 2000
_MAX_SIDE_PX = 1600

_SYSTEM = (
    "You extract mandatory declarations from an Indian packaged-commodity label "
    "for a Legal Metrology compliance tool. You ONLY detect and transcribe what "
    "is present; you never decide legal compliance, and you NEVER infer or guess "
    "a value that is not actually printed on the pack -- if you cannot read it, "
    "report present=false, not a guess. For each requested declaration report: "
    "present (was it found on the pack), value (verbatim text or null -- never "
    "inferred, estimated, or completed from context), format_pass (true/false "
    "only where a format is prescribed, else null), and applicable (false only "
    "when the rule genuinely cannot apply, e.g. best-before for a non-perishable). "
    "For 'country_of_origin': it applies only to imported products (an "
    "'imported by' / 'importer' notice, or a printed 'country of origin' / "
    "'made in' declaration naming a country other than India). If none of that "
    "is printed, set applicable=false and present=false -- do not infer a "
    "country from the brand or product name. If a country IS printed, set "
    "present=true and value to exactly what is printed, verbatim. "
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
    return bool(os.environ.get("GEMINI_API_KEY"))


def _client():
    """Gemini client authenticated with `GEMINI_API_KEY` only.

    Raises ExtractionError if the SDK is missing or the key is unset at
    construction time (a rejected key only surfaces later, on the actual API
    call -- see `dispatch.extract_declarations`'s OCR fallback).
    """
    try:
        from google import genai
    except Exception as exc:  # ImportError
        raise ExtractionError(
            "Gemini extraction needs the `google-genai` SDK (pip install "
            f"google-genai). Underlying error: {exc}"
        ) from exc

    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise ExtractionError("GEMINI_API_KEY is not set.")
    try:
        return genai.Client(api_key=key)
    except Exception as exc:
        raise ExtractionError(
            f"Could not construct the Gemini client. Underlying error: {exc}"
        ) from exc


def gemini_available() -> bool:
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
        "needs a name/address, telephone number AND e-mail -- Rule 6(2) makes "
        "all four mandatory, not just one; net quantity needs a standard unit."
    )


def _parse_response(interaction, declaration_ids: List[str]) -> List[FieldExtraction]:
    raw = interaction.output_text or ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ExtractionError(f"Gemini returned non-JSON: {raw[:200]!r}") from exc

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
            format_pattern="Gemini-assessed" if f.get("format_pass") is not None else None,
            applicable=bool(f.get("applicable", True)),
        ))
    return out


def _is_quota_error(exc: Exception) -> bool:
    from google.genai import errors as genai_errors
    if isinstance(exc, genai_errors.APIError):
        return exc.code == 429 or "RESOURCE_EXHAUSTED" in str(exc.status or "")
    return "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc)


def _interactions_create(client, model: str, input_content):
    return client.interactions.create(
        model=model,
        input=input_content,
        response_format={
            "type": "text",
            "mime_type": "application/json",
            "schema": _RESPONSE_SCHEMA,
        },
        generation_config={
            "thinking_level": "minimal",
            "max_output_tokens": _MAX_OUTPUT_TOKENS,
        },
        system_instruction=_SYSTEM,
    )


def _create(client, model: str, input_content, allow_retry: bool):
    """One `interactions.create` call. On a quota/rate error, and only if
    `allow_retry`, backs off once with jitter and retries; any other failure
    (or a second quota error) raises immediately -- the caller decides whether
    to try the fallback model."""
    try:
        return _interactions_create(client, model, input_content)
    except Exception as exc:
        if allow_retry and _is_quota_error(exc):
            time.sleep(1.5 + random.uniform(0, 1))
            return _interactions_create(client, model, input_content)
        raise


def _call_with_model_fallback(client, input_content) -> Tuple[object, str]:
    """Try DEFAULT_MODEL (with one jittered retry on quota errors); on a
    quota-shaped failure, try FALLBACK_MODEL once (no retry -- a second
    jittered attempt on the same tier is pointless). Raises ExtractionError
    if both fail."""
    try:
        return _create(client, DEFAULT_MODEL, input_content, allow_retry=True), DEFAULT_MODEL
    except Exception as first_exc:
        if not _is_quota_error(first_exc):
            raise ExtractionError(f"Gemini request failed: {first_exc}") from first_exc
        try:
            return _create(client, FALLBACK_MODEL, input_content, allow_retry=False), FALLBACK_MODEL
        except Exception as second_exc:
            raise ExtractionError(
                "Gemini free-tier quota reached on both "
                f"{DEFAULT_MODEL} and {FALLBACK_MODEL}: {second_exc}"
            ) from second_exc


@dataclass
class GeminiCallResult:
    fields: List[FieldExtraction]
    model_used: str
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    thought_tokens: Optional[int] = None


def _usage_from(interaction) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    usage = getattr(interaction, "usage", None)
    if usage is None:
        return None, None, None
    return (
        getattr(usage, "total_input_tokens", None),
        getattr(usage, "total_output_tokens", None),
        getattr(usage, "total_thought_tokens", None),
    )


def _encode_jpeg_stripped(image, max_side: int = _MAX_SIDE_PX) -> str:
    """Downscale to <= max_side on the long edge, then JPEG-encode to base64.

    `cv2.imencode` on already-decoded pixels never carries the source photo's
    EXIF (orientation/GPS/etc.) -- there is nothing to strip beyond what
    decoding already dropped. Phone photos are 3000-4000px; the model reads
    labels fine at ~1600px and the upload is several times smaller and faster.
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


def _cache_path(image_hashes: Sequence[str]) -> Path:
    combined = hashlib.sha256("".join(sorted(image_hashes)).encode("utf-8")).hexdigest()
    return get_settings().repo_root / "data" / "gemini_cache" / f"{combined}.json"


def _load_cached(image_hashes: Sequence[str]) -> Optional[GeminiCallResult]:
    path = _cache_path(image_hashes)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return GeminiCallResult(
            fields=[FieldExtraction(**f) for f in data["fields"]],
            model_used=data["model_used"],
            input_tokens=data.get("input_tokens"),
            output_tokens=data.get("output_tokens"),
            thought_tokens=data.get("thought_tokens"),
        )
    except Exception:
        return None  # corrupt/incompatible cache entry -- treat as a miss


def _save_cache(image_hashes: Sequence[str], result: GeminiCallResult) -> None:
    path = _cache_path(image_hashes)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "fields": [asdict(f) for f in result.fields],
            "model_used": result.model_used,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "thought_tokens": result.thought_tokens,
        }))
    except Exception:
        pass  # caching is best-effort; never fail a scan over a disk error


def extract_fields_from_images(
    images: List,
    catalog: RuleCatalog,
    declaration_ids: List[str],
    image_hashes: Optional[Sequence[str]] = None,
) -> GeminiCallResult:
    """Read declarations from one or more BGR images via Gemini vision, in a
    single request (all images together -- one request per scan).

    `image_hashes` (a content hash per image, e.g. SHA-256 of the decoded
    pixels) enables an on-disk cache keyed by the whole image set, so
    re-scanning the same photos never spends quota twice.
    """
    if not images:
        raise ExtractionError("no images provided to the vision extractor")
    if image_hashes:
        cached = _load_cached(image_hashes)
        if cached is not None:
            return cached
    client = _client()
    content = [
        {"type": "image", "data": _encode_jpeg_stripped(img), "mime_type": "image/jpeg"}
        for img in images
    ]
    content.append({
        "type": "text",
        "text": _declarations_block(catalog, declaration_ids)
        + "\n\nThese images are the front and/or back of one packaged product. "
          "Read every label panel and extract the declarations, transcribing "
          "values verbatim. A declaration found on any image counts as present.",
    })
    interaction, model_used = _call_with_model_fallback(client, content)
    fields = _parse_response(interaction, declaration_ids)
    in_tok, out_tok, thought_tok = _usage_from(interaction)
    result = GeminiCallResult(fields=fields, model_used=model_used,
                              input_tokens=in_tok, output_tokens=out_tok,
                              thought_tokens=thought_tok)
    if image_hashes:
        _save_cache(image_hashes, result)
    return result


def extract_fields_gemini(
    text: str,
    catalog: RuleCatalog,
    declaration_ids: List[str],
) -> GeminiCallResult:
    """Extract declarations from OCR/label text via Gemini (text-only path)."""
    client = _client()
    prompt = _declarations_block(catalog, declaration_ids) + "\n\nLABEL TEXT:\n" + text
    interaction, model_used = _call_with_model_fallback(client, prompt)
    fields = _parse_response(interaction, declaration_ids)
    in_tok, out_tok, thought_tok = _usage_from(interaction)
    return GeminiCallResult(fields=fields, model_used=model_used,
                            input_tokens=in_tok, output_tokens=out_tok,
                            thought_tokens=thought_tok)
