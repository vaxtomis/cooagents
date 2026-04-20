#!/usr/bin/env python
"""Generate the full auth env bundle for cooagents.

Usage:
    python scripts/generate_password_hash.py
    python scripts/generate_password_hash.py --password 'my secret'

The output is suitable for a systemd EnvironmentFile or `.env`:
    ADMIN_USERNAME=admin
    ADMIN_PASSWORD_HASH=$argon2id$...
    JWT_SECRET=...
    AGENT_API_TOKEN=...     # give to local agents via their env (e.g. OpenClaw)
"""
from __future__ import annotations

import argparse
import getpass
import secrets
import sys

from src.auth import hash_password


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--password", help="Plaintext password (prompt if omitted)")
    parser.add_argument("--username", default="admin", help="ADMIN_USERNAME value (default: admin)")
    args = parser.parse_args()

    password = args.password
    if not password:
        password = getpass.getpass("Password: ")
        confirm = getpass.getpass("Confirm:  ")
        if password != confirm:
            print("ERROR: passwords do not match", file=sys.stderr)
            return 1
        if len(password) < 8:
            print("ERROR: password must be at least 8 characters", file=sys.stderr)
            return 1

    pwd_hash = hash_password(password)
    jwt_secret = secrets.token_urlsafe(48)
    agent_token = secrets.token_urlsafe(40)

    print()
    print("# Append these to your environment (e.g. systemd EnvironmentFile or .env):")
    print(f"ADMIN_USERNAME={args.username}")
    print(f"ADMIN_PASSWORD_HASH={pwd_hash}")
    print(f"JWT_SECRET={jwt_secret}")
    print(f"AGENT_API_TOKEN={agent_token}")
    print()
    print("# Copy AGENT_API_TOKEN into the environment of any local agent")
    print("# (e.g. OpenClaw) that needs to call the cooagents API without")
    print("# an interactive login. Example:")
    print("#   systemctl edit openclaw     # then: Environment=AGENT_API_TOKEN=...")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
