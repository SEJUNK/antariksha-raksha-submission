"""Operator account management CLI (writes to the governance audit as 'cli').

    python -m backend.users create --username U --role ROLE [--display-name N] [--password-stdin]
    python -m backend.users list
    python -m backend.users set-password --username U [--password-stdin]
    python -m backend.users set-role --username U --role ROLE
    python -m backend.users deactivate --username U
    python -m backend.users activate --username U

Passwords are read with getpass (asked twice) or, for scripts, from the first
line of stdin with --password-stdin. They are never echoed, logged or stored
in plain text. Roles: VIEWER, OPERATOR, ASSET_MANAGER, ADMINISTRATOR.
"""

import argparse
import getpass
import sys

from backend import auth, db

CLI_ACTOR = {"user_id": None, "username": "cli", "role": None}


class CliError(Exception):
    pass


def _read_password(from_stdin, stdin=None):
    if from_stdin:
        line = (stdin or sys.stdin).readline()
        password = line.rstrip("\r\n")
    else:
        password = getpass.getpass("Password: ")
        if getpass.getpass("Repeat password: ") != password:
            raise CliError("Passwords do not match.")
    try:
        return auth.validate_password(password)
    except auth.ValidationFailed as exc:
        raise CliError(str(exc)) from exc


def _user_or_fail(username):
    user = db.get_user_by_username((username or "").strip())
    if user is None:
        raise CliError(f"No user named '{username}'.")
    return user


def _update(user, **changes):
    try:
        return db.update_user(user["id"], CLI_ACTOR, **changes)
    except db.LastAdministratorError as exc:
        raise CliError(str(exc)) from exc


def run(argv=None, stdin=None, out=None):
    out = out or sys.stdout
    parser = argparse.ArgumentParser(prog="python -m backend.users", description="Manage operator accounts.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("create", help="create a user")
    p.add_argument("--username", required=True)
    p.add_argument("--role", required=True, choices=auth.ROLES)
    p.add_argument("--display-name")
    p.add_argument("--password-stdin", action="store_true", help="read the password from stdin (one line)")
    sub.add_parser("list", help="list users (no password data)")
    p = sub.add_parser("set-password", help="set a user's password (revokes their sessions)")
    p.add_argument("--username", required=True)
    p.add_argument("--password-stdin", action="store_true")
    p = sub.add_parser("set-role", help="change a user's role")
    p.add_argument("--username", required=True)
    p.add_argument("--role", required=True, choices=auth.ROLES)
    for name in ("deactivate", "activate"):
        p = sub.add_parser(name, help=f"{name} a user" + (" (revokes their sessions)" if name == "deactivate" else ""))
        p.add_argument("--username", required=True)
    args = parser.parse_args(argv)

    db.init_db()
    try:
        if args.command == "create":
            password = _read_password(args.password_stdin, stdin)
            try:
                user = auth.create_user_account(args.username, args.role, password, args.display_name,
                                                actor=CLI_ACTOR, created_by="cli")
            except (auth.ValidationFailed, db.DuplicateError) as exc:
                raise CliError(str(exc)) from exc
            print(f"Created {user['role']} '{user['username']}' (id {user['id']}).", file=out)
        elif args.command == "list":
            users = db.list_users()
            if not users:
                print("No users.", file=out)
            for u in users:
                print(f"{u['id']:>4}  {u['username']:<32} {u['role']:<14} "
                      f"{'active' if u['active'] else 'INACTIVE':<9} last_login={u['last_login_at'] or '-'}",
                      file=out)
        elif args.command == "set-password":
            user = _user_or_fail(args.username)
            password = _read_password(args.password_stdin, stdin)
            _update(user, password_hash=auth.hash_password(password), audit_action="user_set_password")
            print(f"Password updated for '{user['username']}' (existing sessions revoked).", file=out)
        elif args.command == "set-role":
            user = _user_or_fail(args.username)
            _update(user, role=args.role, audit_action="user_set_role")
            print(f"'{user['username']}' is now {args.role}.", file=out)
        elif args.command in ("deactivate", "activate"):
            user = _user_or_fail(args.username)
            _update(user, active=args.command == "activate", audit_action=f"user_{args.command}")
            print(f"'{user['username']}' {args.command}d.", file=out)
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run())
