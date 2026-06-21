# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

### Added
- pipx packaging support (`src/ffetcher/` layout, `pyproject.toml`, `ffetcher` entry point)

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