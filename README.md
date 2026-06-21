# ffetcher (feed fetcher)

An XMPP bot that monitors RSS and Atom feeds and posts new articles to one or more MUC rooms. It specializes on fediverse feeds.
This code is AI generated and reviewed/tested by the author.

---

<img src="./ffetcher.webp" width="400">


## Features

ffetcher monitors a list of RSS and Atom feeds and delivers new articles to XMPP group chats. Each room has its own independent list of feeds, so different communities can follow different sources without overlap.

When a new feed is added, ffetcher silently reads through the existing articles without posting them, so the room is not flooded on first run. From that point on, only genuinely new articles are delivered.

Articles are posted with a plain-text excerpt alongside the link. The excerpt length is configurable, and optionally the text can be formatted as a block quote for clients that support it. Article titles are displayed in bold where supported.

A word filter allows specific terms to be silently blocked. If any of the configured words appears in an article's body or its link, the article is dropped without being posted. This makes it easy to mute specific accounts, topics or domains across all feeds at once.

A language filter allows only articles written in the configured languages to be posted. Language detection is automatic and based on the article text.

ffetcher is careful not to hammer feed servers: there is a configurable delay between consecutive feed downloads, and a custom User-Agent string identifies the bot to server administrators.

---

## Requirements

- Python 3.10 or newer
- An XMPP account for the bot
- At least one MUC the bot account is allowed to join

---

## Installation

ffetcher can be installed either with pipx (recommended for running it as a standalone command) or with a classic venv + pip setup (recommended if you want to edit the code directly).

### Option A: pipx

