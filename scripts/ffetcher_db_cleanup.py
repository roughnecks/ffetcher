#!/usr/bin/env python3
"""
ffetcher_db_cleanup.py — ffetcher database cleanup script.

Removes entries from the database that are no longer present in their source
feeds. For each subscribed (feed_url, muc) pair, downloads the feed, extracts
all current article links, computes their hashes, and deletes any entries
that are not in the current feed state.

A delay (30s by default) is applied between consecutive feed downloads, to
avoid hammering a server that hosts several of the subscribed feeds (e.g.
multiple Mastodon tag feeds on the same instance) with back-to-back
requests. Adjust with --delay, or disable with --delay 0.

Requires ffetcher >= 4.0.0, where every row in the `entries` table records
which feed_url produced it. This is what makes it safe to prune entries
belonging to a single feed without touching entries recorded under a
different feed, even when both post into the same MUC.

Usage:
    python3 ffetcher_db_cleanup.py -d /path/to/feeds.db
    python3 ffetcher_db_cleanup.py -d /path/to/feeds.db -v  # verbose

Works entirely independently from the ffetcher bot — can be run standalone,
scheduled via cron, or executed while the bot is offline.

Repository: https://code.woodpeckersnest.space/roughnecks/ffetcher
See ffetcher_db_cleanup_README.md for detailed documentation.
"""

import argparse
import hashlib
import logging
import sqlite3
import sys
import time
from pathlib import Path

import feedparser

DEFAULT_DELAY_SECONDS = 30


