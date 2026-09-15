# core

Plain-dataclass settings (`config.py`, sourced from environment variables —
no pydantic-settings), typed errors, and startup safety checks (marker-size
mismatch warning, production auth/secret guard). JWT/password hashing live in
`api/security.py`, not here.
