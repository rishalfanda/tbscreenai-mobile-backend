"""Remove stored images that no diagnosis ever claimed.

Run:   python -m scripts.cleanup_orphan_images            # list only
       python -m scripts.cleanup_orphan_images --delete   # actually remove

Listing is the default because removal cannot be undone. An image counts as
an orphan only when it is older than the grace period (seven days unless
--grace-days says otherwise) and no diagnosis references it. Images that a
diagnosis references are never touched, however old.
"""

import argparse
from datetime import UTC, datetime, timedelta

from app.core.database import SessionLocal
from app.services.images import ORPHAN_GRACE, find_orphans
from app.services.storage import get_object_storage


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--delete", action="store_true", help="remove what is found")
    parser.add_argument(
        "--grace-days",
        type=int,
        default=ORPHAN_GRACE.days,
        help=f"only images older than this many days (default {ORPHAN_GRACE.days})",
    )
    args = parser.parse_args(argv)
    if args.grace_days < 1:
        parser.error("--grace-days must be at least 1")

    storage = get_object_storage()
    with SessionLocal() as db:
        orphans = find_orphans(
            db, storage, now=datetime.now(UTC), grace=timedelta(days=args.grace_days)
        )

    for key in orphans:
        if args.delete:
            storage.delete_object(key)
        print(("removed " if args.delete else "would remove ") + key)
    verb = "removed" if args.delete else "found"
    print(f"{len(orphans)} orphan image(s) {verb}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
