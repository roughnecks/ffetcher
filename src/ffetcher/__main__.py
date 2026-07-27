#!/usr/bin/env python3
# This module is invoked both as `python -m ffetcher` and via the `ffetcher`
# console script installed by pipx/pip (see pyproject.toml).

import asyncio
import logging
import os
from collections import defaultdict

import slixmpp
from dotenv import load_dotenv, find_dotenv

from .db import init_db, cleanup_feeds
from .feeds import get_new_articles
from .config import load_feeds

# How often to ping each MUC to verify we are still joined (seconds).
MUC_PING_INTERVAL = 60

# Default content written to .env when it does not exist yet.
ENV_TEMPLATE = """BOT_JID=ffetcher@example.com
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
"""

# Default content written to feeds.ini when it does not exist yet.
FEEDS_INI_TEMPLATE = """# Each section name is the JID of a MUC room.
# Each key is an arbitrary label; its value is a feed URL.
# A MUC can have any number of feeds.
[room-one@conference.example.com]
feed1 = https://www.debian.org/News/news
feed2 = https://www.debian.org/security/dsa-long
[room-two@conference.example.com]
feed1 = https://other.example.com/atom.xml
# For 1:1 chat delivery, prefix the section name with "chat:".
[chat:user@example.com]
feed1 = https://other.example.com/atom.xml
# Articles whose title or body text contain any of these words will be
# silently dropped. Matching is case-insensitive and whole-word only,
# so "casino" will not match "casinotto". This section is optional.
[badwords]
word1 = casino
word2 = sponsor
word3 = giveaway
# Articles whose link contains any of these strings will be silently
# dropped. Matching is case-insensitive substring search, which is more
# reliable for URLs (e.g. to silence a specific account). This section
# is optional.
[badwords_links]
word1 = @someaccount
word2 = example.com/airport
# Only post articles written in these languages (ISO 639-1 codes).
# If this section is absent or empty, all languages are accepted.
# Detection is automatic and may occasionally misidentify very short texts.
[languages]
lang1 = en
lang2 = it
"""


def _scaffold_config(env_path, feeds_path):
    """
    Create .env and feeds.ini with default template content if they do not
    already exist in the current working directory. This allows a first-time
    user (especially under pipx, where the example files are not otherwise
    available) to get a working starting point without cloning the repository.

    Returns True if at least one file was created, so the caller can exit
    and let the user edit the new files before starting the bot for real.
    """
    created = False

    if not os.path.exists(env_path):
        with open(env_path, "w", encoding="utf-8") as f:
            f.write(ENV_TEMPLATE)
        print("Created %s with default values. Edit it before running ffetcher again." % env_path)
        created = True

    if not os.path.exists(feeds_path):
        with open(feeds_path, "w", encoding="utf-8") as f:
            f.write(FEEDS_INI_TEMPLATE)
        print("Created %s with default values. Edit it before running ffetcher again." % feeds_path)
        created = True

    return created


def _build_feed_map(feeds_config):
    """
    Invert the feeds_config dict to build a map from feed URL to the list
    of destination JIDs that subscribe to it. This allows each feed to be
    downloaded only once even when it appears in multiple destinations.

    Input:  {jid: [feed_url, ...]}
    Output: {feed_url: [jid, ...]}
    """
    feed_map = defaultdict(list)
    for jid, feed_urls in feeds_config.items():
        for feed_url in feed_urls:
            feed_map[feed_url].append(jid)
    return feed_map


