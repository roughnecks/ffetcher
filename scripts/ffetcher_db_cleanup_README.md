# ffetcher Database Cleanup Script

A standalone Python script that removes old entries from the ffetcher database that are no longer present in their source feeds.

**Requires ffetcher >= 4.0.0.** This version introduced a `feed_url` column on every `entries` row, which is what makes this script safe to use even when multiple feeds post into the same MUC. Databases created by older ffetcher versions are not compatible — see the main [CHANGELOG](../CHANGELOG.md) for details.

## What It Does

For each registered feed subscription `(feed_url, muc)` pair:

1. Downloads and parses the current feed XML
2. Extracts all article links currently present
3. Computes SHA256 hashes (using the same algorithm as ffetcher)
4. Compares with the database entries belonging to **that specific feed**
5. **Deletes** any entries for that feed that no longer exist in its current content

Because entries are matched per `(feed_url, muc)` and not just per `muc`, a MUC that receives articles from several different feeds is handled correctly: cleaning up one feed never touches entries belonging to another feed posting into the same room.

## Features

- **Safe**: unreachable, empty, or malformed feeds are silently skipped — the database is never modified for those
- **Feed-accurate**: entries are pruned per-feed, not per-MUC, so shared destination rooms are handled correctly
- **Standalone**: works independently from the ffetcher bot; can run while the bot is offline
- **Minimal dependencies**: only requires `feedparser` (already a ffetcher dependency)
- **Structured logging**: verbose debug output available with `-v`
- **Reclaims disk space**: runs `VACUUM` after any deletion, so the `.db` file actually shrinks instead of just having unused space internally
- **Server-friendly**: waits 30 seconds (configurable) between feed downloads, so subscribing to several feeds on the same server (e.g. multiple Mastodon tags) doesn't hit it with back-to-back requests
- **Cron-friendly**: simple CLI interface, suitable for scheduled execution

## Quick Start

The database lives wherever `BOT_DB_PATH` points (default `./feeds.db`, relative to the directory you run the bot from) — the same place as your `.env` and `feeds.ini`.

**If you cloned the ffetcher repository and run the bot from there:**

```bash
cd /path/to/ffetcher
python3 scripts/ffetcher_db_cleanup.py -d feeds.db
```

**If you installed ffetcher via pipx and don't have the repository:**

Download the script once into the same directory where you run the bot (where `.env`, `feeds.ini` and `feeds.db` already live), then run it from there:

```bash
cd /path/to/your/ffetcher-run-dir
curl -o ffetcher_db_cleanup.py \
  https://code.woodpeckersnest.space/roughnecks/ffetcher/raw/branch/main/scripts/ffetcher_db_cleanup.py

python3 ffetcher_db_cleanup.py -d feeds.db
```

