import hashlib
import logging
import sqlite3

# Module-level variable holding the database path, set by init_db().
_db_path = None


def init_db(db_path):
    """
    Set the database path and create the required tables if they do not exist.
    Must be called once at startup before any other db function.
    """
    global _db_path
    _db_path = db_path

    conn = sqlite3.connect(_db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS entries (
                id    INTEGER PRIMARY KEY AUTOINCREMENT,
                hash  TEXT NOT NULL,
                muc   TEXT NOT NULL,
                UNIQUE(hash, muc)
            )
            """
        )
        # Tracks which (feed_url, muc) pairs have been seen at least once.
        # Feeds absent from this table are treated as new and their articles
        # are silently recorded without being posted.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS known_feeds (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                feed_url TEXT NOT NULL,
                muc      TEXT NOT NULL,
                UNIQUE(feed_url, muc)
            )
            """
        )
        conn.commit()
        logging.info("Database ready: %s", _db_path)
    finally:
        conn.close()


def cleanup_feeds(feeds_config):
    """
    Remove from known_feeds any (feed_url, muc) pair that is no longer
    present in the current feeds configuration. This prevents stale records
    from causing articles to be posted when a feed is removed and re-added.
    """
    conn = sqlite3.connect(_db_path)
    try:
        rows = conn.execute("SELECT feed_url, muc FROM known_feeds").fetchall()
        removed = 0
        for feed_url, muc in rows:
            if muc not in feeds_config or feed_url not in feeds_config[muc]:
                conn.execute(
                    "DELETE FROM known_feeds WHERE feed_url = ? AND muc = ?",
                    (feed_url, muc),
                )
                logging.info("Removed stale feed from db: %s -> %s", feed_url, muc)
                removed += 1
        conn.commit()
        if removed:
            logging.info("Cleaned up %d stale feed record(s) from known_feeds", removed)
    finally:
        conn.close()


def is_known_feed(feed_url, muc):
    """
    Return True if this (feed_url, muc) pair has been seen before.
    On the first call for a new pair, record it and return False.
    """
    conn = sqlite3.connect(_db_path)
    try:
        try:
            conn.execute(
                "INSERT INTO known_feeds (feed_url, muc) VALUES (?, ?)",
                (feed_url, muc),
            )
            conn.commit()
            return False  # First time we see this feed.
        except sqlite3.IntegrityError:
            return True   # Already known.
    finally:
        conn.close()


def is_new_entry(link, muc):
    """
    Return True if this link has not been posted to this MUC before,
    and record it so future calls return False.
    Return False if it was already posted.
    """
    url_hash = hashlib.sha256(link.encode("utf-8")).hexdigest()

    conn = sqlite3.connect(_db_path)
    try:
        try:
            conn.execute(
                "INSERT INTO entries (hash, muc) VALUES (?, ?)",
                (url_hash, muc),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            # UNIQUE constraint failed: entry already recorded.
            return False
    finally:
        conn.close()