#!/usr/bin/env python3
"""
backfill_contact_emails.py -- merge verified emails from a Bigin Contacts
export into the existing leads_registry.db, matched by the same key logic
as dedupe_import.py (phone first, name+address hash as fallback).

Usage:
    py scripts/backfill_contact_emails.py bin/data/bigin_contact_emails.csv
"""
import csv
import sqlite3
import os
import sys
import hashlib

sys.path.insert(0, os.path.dirname(__file__))
from dedupe_import import normalize_phone, DEFAULT_REGISTRY


def make_phone_key(phone):
    p = normalize_phone(phone)
    return ("ph:" + p) if p else None


def make_name_addr_key(name, address):
    basis = (name or "").strip().lower() + "|" + (address or "").strip().lower()
    return "na:" + hashlib.sha1(basis.encode("utf-8")).hexdigest()


def main():
    if len(sys.argv) < 2:
        print("Usage: py backfill_contact_emails.py <contacts_csv>")
        sys.exit(1)

    input_csv = sys.argv[1]
    conn = sqlite3.connect(DEFAULT_REGISTRY)

    updated, no_match, already_had, checked = 0, [], 0, 0

    with open(input_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            checked += 1
            email = (row.get("email") or "").strip()
            if not email:
                continue

            phone_key = make_phone_key(row.get("phone") or "")
            key = None
            if phone_key:
                match = conn.execute(
                    "SELECT key, email FROM leads WHERE key = ?", (phone_key,)
                ).fetchone()
                if match:
                    key = phone_key

            if not key:
                na_key = make_name_addr_key(row.get("name"), row.get("address"))
                match = conn.execute(
                    "SELECT key, email FROM leads WHERE key = ?", (na_key,)
                ).fetchone()
                if match:
                    key = na_key

            if not key:
                no_match.append((row.get("name"), row.get("phone"), email))
                continue

            existing_email = (match[1] or "").strip()
            if existing_email:
                already_had += 1
                continue

            conn.execute("UPDATE leads SET email = ? WHERE key = ?", (email, key))
            updated += 1

    conn.commit()

    total_with_email = conn.execute(
        "SELECT COUNT(*) FROM leads WHERE email IS NOT NULL AND email != ''"
    ).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    conn.close()

    print(f"Contacts processed: {checked}")
    print(f"Emails written to registry: {updated}")
    print(f"Already had an email (skipped): {already_had}")
    print(f"No matching registry record: {len(no_match)}")
    for name, phone, email in no_match:
        print(f"  - {name} | phone={phone!r} | email={email}")
    print(f"Registry now: {total_with_email}/{total} rows have an email")


if __name__ == "__main__":
    main()
