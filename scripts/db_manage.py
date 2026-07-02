#!/usr/bin/env python3
"""
Database Management CLI for Housing List Search.

This is the "sharp tools" script for intentional database operations.

See PROJECT_CONTRACT_v0.8.6.md for the active contract.

Usage examples:
    python scripts/db_manage.py init
    python scripts/db_manage.py drop --confirm DROP
    python scripts/db_manage.py prune --all-stale --dry-run
    python scripts/db_manage.py prune --not-seen-since 30
    python scripts/db_manage.py prune --from-diff --dry-run
    python scripts/db_manage.py snapshot --name validation-gilroy
    python scripts/db_manage.py info
"""

import argparse
import sys
from pathlib import Path

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from housing_list_search.db import get_manager


def main():
    parser = argparse.ArgumentParser(
        description="Database Management Tool (bespoke DBA for validation & trash compactor)"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # init
    subparsers.add_parser("init", help="Initialize database schema")

    # drop
    drop_p = subparsers.add_parser("drop", help="Drop the entire database (DANGEROUS)")
    drop_p.add_argument("--confirm", required=True, help="Must be exactly 'DROP'")

    # prune
    prune_p = subparsers.add_parser("prune", help="Remove stale records")
    prune_p.add_argument(
        "--not-seen-since", type=int, metavar="DAYS", help="Prune records not seen in N days"
    )
    prune_p.add_argument("--all-stale", action="store_true", help="Apply all current stale rules")
    prune_p.add_argument("--dry-run", action="store_true", help="Show what would be deleted")
    prune_p.add_argument("--authority", help="Limit to a specific authority")
    prune_p.add_argument(
        "--expires-at-past", action="store_true", help="Prune records past expires_at"
    )
    prune_p.add_argument(
        "--from-diff", action="store_true", help="Delete rows matching STALE entries in diff.csv"
    )
    prune_p.add_argument("--diff-path", default="diff.csv", help="Path to diff.csv for --from-diff")

    # snapshot
    snap_p = subparsers.add_parser("snapshot", help="Create a named snapshot")
    snap_p.add_argument("--name", required=True, help="Label for the snapshot")

    # list-snapshots
    subparsers.add_parser("list-snapshots", help="List available snapshots")

    # info
    subparsers.add_parser("info", help="Show database status")

    # history
    subparsers.add_parser("history", help="Show recent run history")

    args = parser.parse_args()

    mgr = get_manager()

    try:
        if args.command == "init":
            mgr.init_db()
            print("✅ Database initialized / schema ensured")

        elif args.command == "drop":
            mgr.drop_db(confirm=args.confirm)
            print("💥 Database dropped")

        elif args.command == "prune":
            if args.from_diff:
                result = mgr.prune_from_diff(args.diff_path, dry_run=args.dry_run)
            else:
                result = mgr.prune(
                    not_seen_since_days=args.not_seen_since,
                    authority=args.authority,
                    dry_run=args.dry_run,
                    all_stale=args.all_stale,
                    expires_at_past=args.expires_at_past,
                )
            if args.dry_run:
                print(f"Would delete {result.get('would_delete', 0)} records (dry run)")
            else:
                print(
                    f"Pruned {result.get('deleted', 0)} records. Before={result['before']}, After={result['after']}"
                )

        elif args.command == "snapshot":
            path = mgr.snapshot(args.name)
            print(f"✅ Snapshot created: {path}")

        elif args.command == "list-snapshots":
            snaps = mgr.list_snapshots()
            if not snaps:
                print("No snapshots found.")
            else:
                for s in snaps:
                    print(s)

        elif args.command == "info":
            info = mgr.info()
            for k, v in info.items():
                print(f"{k}: {v}")

        elif args.command == "history":
            # simple history dump
            conn = mgr.connect()
            c = conn.cursor()
            c.execute(
                "SELECT timestamp, command, authority_filter, rows_before, rows_after FROM run_history ORDER BY timestamp DESC LIMIT 20"
            )
            rows = c.fetchall()
            if not rows:
                print("No run history yet.")
            else:
                for r in rows:
                    print(r)

    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)
    finally:
        mgr.close()


if __name__ == "__main__":
    main()
