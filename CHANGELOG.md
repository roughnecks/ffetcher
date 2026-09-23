# Changelog

All notable changes to this project are documented in this file.

## [4.0.0]

### Fixed
- **Deduplication bug**: entries carried no record of which feed produced each one. When a MUC receives articles from more than one feed, this made it impossible to tell which feed an already-seen article belonged to. In practice, this caused a maintenance/cleanup pass scoped to a single feed to incorrectly delete entries belonging to *other* feeds sharing the same MUC, which then caused those articles to be reposted as if new on the bot's next run.

### Changed
- **BREAKING**: the `entries` table now has a `feed_url` column. The uniqueness constraint remains `(hash, muc)`, so an article shared by more than one feed subscribed to the same MUC is still posted only once, exactly as before — `feed_url` simply records which feed's copy of the article "won" that single post. This is what lets maintenance tools scope their work to one feed's own entries without touching entries recorded under a different feed.
- **BREAKING**: `is_new_entry()` now requires a `feed_url` argument.
- Existing databases created by previous versions are **not compatible** and are not automatically migrated. Delete the old database file (`BOT_DB_PATH`) before running this version; it will be recreated with the new schema on first start. All feeds will be re-seeded (recorded silently, not posted) on that first run, so no old articles will flood your rooms.

### Added
- New standalone maintenance script `scripts/ffetcher_db_cleanup.py`: prunes database entries that are no longer present in their source feed. Scoped per `(feed_url, muc)`, so it is safe to use even when several feeds post into the same MUC. See `scripts/ffetcher_db_cleanup_README.md`.

## [3.1.0]

### Added
- 1:1 chat delivery: sections prefixed with `chat:` in `feeds.ini` send articles as private messages instead of groupchat
- Feed deduplication: feeds shared across multiple destinations are now downloaded only once per cycle

## [3.0.0]

### Added
- pipx packaging support (`src/ffetcher/` layout, `pyproject.toml`, `ffetcher` entry point)
- `.env` and `feeds.ini` are now auto-generated with default placeholder content on first run if missing, instead of failing with an error

### Changed
- Renamed the package's main module to `__main__.py`, enabling `python -m ffetcher` and removing the redundant `ffetcher.ffetcher` entry point path
- venv + pip installation now uses `pip install .` instead of `pip install -r requirements.txt`, exposing the `ffetcher` command within the venv instead of running a standalone script
- Updated the example systemd unit to call the installed `ffetcher` binary instead of `python3 ffetcher.py`

### Removed
- `requirements.txt`: dependencies are now declared exclusively in `pyproject.toml`

## [2.1.1]

- RSS/Atom feed monitoring with per-MUC feed configuration
- Anti-flood seeding for newly added feeds
- SQLite-based deduplication of published entries
- Configurable excerpt length and optional XEP-0393 block quote formatting
- `[badwords]` and `[badwords_links]` filter sections
- `[languages]` filter section with automatic language detection
- Inline image posting (`BOT_SHOW_IMAGES`)
- MUC reconnection via XEP-0199 ping loop
- Async feed fetching with timeout to avoid blocking the event loop
- Mastodon-specific HTML cleanup (hashtags, mentions, custom emoji, broken links, gemini:// support)