(`feedparser` is a ffetcher dependency, so it's already available wherever ffetcher itself runs.)

## Usage

### Basic cleanup

```bash
python3 ffetcher_db_cleanup.py -d feeds.db
```

### With verbose output (debug logging)

```bash
python3 ffetcher_db_cleanup.py -d feeds.db -v
```

### Skip VACUUM (for very large databases or limited disk space)

```bash
python3 ffetcher_db_cleanup.py -d feeds.db --no-vacuum
```

### Adjust or disable the delay between feed downloads

```bash
# Shorter delay (10 seconds)
python3 ffetcher_db_cleanup.py -d feeds.db --delay 10

# No delay at all (only if your feeds are spread across different servers)
python3 ffetcher_db_cleanup.py -d feeds.db --delay 0
```

### Help

```bash
python3 ffetcher_db_cleanup.py -h
```

## Scheduling with Cron

Cron doesn't run from any particular directory, so use `cd` to the directory where `.env`, `feeds.ini` and `feeds.db` live, then reference `feeds.db` (or `BOT_DB_PATH`, if you set it to something else) as a relative path — or just spell out its absolute path directly.

With the default 30-second delay between feeds, a run with many subscriptions can take several minutes (see [Delay Between Feed Downloads](#delay-between-feed-downloads) above) — keep that in mind when picking a cron schedule so consecutive runs don't overlap.

**If you cloned ffetcher and run the bot from the repo:**

```bash
0 3 * * * cd /path/to/ffetcher && python3 scripts/ffetcher_db_cleanup.py -d feeds.db >> cleanup.log 2>&1
```

**If you installed via pipx** (script saved as shown in Quick Start, in your bot's run directory):

```bash
0 3 * * * cd /path/to/your/ffetcher-run-dir && python3 ffetcher_db_cleanup.py -d feeds.db >> cleanup.log 2>&1
```

**With a local venv:**

```bash
0 3 * * * cd /path/to/ffetcher && /path/to/venv/bin/python3 scripts/ffetcher_db_cleanup.py -d feeds.db >> cleanup.log 2>&1
```

## Database Behavior

### What gets deleted?

An entry is deleted when, for its specific `(feed_url, muc)` pair, its link hash is no longer present in that feed's current XML content.

### What is preserved?

- Entries whose link still exists in their feed's current content
- Entries belonging to *other* feeds, even if they share the same destination MUC
- All `known_feeds` records (subscription list)
- Database structure — tables are never dropped

### What if a feed is unreachable, times out, or fails to parse?

The script **skips that feed entirely**. No database modification happens for it. Running the script while some feeds are temporarily down is safe.

### What if a feed returns zero articles?

Treated the same as unreachable: skipped, to avoid ever wiping all entries for a feed based on a transient empty response.

## Disk Space Reclamation (VACUUM)

SQLite doesn't shrink a database file when rows are deleted — the freed pages stay inside the file, marked as reusable for future inserts, but the file on disk stays the same size. After a cleanup that removes many entries, the `.db` file itself wouldn't get any smaller without an explicit `VACUUM`.

This script runs `VACUUM` automatically **after** cleanup, but only if at least one entry was actually deleted (skipped entirely if nothing changed, since `VACUUM` on an unchanged database is pure overhead).

**Trade-offs to be aware of:**

- `VACUUM` rewrites the entire database file, so it temporarily needs roughly as much free disk space as the database's current size
- On a very large database, it can take noticeably longer than the cleanup itself
- The database is briefly locked (exclusive access) during the `VACUUM`

If your `feeds.db` is small (a typical ffetcher install with a handful of feeds), this is a non-issue and finishes near-instantly. If you're running this on a constrained system or a very large database, use `--no-vacuum` and run `VACUUM` manually/less frequently instead:

```bash
sqlite3 feeds.db "VACUUM;"
```

## Delay Between Feed Downloads

Many ffetcher setups subscribe to several feeds hosted on the same server — for example, multiple Mastodon hashtag feeds on the same instance (`.../tags/homelab.rss`, `.../tags/xmpp.rss`, etc.). Fetching all of them back-to-back could look like a small burst of requests to that server.

To be a considerate client, the script waits **30 seconds by default** between one feed download and the next (no delay is applied before the first feed or after the last one). At startup, it logs an approximate total runtime based on the number of feeds and the delay, so you know what to expect before it's done — for example, with 18 feeds and the default 30s delay:

```
Processing 18 feed(s)
With a 30s delay between feeds, this will take at least ~8m30s to complete (plus actual download/parse time per feed)
```

This is a lower bound: actual runtime also includes the time each feed takes to respond and parse.

Adjust it with `--delay SECONDS`, or set `--delay 0` to disable it entirely (only recommended if your feeds are spread across different servers, or you know the server can handle it).

## Output Example

```
2026-09-21 03:00:00 - INFO - Starting database cleanup
2026-09-21 03:00:00 - INFO - Processing 18 feed(s)
2026-09-21 03:00:00 - INFO - With a 30s delay between feeds, this will take at least ~8m30s to complete (plus actual download/parse time per feed)
2026-09-21 03:00:01 - INFO - Feed cleanup: https://mastodon.social/tags/homelab.rss -> bots@chat.woodpeckersnest.space: deleted 2 entry(ies)
2026-09-21 03:08:32 - INFO - Cleanup complete: 17 feed(s) processed, 1 skipped, 14 total entries deleted
2026-09-21 03:08:32 - INFO - Reclaiming disk space...
2026-09-21 03:08:32 - INFO - VACUUM complete: 0.42 MB reclaimed (1.87 MB -> 1.45 MB)
```

With `-v`, you also get debug-level output for every feed processed, including feeds with nothing to delete.

## Database Schema (ffetcher >= 4.0.0)

```sql
CREATE TABLE known_feeds (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_url TEXT NOT NULL,
    muc      TEXT NOT NULL,
    UNIQUE(feed_url, muc)
);

CREATE TABLE entries (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    hash     TEXT NOT NULL,
    muc      TEXT NOT NULL,
    feed_url TEXT NOT NULL,
    UNIQUE(hash, muc)
);
```

Each entry's `hash` is `SHA256(article_link)`, matching the bot's own logic. `feed_url` records which feed's copy of an article was the one actually posted (relevant when the same article is shared by more than one feed subscribed to the same MUC — see Edge Cases below); it is not part of the uniqueness constraint, so cross-feed deduplication into a shared MUC still works exactly as before. The script refuses to run against a database missing the `feed_url` column (i.e. one created by ffetcher < 4.0.0) and prints an explanatory error instead of silently doing the wrong thing.

## Edge Cases

### A feed's URL changes in `feeds.ini`

The old URL, if still present in `known_feeds`, is processed independently from the new one. Restart the bot first (it prunes stale `known_feeds` records via `cleanup_feeds()` on startup) before running this script, for full consistency.

### An article is shared by two feeds subscribed to the same MUC

ffetcher posts it only once (this has always been the behavior): whichever feed is processed first "wins" and its `feed_url` is recorded for that entry. If, later on, that winning feed rotates the article out of its own XML while the *other* feed still carries it, running this script will delete the entry (since it's scoped to the winning feed's own current content). If the bot then re-processes the other feed and finds the article still there, it will be reposted once. This is a rare edge case that only arises if you run this script; it never happens during ffetcher's normal operation.

### Non-ASCII characters in links

Handled correctly; links are hashed as UTF-8, matching the bot exactly.

## Troubleshooting

**"Database file not found"**
Check the path passed to `-d`.

**"The 'entries' table has no 'feed_url' column"**
Your database predates ffetcher 4.0.0. This script cannot be used against it. See the CHANGELOG for the required upgrade path (fresh database on first run of 4.0.0).

**"No feeds found in database"**
No subscriptions yet, or the bot hasn't been run since `feeds.ini` was configured.

**Script seems slow / a feed hangs**
Each feed fetch is capped by `feedparser`'s own network timeout behavior; a very slow server may still take a while before failing. Consider checking that feed's health separately if it's consistently slow.

**`ModuleNotFoundError: No module named 'feedparser'`**
Run the script using the same Python environment ffetcher itself uses (its venv, or wherever pipx installed it), since `feedparser` lives there.

## License

Same as ffetcher (UNLICENSE).