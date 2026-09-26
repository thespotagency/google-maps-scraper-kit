#!/usr/bin/env python3
"""
dedupe_import.py -- cross-run lead deduplication registry.

Keeps a persistent SQLite registry (bin/data/leads_registry.db) of every
business ever scraped, keyed by Google Maps place_id (falls back to
normalized phone, then name+address hash if place_id is missing).

Usage:
    python scripts/dedupe_import.py results.csv
        -> writes results.new.csv with ONLY businesses never seen before,
           and records everything (new + dup) in the registry.

    python scripts/dedupe_import.py old_export.csv --seed
        -> bulk-loads a past export (e.g. your Bigin CRM export, or an old
           scrape CSV) into the registry as "already known", writes NO
           output file. Use this once to backfill history.
"""
import csv
import sqlite3
import os
import re
import argparse
import hashlib
from datetime import datetime, timezone

DEFAULT_REGISTRY = os.path.join(
    os.path.dirname(__file__), "..", "bin", "data", "leads_registry.db"
)


def normalize_phone(phone):
    if not phone:
        return ""
    return re.sub(r"\D", "", phone)


def make_key(row):
    """Prefer place_id (stable, globally unique). Fall back to phone,
    then to a hash of name+address, so this still works on CSVs that
    already had place_id stripped (the kit's default 'clean' output)."""
    pid = (row.get("place_id") or "").strip()
    if pid:
        return "pid:" + pid
    phone = normalize_phone(row.get("phone") or "")
    if phone:
        return "ph:" + phone
    name = (row.get("title") or "").strip().lower()
    addr = (row.get("address") or row.get("complete_address") or "").strip().lower()
    basis = name + "|" + addr
    return "na:" + hashlib.sha1(basis.encode("utf-8")).hexdigest()


def ensure_schema(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS leads (
            key TEXT PRIMARY KEY, place_id TEXT, title TEXT, phone TEXT,
            website TEXT, email TEXT, category TEXT, address TEXT,
            first_scraped_at TEXT, last_seen_at TEXT,
            times_seen INTEGER DEFAULT 1, status TEXT DEFAULT 'new'
        )"""
    )
    conn.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_csv")
    ap.add_argument("--registry", default=DEFAULT_REGISTRY)
    ap.add_argument("--out-new", default=None)
    ap.add_argument(
        "--seed", action="store_true",
        help="Bulk-load as already-known, write no output file (backfill past exports)",
    )
    args = ap.parse_args()

    os.makedirs(os.path.dirname(args.registry), exist_ok=True)
    conn = sqlite3.connect(args.registry)
    ensure_schema(conn)

    out_path = args.out_new or (os.path.splitext(args.input_csv)[0] + ".new.csv")
    now = datetime.now(timezone.utc).isoformat()

    new_rows, dup_count, seen_this_run = [], 0, set()

    with open(args.input_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            key = make_key(row)
            if key in seen_this_run:
                continue
            seen_this_run.add(key)

            existing = conn.execute(
                "SELECT key FROM leads WHERE key = ?", (key,)
            ).fetchone()

            if existing:
                dup_count += 1
                conn.execute(
                    "UPDATE leads SET last_seen_at=?, times_seen=times_seen+1 WHERE key=?",
                    (now, key),
                )
                continue

            conn.execute(
                """INSERT INTO leads (key, place_id, title, phone, website, email,
                   category, address, first_scraped_at, last_seen_at, times_seen, status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,1,?)""",
                (
                    key, row.get("place_id", ""), row.get("title", ""),
                    row.get("phone", ""), row.get("website", ""),
                    row.get("email", "") or row.get("emails", ""),
                    row.get("category", ""),
                    row.get("address", "") or row.get("complete_address", ""),
                    now, now, "seed" if args.seed else "new",
                ),
            )
            if not args.seed:
                new_rows.append(row)

    conn.commit()
    conn.close()

    if args.seed:
        added = len(seen_this_run) - dup_count
        print(f"Seeded {added} businesses into the registry ({dup_count} already known). No output file written.")
        return

    print(f"Input rows (unique): {len(seen_this_run)}")
    print(f"Already known (skipped): {dup_count}")
    print(f"Genuinely new: {len(new_rows)}")

    if new_rows:
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(new_rows)
        print(f"New leads written to: {out_path}")
    else:
        print("No new leads -- every business in this scrape was already in the registry.")


if __name__ == "__main__":
    main()
