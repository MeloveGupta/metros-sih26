"""Evidence + generated-report storage: local disk (dev) or Supabase Storage
(production).

Local disk (under `settings.uploads_dir`) is used whenever `SUPABASE_URL` /
`SUPABASE_SERVICE_ROLE_KEY` / `SUPABASE_BUCKET` aren't all set -- that's the
only behavioural difference, so local dev and the test suite need no
Supabase account at all. Every path handled here is relative (e.g.
"<report_id>/0_front.png", "<report_id>/crops/mrp.png",
"<report_id>/report.pdf"), matching what's stored in
`Report.evidence.images[].file` / `DeclarationFinding.evidence_crop` either
way -- callers never need to know which backend is in play.

Talks to Supabase Storage's plain REST API directly (not the `supabase-py`
SDK) with `httpx`, already a real dependency here -- the full SDK pulls in
postgrest/gotrue/realtime clients this app never uses, for no benefit given
Render's free-tier memory ceiling.
"""
from __future__ import annotations

import mimetypes

from .config import get_settings
from .errors import StorageError


def _supabase_configured() -> bool:
    s = get_settings()
    return bool(s.supabase_url and s.supabase_service_role_key and s.supabase_bucket)


def _content_type(rel_path: str) -> str:
    return mimetypes.guess_type(rel_path)[0] or "application/octet-stream"


def save_bytes(rel_path: str, data: bytes) -> None:
    """Write `data` at `rel_path`, creating any parent directory (local) or
    the object (Supabase) as needed. Overwrites an existing object."""
    if _supabase_configured():
        _supabase_upload(rel_path, data)
        return
    path = get_settings().uploads_dir / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def read_bytes(rel_path: str) -> bytes:
    """Return the object's bytes. Raises FileNotFoundError if it doesn't
    exist, in either backend."""
    if _supabase_configured():
        return _supabase_download(rel_path)
    path = get_settings().uploads_dir / rel_path
    if not path.is_file():
        raise FileNotFoundError(rel_path)
    return path.read_bytes()


def _supabase_upload(rel_path: str, data: bytes) -> None:
    import httpx  # lazy: only touched when Supabase Storage is configured

    settings = get_settings()
    url = (f"{settings.supabase_url.rstrip('/')}/storage/v1/object/"
           f"{settings.supabase_bucket}/{rel_path}")
    headers = {
        "Authorization": f"Bearer {settings.supabase_service_role_key}",
        "apikey": settings.supabase_service_role_key,
        "Content-Type": _content_type(rel_path),
        "x-upsert": "true",  # overwrite on re-scan/regenerate rather than 400 Duplicate
    }
    try:
        resp = httpx.post(url, headers=headers, content=data, timeout=30.0)
    except httpx.HTTPError as exc:
        raise StorageError(f"could not reach Supabase Storage: {exc}") from exc
    if resp.status_code >= 300:
        raise StorageError(
            f"Supabase Storage upload of {rel_path!r} failed "
            f"({resp.status_code}): {resp.text[:200]}"
        )


_NOT_FOUND_CODES = {"NoSuchKey", "NoSuchBucket", "not_found", "object_not_found"}


def _is_not_found_response(resp) -> bool:
    """Supabase Storage's object-download endpoint doesn't reliably use HTTP
    404 for "doesn't exist" -- it can return 400 with the real status/code
    inside the JSON body instead (e.g. {"statusCode":"404","code":"NoSuchKey"}
    or {"error":"Bucket not found","code":"NoSuchBucket"}). Check both the
    transport status and the body so a routine cache-miss (a report not yet
    rendered) is recognized as "not found" instead of a hard failure."""
    if resp.status_code == 404:
        return True
    if resp.status_code != 400:
        return False
    try:
        body = resp.json()
    except ValueError:
        return False
    return (
        str(body.get("statusCode", "")) == "404"
        or body.get("code") in _NOT_FOUND_CODES
        or body.get("error") in _NOT_FOUND_CODES
    )


def _supabase_download(rel_path: str) -> bytes:
    import httpx

    settings = get_settings()
    url = (f"{settings.supabase_url.rstrip('/')}/storage/v1/object/"
           f"{settings.supabase_bucket}/{rel_path}")
    headers = {
        "Authorization": f"Bearer {settings.supabase_service_role_key}",
        "apikey": settings.supabase_service_role_key,
    }
    try:
        resp = httpx.get(url, headers=headers, timeout=30.0)
    except httpx.HTTPError as exc:
        raise StorageError(f"could not reach Supabase Storage: {exc}") from exc
    if _is_not_found_response(resp):
        raise FileNotFoundError(rel_path)
    if resp.status_code >= 300:
        raise StorageError(
            f"Supabase Storage download of {rel_path!r} failed "
            f"({resp.status_code}): {resp.text[:200]}"
        )
    return resp.content
