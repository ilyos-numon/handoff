"""Invite link CLI.

``python -m handoff.rbac.cli invite --role ROLE --leaver ID`` prints a role link;
``python -m handoff.rbac.cli admin`` prints the bootstrap admin link.
"""

import argparse
import re
import sys

from handoff.config import load_settings
from handoff.rbac.tokens import (
    ADMIN_ROLE,
    ADMIN_SCOPE,
    ROLES,
    Principal,
    issue_invite,
    load_secret,
)

DEFAULT_BASE_URL = "http://localhost:8780"
_LEAVER = re.compile(r"^[a-z0-9_-]{1,40}$")


def _leaver(value: str) -> str:
    if not _LEAVER.fullmatch(value):
        raise argparse.ArgumentTypeError("leaver must match ^[a-z0-9_-]{1,40}$")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m handoff.rbac.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    invite = commands.add_parser("invite", help="print a signed invite link")
    invite.add_argument("--role", required=True, choices=ROLES)
    invite.add_argument("--leaver", required=True, type=_leaver)
    invite.add_argument("--base-url", default=DEFAULT_BASE_URL)
    admin = commands.add_parser("admin", help="print a signed admin link")
    admin.add_argument("--base-url", default=DEFAULT_BASE_URL)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = load_settings()
    if not settings.auth:
        print("warning: HANDOFF_AUTH is off; the server will not accept this link", file=sys.stderr)
    secret = load_secret(settings.data_dir, settings.auth_secret)
    if args.command == "admin":
        principal = Principal(role=ADMIN_ROLE, leaver=ADMIN_SCOPE)
    else:
        principal = Principal(role=args.role, leaver=args.leaver)
    token = issue_invite(secret, principal)
    print(f"{args.base_url.rstrip('/')}/auth/accept?token={token}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
