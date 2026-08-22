#!/usr/bin/env python3
"""Mint invite codes for CoProducer v1 Beta testers."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

from nodaw.beta.license import BetaGate, is_valid_email  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Mint CoProducer beta invite code")
    p.add_argument("email", help="Tester email")
    p.add_argument("--note", default="", help="Optional note")
    p.add_argument("--code", default=None, help="Optional fixed 6-digit code")
    args = p.parse_args()
    if not is_valid_email(args.email):
        print("Invalid email", file=sys.stderr)
        return 2
    gate = BetaGate(ROOT)
    inv = gate.mint_invite(args.email, note=args.note, code=args.code)
    print("Invite created")
    print(f"  Email:  {inv['email']}")
    print(f"  Code:   {inv['code']}")
    print(f"  Id:     {inv['invite_id']}")
    print(f"  Store:  {inv['invites_path']}")
    print("Send the email + code to the tester. Codes are single-use per email.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