class FeedBot(slixmpp.ClientXMPP):

    def __init__(self, jid, password, nick, feeds_config, message_types,
                 interval, summary_max_length, badwords, badwords_links,
                 languages, quote_summary, user_agent, show_images):
        slixmpp.ClientXMPP.__init__(self, jid, password)

        self.nick = nick
        self.feeds_config = feeds_config    # dict: {jid: [feed_url, ...]}
        self.message_types = message_types  # dict: {jid: "groupchat"|"chat"}
        self.interval = interval
        self.summary_max_length = summary_max_length
        self.badwords = badwords
        self.badwords_links = badwords_links
        self.languages = languages
        self.quote_summary = quote_summary
        self.user_agent = user_agent
        self.show_images = show_images

        # Inverted map: {feed_url: [jid, ...]} — each feed is downloaded once.
        self.feed_map = _build_feed_map(feeds_config)

        self.add_event_handler("session_start", self.on_start)
        self.add_event_handler("disconnected", self.on_disconnect)

    async def on_start(self, event):
        await self.get_roster()
        self.send_presence()

        # Join only MUC destinations; 1:1 chats need no join step.
        for jid, msg_type in self.message_types.items():
            if msg_type == "groupchat":
                self.plugin["xep_0045"].join_muc(jid, self.nick)
                logging.info("Joined MUC: %s", jid)

        # Wait a moment before starting loops, to let MUC joins settle.
        await asyncio.sleep(5)
        asyncio.ensure_future(self.feed_loop())
        asyncio.ensure_future(self.muc_ping_loop())

    async def feed_loop(self):
        while True:
            await self.check_feeds()
            await asyncio.sleep(self.interval)

    async def muc_ping_loop(self):
        """
        Periodically ping each MUC to verify we are still joined.
        If a ping fails, attempt to rejoin the room.
        1:1 chats are skipped as they need no join state.
        """
        while True:
            await asyncio.sleep(MUC_PING_INTERVAL)
            for jid, msg_type in self.message_types.items():
                if msg_type == "groupchat":
                    await self._ping_muc(jid)

    async def _ping_muc(self, muc):
        """
        Send a XEP-0199 ping to our own participant in the MUC.
        If the ping times out or returns an error, attempt to rejoin.
        """
        target = "%s/%s" % (muc, self.nick)
        try:
            await self.plugin["xep_0199"].ping(target, timeout=10)
            logging.debug("MUC ping OK: %s", muc)
        except Exception as e:
            logging.warning("MUC ping failed for %s (%s), attempting rejoin...", muc, e)
            try:
                self.plugin["xep_0045"].join_muc(muc, self.nick)
                logging.info("Rejoined MUC: %s", muc)
            except Exception as rejoin_err:
                logging.error("Failed to rejoin MUC %s: %s", muc, rejoin_err)

    async def check_feeds(self):
        # Iterate by feed URL so each feed is downloaded only once,
        # even when it appears in multiple destinations.
        for feed_url, jids in self.feed_map.items():
            for jid in jids:
                try:
                    articles = await get_new_articles(
                        feed_url, jid, self.summary_max_length,
                        self.badwords, self.badwords_links,
                        self.user_agent, self.languages
                    )
                except Exception as e:
                    logging.error("Error fetching feed %s: %s", feed_url, e)
                    continue

                for article in articles:
                    msg_type = self.message_types[jid]
                    self.send_message(
                        mto=jid,
                        mbody=self.format_message(article),
                        mtype=msg_type,
                    )
                    await asyncio.sleep(1)

                    # Send each image URL as a separate message so that
                    # clients with inline preview can display it directly.
                    if self.show_images:
                        for image_url in article["images"]:
                            self.send_message(
                                mto=jid,
                                mbody=image_url,
                                mtype=msg_type,
                            )
                            await asyncio.sleep(1)

            # Delay between feed downloads to avoid hammering servers.
            await asyncio.sleep(30)

    def format_message(self, article):
        parts = ["*" + article["title"] + "*"]
        if article["summary"]:
            if self.quote_summary:
                # Format summary as a block quote (XEP-0393).
                # Each line is prefixed with "> " so supporting clients
                # render it as a visual quotation block.
                quoted = "\n".join("> " + line for line in article["summary"].splitlines())
                parts.append(quoted)
            else:
                parts.append(article["summary"])
        # Empty line before the link.
        parts.append("")
        parts.append(article["link"])
        return "\n".join(parts)

    def on_disconnect(self, event):
        logging.warning("Disconnected, reconnecting...")
        self.reconnect()


def main():
    # If .env or feeds.ini do not exist in the current working directory,
    # create them with default content and exit, so a first-time user
    # (especially under pipx) has something to edit instead of an error.
    if _scaffold_config(".env", "feeds.ini"):
        return

    # usecwd=True ensures .env is searched starting from the directory the
    # user runs the command from, rather than from the installed package
    # location (relevant when ffetcher is installed via pipx, since the
    # package then lives under ~/.local/pipx/venvs/ rather than the user's
    # working directory).
    load_dotenv(find_dotenv(usecwd=True))

    log_level  = os.getenv("BOT_LOG_LEVEL", "INFO").upper()
    log_file   = os.getenv("BOT_LOG_FILE", "bot.log")

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(),
        ],
    )
    logging.captureWarnings(True)

    jid        = os.getenv("BOT_JID")
    password   = os.getenv("BOT_PASSWORD")
    nick       = os.getenv("BOT_NICK")
    db_path    = os.getenv("BOT_DB_PATH", "./feeds.db")
    interval   = int(os.getenv("BOT_INTERVAL", "600"))
    feeds_file = os.getenv("BOT_FEEDS_FILE", "./feeds.ini")
    summary_max_length = int(os.getenv("BOT_SUMMARY_MAX_LENGTH", "600"))
    quote_summary = os.getenv("BOT_QUOTE_SUMMARY", "false").lower() == "true"
    show_images = os.getenv("BOT_SHOW_IMAGES", "false").lower() == "true"
    user_agent = os.getenv("BOT_USER_AGENT", "ffetcher/1.0 +https://example.com")

    if not all([jid, password, nick]):
        raise SystemExit("BOT_JID, BOT_PASSWORD and BOT_NICK must be set in .env")

    init_db(db_path)

    feeds_config, message_types, badwords, badwords_links, languages = load_feeds(feeds_file)
    if not feeds_config:
        raise SystemExit("No feeds configured. Check %s" % feeds_file)

    # Remove stale feed records from the database. This ensures that feeds
    # removed from feeds.ini and later re-added are treated as new, preventing
    # articles from being posted as if the feed had never been seen.
    cleanup_feeds(feeds_config)

    bot = FeedBot(jid, password, nick, feeds_config, message_types, interval,
                  summary_max_length, badwords, badwords_links, languages,
                  quote_summary, user_agent, show_images)
    bot.register_plugin("xep_0030")  # Service Discovery
    bot.register_plugin("xep_0045")  # Multi-User Chat
    bot.register_plugin("xep_0199")  # XMPP Ping

    bot.connect()
    asyncio.get_event_loop().run_forever()


if __name__ == "__main__":
    main()