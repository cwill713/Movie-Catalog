"""Match manually-logged watch entries to catalog titles.

Entries created before the catalog existed carry manual_title/manual_year and no
imdb_id. This proposes a catalog match for each using trigram similarity on the
title plus closeness of year, and links the confident ones.

Deliberately two-step: it prints proposals and changes nothing until --apply.
Fuzzy matching on titles is exactly the sort of thing that looks right in
aggregate and is wrong on the one row you care about.

    python scripts/link_watch_entries.py            # show proposals
    python scripts/link_watch_entries.py --apply    # link matches at/above the threshold
"""

import argparse
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import get_settings  # noqa: E402
from app.db import DEFAULT_HOUSEHOLD_ID  # noqa: E402

# Combined score: trigram title similarity, penalised by distance in years.
# An exact-year match with 0.55 similarity beats a 3-year-off match at 0.75.
CANDIDATES = """
    select t.imdb_id,
           t.primary_title,
           t.start_year,
           t.imdb_rating,
           t.imdb_votes,
           similarity(t.primary_title, %(title)s) as title_score,
           abs(coalesce(t.start_year, 0) - %(year)s) as year_gap,
           similarity(t.primary_title, %(title)s)
             - least(abs(coalesce(t.start_year, 9999) - %(year)s), 10) * 0.05 as score
      from titles t
     where t.primary_title %% %(title)s
        or t.primary_title ilike %(pattern)s
     order by score desc, t.imdb_votes desc nulls last
     limit 4
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--threshold", type=float, default=0.45,
                        help="minimum score to link automatically (default 0.45)")
    args = parser.parse_args()

    settings = get_settings()
    linked = skipped = 0

    with psycopg.connect(settings.require_database_url(), row_factory=dict_row) as conn:
        entries = conn.execute(
            """select id, manual_title, manual_year
                 from watch_entries
                where household_id = %s and imdb_id is null
                order by created_at""",
            (DEFAULT_HOUSEHOLD_ID,),
        ).fetchall()

        if not entries:
            print("every watch entry is already linked to a catalog title")
            return 0

        print(f"{len(entries)} unlinked entr{'y' if len(entries) == 1 else 'ies'}\n")

        for entry in entries:
            title, year = entry["manual_title"], entry["manual_year"]
            print(f"  {title}  ({year})")

            rows = conn.execute(
                CANDIDATES,
                {"title": title, "year": year or 0, "pattern": f"%{title}%"},
            ).fetchall()

            if not rows:
                print("      no candidates found\n")
                skipped += 1
                continue

            for i, c in enumerate(rows):
                mark = "->" if i == 0 and c["score"] >= args.threshold else "  "
                print(f"    {mark} {c['score']:.2f}  {c['imdb_id']}  "
                      f"{c['primary_title'][:44]:<44} {c['start_year']}  "
                      f"(title {c['title_score']:.2f}, year gap {c['year_gap']})")

            best = rows[0]
            if best["score"] < args.threshold:
                print(f"      below threshold {args.threshold} - needs a manual decision")
                skipped += 1
            elif args.apply:
                conn.execute(
                    "update watch_entries set imdb_id = %s where id = %s",
                    (best["imdb_id"], entry["id"]),
                )
                print(f"      linked -> {best['imdb_id']}")
                linked += 1
            else:
                linked += 1
            print()

        if args.apply:
            conn.commit()
            print(f"linked {linked}, skipped {skipped}")
        else:
            print(f"would link {linked}, skip {skipped}   (re-run with --apply)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
