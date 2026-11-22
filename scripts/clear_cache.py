"""
Delete every cached answer from the Upstash cache and keep its index. Run it by hand after a
pipeline change that changes answers:

    uv run python scripts/clear_cache.py

Reads UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN from the shell or the project .env.
"""

import argparse
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from upstash_redis import Redis

from backend.app.cache import PREFIX

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ("UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_TOKEN")
SCAN_PAGE = 100


def main(argv: list[str] | None = None) -> None:
    # Parsed only so that --help prints this and deletes nothing.
    argparse.ArgumentParser(
        description="Delete every cached answer and keep the index."
    ).parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
    # httpx (under the Upstash client) logs one INFO line per request.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_dotenv(PROJECT_ROOT / ".env")
    missing = [name for name in SETTINGS if not os.getenv(name)]
    if missing:
        logger.error("Missing settings: %s; set them in .env", ", ".join(missing))
        raise SystemExit(1)

    client = Redis(os.environ["UPSTASH_REDIS_REST_URL"], os.environ["UPSTASH_REDIS_REST_TOKEN"])
    # SCAN pages through the keys without blocking the database, as KEYS would.
    cursor, deleted = 0, 0
    while True:
        cursor, keys = client.scan(cursor, match=f"{PREFIX}*", count=SCAN_PAGE)
        if keys:
            deleted += client.delete(*keys)
        if cursor == 0:
            break
    # The index drops deleted keys by itself, so it is left in place.
    logger.info("deleted %d cached answers", deleted)


if __name__ == "__main__":
    main()
