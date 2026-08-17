"""Copy reviewed photo assignments from one database to another, matched by slug.

    python api/tools/copy_media.py --from <source dsn> --to <target dsn> [--overwrite]

WHY THIS EXISTS. `tripplan fetch-photos` is the way photographs get attached, and it
is the documented path for a fresh database. But it asks Wikimedia Commons what it
holds *today*, and Commons is a moving target: a re-run can pick a different file for
the same place, and every one of those choices was reviewed by eye (a historical MAP
became a district hero image once, and a bus stop stood in for a temple).

So when the point is to stand up a deployment showing the set somebody has already
checked, copying beats re-fetching. It is also 10 minutes faster and puts no load on
a free service.

WHAT IT DOES NOT DO. It does not copy the image FILES. `pois.media[].local_path`
points at `web/public/photos/`, which a deployed frontend does not have — production
serves the Commons URL from the same row instead (`NEXT_PUBLIC_PHOTO_SOURCE=remote`).
Both fields travel together in the jsonb, so one row serves both cases.

Slug-matched on purpose: ids are per-database, slugs are the stable identity that the
seed files define.
"""

from __future__ import annotations

import argparse
import asyncio
import json

import asyncpg


async def copy_table(
    source: asyncpg.Connection,
    target: asyncpg.Connection,
    table: str,
    *,
    overwrite: bool,
) -> tuple[int, int]:
    """Copy `media` for one table. Returns (updated, skipped)."""
    rows = await source.fetch(
        f"SELECT slug, media FROM {table} WHERE media <> '[]'::jsonb"  # noqa: S608 — literal
    )
    updated = skipped = 0
    for row in rows:
        # Only fill a gap unless told otherwise: a target that already has a photo
        # may have been curated there, and silently replacing it would undo that.
        clause = "" if overwrite else " AND media = '[]'::jsonb"
        result = await target.execute(
            f"UPDATE {table} SET media = $2::jsonb WHERE slug = $1{clause}",  # noqa: S608
            row["slug"],
            json.dumps(json.loads(row["media"]) if isinstance(row["media"], str) else row["media"]),
        )
        if result.endswith("0"):
            skipped += 1
        else:
            updated += 1
    return updated, skipped


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="source", required=True, help="source DSN")
    parser.add_argument("--to", dest="target", required=True, help="target DSN")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace photos the target already has, rather than only filling gaps",
    )
    args = parser.parse_args()

    source = await asyncpg.connect(dsn=args.source)
    target = await asyncpg.connect(dsn=args.target)
    try:
        for table in ("pois", "regions"):
            updated, skipped = await copy_table(source, target, table, overwrite=args.overwrite)
            print(f"{table:<8} copied {updated}, left alone {skipped}")

        # Report what the target still lacks. A row with no photograph is a normal
        # outcome here — about a third of places have none on Commons — but the count
        # is worth seeing rather than assuming.
        for table in ("pois", "regions"):
            without = await target.fetchval(
                f"SELECT count(*) FROM {table} WHERE media = '[]'::jsonb"  # noqa: S608
            )
            total = await target.fetchval(f"SELECT count(*) FROM {table}")  # noqa: S608
            print(f"{table:<8} target now: {total - int(without)}/{total} with a photograph")
    finally:
        await source.close()
        await target.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
