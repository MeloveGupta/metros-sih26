#!/usr/bin/env python3
"""Seed initial users into the MetroScan database.

    python scripts/seed_users.py                      # default admin + officer
    python scripts/seed_users.py --email a@b.gov --password s3cret --role admin

Idempotent per email: an existing user is skipped, not duplicated.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.api.security import ROLES, hash_password  # noqa: E402
from backend.db.repository import (  # noqa: E402
    create_user,
    get_user_by_email,
    init_db,
    make_engine,
    session_factory,
)

DEFAULTS = [
    ("admin@metroscan.gov", "admin", "System Admin", "admin"),
    ("officer@metroscan.gov", "officer", "Field Officer", "officer"),
]


def _seed_one(session, email, password, name, role) -> bool:
    if role not in ROLES:
        raise SystemExit(f"role must be one of {ROLES}")
    if get_user_by_email(session, email):
        print(f"= exists: {email}")
        return False
    create_user(session, email=email, name=name, role=role,
                pw_hash=hash_password(password))
    print(f"+ created: {email} ({role})")
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--email")
    ap.add_argument("--password")
    ap.add_argument("--name", default="")
    ap.add_argument("--role", default="officer")
    args = ap.parse_args(argv)

    engine = make_engine()
    init_db(engine)
    Session = session_factory(engine)
    with Session() as session:
        if args.email:
            if not args.password:
                raise SystemExit("--password required with --email")
            _seed_one(session, args.email, args.password,
                      args.name or args.email, args.role)
        else:
            print("Seeding default users (change these passwords in production!):")
            for email, pw, name, role in DEFAULTS:
                _seed_one(session, email, pw, name, role)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