[pipx](https://pipx.pypa.io/) installs ffetcher into its own isolated environment and exposes the `ffetcher` command globally, without affecting your system Python packages.

Install the latest version from the main branch:

```sh
pipx install git+https://code.woodpeckersnest.space/roughnecks/ffetcher.git
```

Install a specific branch:

```sh
pipx install git+https://code.woodpeckersnest.space/roughnecks/ffetcher.git@branch-name
```

Install a specific tag (release):

```sh
pipx install git+https://code.woodpeckersnest.space/roughnecks/ffetcher.git@v1.0.0
```

Install a specific commit:

```sh
pipx install git+https://code.woodpeckersnest.space/roughnecks/ffetcher.git@<commit-hash>
```

To install the optional language filter support ([languages] section in feeds.ini):

```sh
pipx install "ffetcher[languages] @ git+https://code.woodpeckersnest.space/roughnecks/ffetcher.git"
```

To upgrade or switch to a different branch/tag/commit later, add `--force`:

```sh
pipx install --force git+https://code.woodpeckersnest.space/roughnecks/ffetcher.git@branch-name
```

If the `--force` option doesn't seem to work, just uninstall and reinstall again with `pipx`

```sh
pipx uninstall ffetcher
```

Once installed, create a working directory for your configuration files (see [Configuration](#configuration) below) and run:

```sh
ffetcher
```

### Option B: venv + pip

* If you need language filters, please uncomment the last line in `requirements.txt` file before continuing to install with `pip`

```sh
git clone https://code.woodpeckersnest.space/roughnecks/ffetcher.git
cd ffetcher
python3 -m venv .venv
source .venv/bin/activate
pip install .
```

This installs ffetcher and its dependencies into the venv, and exposes the `ffetcher` command within it (see [Running](#running) below).

---

## Configuration

ffetcher uses two configuration files: `.env` for credentials and bot settings, and `feeds.ini` for the feed list.

If either file is missing from the directory you run ffetcher from, it will create both with default placeholder content and exit, so you have something to edit instead of an error. This applies to both the pipx and venv + pip installation methods.

```sh
ffetcher
# Created .env with default values. Edit it before running ffetcher again.
# Created feeds.ini with default values. Edit it before running ffetcher again.
```

Alternatively, with the venv + pip setup you can still copy the example files from the repository directly:

```sh
cp .env.example .env
cp feeds.ini.example feeds.ini
```

Edit both files before starting the bot. With pipx, place these files in the directory from which you run the `ffetcher` command — they are not stored inside the pipx-managed environment.

### .env

| Variable | Description | Default |
|---|---|---|
| `BOT_JID` | Full JID of the bot account | *(required)* |
| `BOT_PASSWORD` | Password for the bot account | *(required)* |
| `BOT_NICK` | Nickname used in MUC rooms | *(required)* |
| `BOT_DB_PATH` | Path to the SQLite database file | `./feeds.db` |
| `BOT_FEEDS_FILE` | Path to the feeds configuration file | `./feeds.ini` |
| `BOT_INTERVAL` | Feed check interval in seconds | `600` |
| `BOT_SUMMARY_MAX_LENGTH` | Maximum length of article excerpts in characters | `600` |
| `BOT_QUOTE_SUMMARY` | Format the article excerpt as a block quote (XEP-0393). Set to `true` only if your XMPP client supports XEP-0393, otherwise the `>` characters will appear as literal text | `false` |
| `BOT_SHOW_IMAGES` | Send image attachments found in articles as separate messages, for clients that support inline preview | `false` |
| `BOT_USER_AGENT` | User-Agent string sent when downloading feeds. Include a contact URL so server administrators can reach you | `ffetcher/1.0 +https://example.com` |
| `BOT_LOG_LEVEL` | Log level: DEBUG, INFO, WARNING, ERROR | `INFO` |
| `BOT_LOG_FILE` | Path to the log file | `./bot.log` |

Example:

```ini
BOT_JID=ffetcher@example.com
BOT_PASSWORD=changeme
BOT_NICK=ffetcher
BOT_DB_PATH=./feeds.db
BOT_FEEDS_FILE=./feeds.ini
BOT_INTERVAL=600
BOT_SUMMARY_MAX_LENGTH=600
BOT_QUOTE_SUMMARY=false
BOT_SHOW_IMAGES=false
BOT_USER_AGENT=ffetcher/1.0 +https://example.com
BOT_LOG_LEVEL=INFO
BOT_LOG_FILE=./bot.log
```

### feeds.ini

Each section name is a MUC JID. Each key is an arbitrary label and its value is a feed URL. A MUC can have any number of feeds.

The optional `[badwords]` section contains words that, if found as a whole word in an article's title or body text, cause the article to be silently dropped. Matching is case-insensitive and whole-word only.

The optional `[badwords_links]` section contains strings that, if found anywhere in an article's link, cause the article to be silently dropped. Matching is case-insensitive substring search, which is more reliable for URLs, for example to silence a specific account with `@account`.

The optional `[languages]` section contains ISO 639-1 language codes (e.g. `en`, `it`, `de`) for the languages you want to receive. If this section is absent or empty, all languages are accepted and no filtering takes place.

```ini
[room-one@conference.example.com]
feed1 = https://www.debian.org/News/news
feed2 = https://www.debian.org/security/dsa-long

[room-two@conference.example.com]
feed1 = https://other.example.com/atom.xml

[badwords]
word1 = casino
word2 = sponsor
word3 = giveaway

[badwords_links]
word1 = @someaccount
word2 = example.com/airport

[languages]
lang1 = en
```

Changes to `feeds.ini` take effect after restarting the bot. New feeds or MUCs added later are automatically seeded without flooding on their first run. Feeds removed from `feeds.ini` are cleaned up from the database on the next restart, so re-adding them later will correctly trigger a fresh seed.

---

## Running

### With pipx

```sh
ffetcher
```

### With venv + pip

```sh
source .venv/bin/activate
ffetcher
```

*To run the bot in the background you can use `screen`, `tmux`, or a systemd service.*

### Example systemd service

Create `/etc/systemd/system/ffetcher.service`:

```ini
[Unit]
Description=ffetcher XMPP feed bot
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/path/to/ffetcher
ExecStart=/path/to/ffetcher/.venv/bin/ffetcher
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

If installed via pipx, point `ExecStart` to the pipx-managed binary instead:

```ini
ExecStart=/home/youruser/.local/bin/ffetcher
```

Then enable and start it:

```sh
systemctl enable --now ffetcher.service
```