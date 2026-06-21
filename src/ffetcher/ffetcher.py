#!/usr/bin/env python3

import asyncio
import logging
import os

import slixmpp
from dotenv import load_dotenv

from .db import init_db, cleanup_feeds
from .feeds import get_new_articles
from .config import load_feeds

# How often to ping each MUC to verify we are still joined (seconds).
MUC_PING_INTERVAL = 60


class FeedBot(slixmpp.ClientXMPP):

    def __init__(self, jid, password, nick, feeds_config, interval,
                 summary_max_length, badwords, badwords_links, languages,
                 quote_summary, user_agent, show_images):
        slixmpp.ClientXMPP.__init__(self, jid, password)

        self.nick = nick
        self.feeds_config = feeds_config  # dict: {muc: [feed_url, ...]}
        self.interval = interval
        self.summary_max_length = summary_max_length
        self.badwords = badwords
        self.badwords_links = badwords_links
        self.languages = languages
        self.quote_summary = quote_summary
        self.user_agent = user_agent
        self.show_images = show_images

        self.add_event_handler("session_start", self.on_start)
        self.add_event_handler("disconnected", self.on_disconnect)

    async def on_start(self, event):
        await self.get_roster()
        self.send_presence()

        for muc in self.feeds_config:
            self.plugin["xep_0045"].join_muc(muc, self.nick)
            logging.info("Joined MUC: %s", muc)

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
        """
        while True:
            await asyncio.sleep(MUC_PING_INTERVAL)
            for muc in self.feeds_config:
                await self._ping_muc(muc)

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
        for muc, feed_urls in self.feeds_config.items():
            for feed_url in feed_urls:
                try:
                    articles = await get_new_articles(
                        feed_url, muc, self.summary_max_length,
                        self.badwords, self.badwords_links,
                        self.user_agent, self.languages
                    )
                except Exception as e:
                    logging.error("Error fetching feed %s: %s", feed_url, e)
                    continue

                for article in articles:
                    self.send_message(
                        mto=muc,
                        mbody=self.format_message(article),
                        mtype="groupchat",
                    )
                    await asyncio.sleep(1)

                    # Send each image URL as a separate message so that
                    # clients with inline preview can display it directly.
                    if self.show_images:
                        for image_url in article["images"]:
                            self.send_message(
                                mto=muc,
                                mbody=image_url,
                                mtype="groupchat",
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
    load_dotenv()

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

    feeds_config, badwords, badwords_links, languages = load_feeds(feeds_file)
    if not feeds_config:
        raise SystemExit("No feeds configured. Check %s" % feeds_file)

    # Remove stale feed records from the database. This ensures that feeds
    # removed from feeds.ini and later re-added are treated as new, preventing
    # articles from being posted as if the feed had never been seen.
    cleanup_feeds(feeds_config)

    bot = FeedBot(jid, password, nick, feeds_config, interval,
                  summary_max_length, badwords, badwords_links, languages,
                  quote_summary, user_agent, show_images)
    bot.register_plugin("xep_0030")  # Service Discovery
    bot.register_plugin("xep_0045")  # Multi-User Chat
    bot.register_plugin("xep_0199")  # XMPP Ping

    bot.connect()
    asyncio.get_event_loop().run_forever()


if __name__ == "__main__":
    main()