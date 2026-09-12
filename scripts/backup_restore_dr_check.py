"""Non-destructive backup/restore certification helper.

Usage in an operations environment:
  POSTGRES_URL=... BACKUP_FILE=/secure/path/backup.dump python scripts/backup_restore_dr_check.py

The script never restores into the production database. A restore target must
be supplied explicitly and should point at an isolated disposable PostgreSQL
instance.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys


def main() -> int:
    backup = Path(os.getenv("BACKUP_FILE", ""))
    restore_url = os.getenv("DR_RESTORE_DATABASE_URL", "")
    if not backup.is_file():
        print("BACKUP_FILE must point to an existing pg_dump custom-format file", file=sys.stderr)
        return 2
    if not restore_url:
        print("DR_RESTORE_DATABASE_URL must point to an isolated disposable restore target", file=sys.stderr)
        return 2
    pg_restore = shutil.which("pg_restore")
    if not pg_restore:
        print("pg_restore is required", file=sys.stderr)
        return 2
    command = [pg_restore, "--clean", "--if-exists", "--no-owner", "--exit-on-error", "--dbname", restore_url, str(backup)]
    result = subprocess.run(command, text=True, capture_output=True, timeout=int(os.getenv("DR_RESTORE_TIMEOUT_SECONDS", "900")))
    if result.returncode:
        print(result.stderr[-4000:], file=sys.stderr)
        return result.returncode
    print("DR restore completed successfully against the isolated target.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
