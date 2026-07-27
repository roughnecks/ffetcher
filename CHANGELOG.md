# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]
- 1:1 chat delivery: sections prefixed with `chat:` in `feeds.ini` send articles as private messages instead of groupchat

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