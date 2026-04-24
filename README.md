# ffetcher (feed fetcher)

An XMPP bot that monitors RSS and Atom feeds and posts new articles to one or more MUC rooms.

---

<img src="./ffetcher.webp" width="400">


## Features

ffetcher monitors a list of RSS and Atom feeds and delivers new articles to XMPP group chats. Each room has its own independent list of feeds, so different communities can follow different sources without overlap.

When a new feed is added, ffetcher silently reads through the existing articles without posting them, so the room is not flooded on first run. From that point on, only genuinely new articles are delivered.

Articles are posted with a plain-text excerpt alongside the link. The excerpt length is configurable, and optionally the text can be formatted as a block quote for clients that support it. Article titles are displayed in bold where supported.

A word filter allows specific terms to be silently blocked. If any of the configured words appears in an article's body or its link, the article is dropped without being posted. This makes it easy to mute specific accounts, topics or domains across all feeds at once.

ffetcher is careful not to hammer feed servers: there is a configurable delay between consecutive feed downloads, and a custom User-Agent string identifies the bot to server administrators.

---

## Requirements

- Python 3.10 or newer
- An XMPP account for the bot
- At least one MUC the bot account is allowed to join

---

## Installation

```sh
tar xzf ffetcher-main.tar.gz
cd ffetcher
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## Configuration

ffetcher uses two configuration files: `.env` for credentials and bot settings, and `feeds.ini` for the feed list.

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
| `BOT_USER_AGENT` | User-Agent string sent when downloading feeds. Include a contact URL so server administrators can reach you | `ffetcher/1.0 +https://example.com` |
| `BOT_LOG_LEVEL` | Log level: DEBUG, INFO, WARNING, ERROR | `INFO` |
| `BOT_LOG_FILE` | Path to the log file | `./bot.log` |

Example:

```ini
BOT_JID=feedbot@example.com
BOT_PASSWORD=changeme
BOT_NICK=feedbot
BOT_DB_PATH=./feeds.db
BOT_FEEDS_FILE=./feeds.ini
BOT_INTERVAL=600
BOT_SUMMARY_MAX_LENGTH=600
BOT_QUOTE_SUMMARY=false
BOT_USER_AGENT=ffetcher/1.0 +https://example.com
BOT_LOG_LEVEL=INFO
BOT_LOG_FILE=./bot.log
```

### feeds.ini

Each section name is a MUC JID. Each key is an arbitrary label and its value is a feed URL. A MUC can have any number of feeds.

The optional `[badwords]` section contains words that, if found in an article's body or link, cause the article to be silently dropped. Matching is case-insensitive and whole-word only for body text, and substring-based for links, so `@account` will match any link containing that string.

```ini
[room-one@conference.example.com]
feed1 = https://www.debian.org/News/news
feed2 = https://www.debian.org/security/dsa-long

[room-two@conference.example.com]
feed1 = https://other.example.com/atom.xml

[badwords]
word1 = casino
word2 = sponsor
word3 = @someaccount
```

Changes to `feeds.ini` take effect after restarting the bot. New feeds or MUCs added later are automatically seeded without flooding on their first run.

---

## Running

```sh
source venv/bin/activate
python3 bot.py
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
ExecStart=/path/to/ffetcher/venv/bin/python3 bot.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Then enable and start it:

```sh
systemctl enable ffetcher
systemctl start ffetcher
```