def setup_logging(verbose: bool = False) -> None:
    """Configure logging output."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def check_schema(db_path: str) -> bool:
    """
    Verify that the entries table has the feed_url column this script
    requires (ffetcher >= 4.0.0). Returns True if compatible.
    """
    conn = sqlite3.connect(db_path)
    try:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(entries)")]
        return "feed_url" in columns
    finally:
        conn.close()


def fetch_and_parse_feed(feed_url: str) -> dict | None:
    """
    Download and parse a feed using feedparser.
    Returns the parsed feed dict, or None if fetch/parse fails or the feed
    is empty (both cases are treated the same: skip this feed entirely).
    """
    try:
        feed = feedparser.parse(feed_url)

        if feed.bozo:
            reason = feed.get("bozo_exception", "unknown error")
            logging.warning("Feed parse error (skipping): %s (%s)", feed_url, reason)
            return None

        if not feed.entries:
            logging.debug("Feed is empty (skipping): %s", feed_url)
            return None

        return feed

    except Exception as e:
        logging.warning("Failed to fetch feed (skipping): %s (%s)", feed_url, e)
        return None


def extract_links_from_feed(feed: dict) -> set[str]:
    """
    Extract all article links currently present in a parsed feed.
    Returns a set of unique link URLs.
    """
    links = set()
    for entry in feed.entries:
        link = getattr(entry, "link", None)
        if link:
            links.add(link)
    return links


def compute_link_hash(link: str) -> str:
    """Compute SHA256 hash of a link (same algorithm the bot uses)."""
    return hashlib.sha256(link.encode("utf-8")).hexdigest()


def get_all_feed_configs(db_path: str) -> list[tuple[str, str]]:
    """
    Fetch all (feed_url, muc) pairs from the known_feeds table.
    """
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute("SELECT feed_url, muc FROM known_feeds").fetchall()
        conn.close()
        return rows
    except sqlite3.Error as e:
        logging.error("Database error reading known_feeds: %s", e)
        return []


def get_entries_for_feed(db_path: str, muc: str, feed_url: str) -> dict[str, int]:
    """
    Fetch all (hash, id) pairs for a given (muc, feed_url) pair.
    Returns a dict mapping hash -> entry_id.

    Scoping by feed_url (not just muc) is what makes this safe when a MUC
    receives articles from more than one feed: only entries belonging to
    THIS feed are considered candidates for deletion.
    """
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT hash, id FROM entries WHERE muc = ? AND feed_url = ?",
            (muc, feed_url),
        ).fetchall()
        conn.close()
        return {hash_val: entry_id for hash_val, entry_id in rows}
    except sqlite3.Error as e:
        logging.error("Database error reading entries: %s", e)
        return {}


def delete_entries_by_hash(
    db_path: str, muc: str, feed_url: str, hashes_to_delete: set[str]
) -> int:
    """
    Delete entries matching the given hashes for a specific (muc, feed_url)
    pair. Returns the number of rows deleted.
    """
    if not hashes_to_delete:
        return 0

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Delete in batches to avoid excessively long SQL statements.
        deleted = 0
        batch_size = 100
        hashes_list = list(hashes_to_delete)

        for i in range(0, len(hashes_list), batch_size):
            batch = hashes_list[i : i + batch_size]
            placeholders = ",".join("?" * len(batch))
            cursor.execute(
                f"DELETE FROM entries WHERE muc = ? AND feed_url = ? AND hash IN ({placeholders})",
                [muc, feed_url] + batch,
            )
            deleted += cursor.rowcount

        conn.commit()
        conn.close()
        return deleted

    except sqlite3.Error as e:
        logging.error("Database error during deletion: %s", e)
        return 0


def vacuum_database(db_path: str) -> None:
    """
    Run VACUUM to reclaim disk space freed by deleted rows.

    SQLite does not shrink the database file on DELETE by default; the
    freed pages are kept around for reuse. VACUUM rebuilds the file to
    actually release that space back to the filesystem.

    Must run outside any transaction and with no pending changes on the
    connection, so a dedicated connection is opened just for this.
    """
    size_before = Path(db_path).stat().st_size

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("VACUUM")
    finally:
        conn.close()

    size_after = Path(db_path).stat().st_size
    freed_mb = (size_before - size_after) / (1024 * 1024)
    logging.info(
        "VACUUM complete: %.2f MB reclaimed (%.2f MB -> %.2f MB)",
        freed_mb,
        size_before / (1024 * 1024),
        size_after / (1024 * 1024),
    )


def cleanup_database(
    db_path: str,
    verbose: bool = False,
    vacuum: bool = True,
    delay: float = DEFAULT_DELAY_SECONDS,
) -> None:
    """
    Main cleanup routine.
    Iterates through all known feeds and removes entries no longer present
    in the current feed content, scoped strictly per (feed_url, muc) pair.

    A delay is applied between consecutive feed downloads to avoid hammering
    a server that hosts several of the subscribed feeds (e.g. multiple tags
    on the same Mastodon instance) with back-to-back requests.
    """
    logging.info("Starting database cleanup")

    if not Path(db_path).exists():
        logging.error("Database file not found: %s", db_path)
        sys.exit(1)

    if not check_schema(db_path):
        logging.error(
            "The 'entries' table has no 'feed_url' column. "
            "This script requires ffetcher >= 4.0.0. "
            "Older databases are not compatible; see the ffetcher CHANGELOG."
        )
        sys.exit(1)

    feed_configs = get_all_feed_configs(db_path)
    if not feed_configs:
        logging.warning("No feeds found in database; nothing to clean")
        return

    num_feeds = len(feed_configs)
    logging.info("Processing %d feed(s)", num_feeds)

    # Rough time estimate: (num_feeds - 1) delays between downloads, plus a
    # small per-feed allowance for the download/parse itself. This is only
    # an approximation shown so the person running the script knows what to
    # expect, especially with the default 30s delay across many feeds.
    if delay > 0 and num_feeds > 1:
        estimated_seconds = (num_feeds - 1) * delay
        minutes, seconds = divmod(int(estimated_seconds), 60)
        logging.info(
            "With a %.0fs delay between feeds, this will take at least "
            "~%dm%02ds to complete (plus actual download/parse time per feed)",
            delay,
            minutes,
            seconds,
        )

    total_deleted = 0
    feeds_processed = 0
    feeds_skipped = 0

    for i, (feed_url, muc) in enumerate(feed_configs):
        logging.debug("Processing: %s -> %s", feed_url, muc)

        feed = fetch_and_parse_feed(feed_url)
        if not feed:
            logging.debug("Skipping feed: %s", feed_url)
            feeds_skipped += 1
        else:
            current_links = extract_links_from_feed(feed)
            current_hashes = {compute_link_hash(link) for link in current_links}
            logging.debug("Feed has %d article(s)", len(current_links))

            # Entries in the database for THIS specific feed only.
            db_entries = get_entries_for_feed(db_path, muc, feed_url)

            # Hashes in the DB (for this feed) that are no longer in the feed.
            hashes_to_delete = set(db_entries.keys()) - current_hashes

            if hashes_to_delete:
                deleted = delete_entries_by_hash(db_path, muc, feed_url, hashes_to_delete)
                logging.info(
                    "Feed cleanup: %s -> %s: deleted %d entry(ies)",
                    feed_url,
                    muc,
                    deleted,
                )
                total_deleted += deleted
            else:
                logging.debug("No entries to delete for: %s", feed_url)

            feeds_processed += 1

        # Wait between downloads, but not after the very last feed.
        if delay > 0 and i < num_feeds - 1:
            logging.debug("Waiting %.0fs before the next feed...", delay)
            time.sleep(delay)

    logging.info(
        "Cleanup complete: %d feed(s) processed, %d skipped, %d total entries deleted",
        feeds_processed,
        feeds_skipped,
        total_deleted,
    )

    if total_deleted > 0 and vacuum:
        logging.info("Reclaiming disk space...")
        vacuum_database(db_path)
    elif total_deleted > 0:
        logging.debug("VACUUM skipped (--no-vacuum)")
    else:
        logging.debug("Nothing was deleted; skipping VACUUM")


def main():
    parser = argparse.ArgumentParser(
        description="Clean up old entries from the ffetcher database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
The script processes all (feed_url, muc) pairs in the database,
fetches each feed, and removes any entries that no longer exist
in the current feed content.

Feeds that are unreachable, empty, or fail to parse are silently skipped;
the database is never modified for those feeds.

Requires ffetcher >= 4.0.0 (entries table with a feed_url column).
""",
    )

    parser.add_argument(
        "-d",
        "--database",
        required=True,
        help="Path to the ffetcher SQLite database file",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose debug output",
    )
    parser.add_argument(
        "--no-vacuum",
        action="store_true",
        help="Skip VACUUM after cleanup (VACUUM rebuilds the whole file and "
        "needs free disk space roughly equal to the database size; useful "
        "to skip on very large databases or constrained disks)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        metavar="SECONDS",
        help=f"Delay in seconds between feed downloads, to avoid hammering "
        f"a server that hosts several subscribed feeds (default: "
        f"{DEFAULT_DELAY_SECONDS}). Use 0 to disable.",
    )

    args = parser.parse_args()

    setup_logging(verbose=args.verbose)
    cleanup_database(
        args.database,
        verbose=args.verbose,
        vacuum=not args.no_vacuum,
        delay=args.delay,
    )


if __name__ == "__main__":
    main()