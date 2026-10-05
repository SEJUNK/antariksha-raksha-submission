"""Prepare a copy of the local SQLite database for upload to a hosted backend
volume (e.g. Railway /data/antariksha.db).

The source database is only READ (SQLite online backup API, so a running
backend is not disturbed). The copy keeps users, roles, password hashes,
governance/decision audit, settings, catalog and screening history, and
removes every login session so no existing session token is carried to the
hosted backend (everyone signs in again there). Nothing secret is printed:
only table row counts and username/role/active flags.

Usage:
    python scripts/prepare_deploy_db.py data/antariksha.db <out.db>
"""

import argparse
import sqlite3
import sys
from pathlib import Path


def prepare(source, out):
    source, out = Path(source), Path(out)
    if not source.is_file():
        raise SystemExit(f"source database not found: {source}")
    if out.exists():
        raise SystemExit(f"refusing to overwrite existing file: {out}")
    if source.resolve() == out.resolve():
        raise SystemExit("output must differ from the source")
    src = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(out)
    try:
        src.backup(dst)
        tables = {r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "sessions" in tables:
            dst.execute("DELETE FROM sessions")
        dst.commit()
        dst.execute("VACUUM")
        check = dst.execute("PRAGMA integrity_check").fetchone()[0]
        if check != "ok":
            raise SystemExit(f"integrity check failed: {check}")
        counts = {t: dst.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                  for t in sorted(tables) if t != "sqlite_sequence"}
        users = []
        if "users" in tables:
            users = [dict(username=u, role=r, active=bool(a)) for u, r, a in
                     dst.execute("SELECT username, role, active FROM users ORDER BY id")]
        return {"integrity": check, "counts": counts, "users": users}
    finally:
        src.close()
        dst.close()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("source")
    p.add_argument("out")
    a = p.parse_args(argv)
    result = prepare(a.source, a.out)
    print(f"integrity: {result['integrity']}")
    for t, n in result["counts"].items():
        print(f"  {t}: {n}")
    for u in result["users"]:
        print(f"  user {u['username']} role={u['role']} active={u['active']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
