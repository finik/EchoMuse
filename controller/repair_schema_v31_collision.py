#!/usr/bin/env python3
"""
Repair a database that ran the pre-rebase v31 (wake_samples.mww_score).

Upstream took v31 for devices.ble_proxy_key and v32 for the EQ preset reset
while this fork's wake-recording work was out of tree, so both numbered their
migration 31. The stored schema_version is an INDEX into MIGRATIONS, so a
database carrying our v31 reads as "31 applied" and the controller would:

  - skip upstream's v31 permanently, leaving devices.ble_proxy_key absent for
    good — the BLE proxy's HA encryption key has nowhere to live; and
  - run the renumbered v33, whose ALTER fails because mww_score is already
    there, so startup refuses.

It refuses loudly rather than corrupting anything, which is the good case.
This brings such a database to 33 with nothing lost: it adds the column
upstream's v31 adds, applies v32's EQ reset through em_db's own fixup so the
two cannot disagree, and then stamps 33 because our v31 already did v33's work.

Run it with the controller STOPPED. Usage:
    python repair_schema_v31_collision.py /path/to/echomuse.db
"""
import shutil
import sqlite3
import sys
from pathlib import Path


def columns(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def main(path):
    db = Path(path)
    if not db.exists():
        sys.exit(f"no such database: {db}")

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    ver = conn.execute(
        "SELECT value FROM system_config WHERE key='schema_version'").fetchone()
    ver = int(ver["value"]) if ver else -1
    have_mww = "mww_score" in columns(conn, "wake_samples")
    have_key = "ble_proxy_key" in columns(conn, "devices")

    if ver != 31 or not have_mww or have_key:
        conn.close()
        sys.exit(
            f"not the colliding shape (version={ver}, mww_score={have_mww}, "
            f"ble_proxy_key={have_key}) — nothing to repair, leaving it alone")
    conn.close()

    backup = db.with_suffix(db.suffix + ".pre-v31-repair.bak")
    src = sqlite3.connect(db)
    dst = sqlite3.connect(backup)
    with dst:
        src.backup(dst)          # the API, so WAL state is consistent
    dst.close()
    src.close()
    print(f"backed up to {backup}")

    # em_db owns what v32 means; importing it keeps one definition.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            conn.execute("ALTER TABLE devices ADD COLUMN ble_proxy_key TEXT")
            try:
                import em_db
                em_db._fixup_v32(conn)
                print("applied v32's EQ reset via em_db._fixup_v32")
            except ImportError:
                print("WARNING: em_db not importable from here — v32's EQ reset "
                      "was NOT applied. Run this from the controller directory, "
                      "or reset Speech/Music EQ presets to Flat by hand.")
            conn.execute(
                "UPDATE system_config SET value='33' WHERE key='schema_version'")
    finally:
        conn.close()
    print("repaired: ble_proxy_key added, schema_version set to 33, "
          "mww_score and every wake sample left untouched")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
