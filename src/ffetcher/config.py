import configparser
import logging

# Section name reserved for text badwords in feeds.ini.
BADWORDS_SECTION = "badwords"

# Section name reserved for link badwords in feeds.ini.
BADWORDS_LINKS_SECTION = "badwords_links"

# Section name reserved for allowed languages in feeds.ini.
LANGUAGES_SECTION = "languages"

# Prefix used to mark 1:1 chat sections in feeds.ini.
CHAT_PREFIX = "chat:"


def load_feeds(feeds_file):
    """
    Load feeds, badwords and allowed languages from an INI file.

    Section names are either MUC JIDs or 1:1 chat JIDs prefixed with
    "chat:" (e.g. [chat:user@example.com]). The prefix determines the
    message type used when posting: groupchat for MUCs, chat for 1:1.

    Reserved section names: [badwords], [badwords_links], [languages].

    The [badwords] section contains strings that, if found as a whole word
    in an article's title or body text, cause the article to be silently
    dropped. Matching is case-insensitive and word-boundary aware.

    The [badwords_links] section contains strings that, if found anywhere
    in an article's link, cause the article to be silently dropped.
    Matching is case-insensitive substring search, which is more reliable
    for URLs where delimiters like / and @ are not word boundaries.

    The [languages] section contains ISO 639-1 language codes (e.g. en, it,
    de) that are accepted. If the section is absent or empty, all languages
    are accepted.

    Example:
        [room@conference.example.com]
        feed1 = https://example.com/rss

        [chat:user@example.com]
        feed1 = https://example.com/rss

        [badwords]
        word1 = casino

        [badwords_links]
        word1 = @someaccount

        [languages]
        lang1 = en

    Returns a tuple:
        (
          {jid: [feed_url, ...]},   # all destinations (MUCs and chats)
          {jid: message_type},      # "groupchat" or "chat" per jid
          [badword, ...],
          [badword_link, ...],
          [lang, ...]
        )
    """
    parser = configparser.ConfigParser()

    try:
        with open(feeds_file, "r", encoding="utf-8") as f:
            parser.read_file(f)
    except FileNotFoundError:
        logging.error("Feeds file not found: %s", feeds_file)
        return {}, {}, [], [], []
    except configparser.Error as e:
        logging.error("Error parsing feeds file: %s", e)
        return {}, {}, [], [], []

    feeds_config = {}
    message_types = {}
    badwords = []
    badwords_links = []
    languages = []

    for section in parser.sections():
        s = section.strip()

        if s == BADWORDS_SECTION:
            badwords = [v.strip() for _, v in parser.items(section) if v.strip()]
            continue

        if s == BADWORDS_LINKS_SECTION:
            badwords_links = [v.strip() for _, v in parser.items(section) if v.strip()]
            continue

        if s == LANGUAGES_SECTION:
            languages = [v.strip().lower() for _, v in parser.items(section) if v.strip()]
            continue

        # Determine message type and extract the real JID.
        if s.startswith(CHAT_PREFIX):
            jid = s[len(CHAT_PREFIX):].strip()
            msg_type = "chat"
        else:
            jid = s
            msg_type = "groupchat"

        urls = [url.strip() for _, url in parser.items(section) if url.strip()]
        if urls:
            feeds_config[jid] = urls
            message_types[jid] = msg_type
        else:
            logging.warning("Section [%s] has no feed URLs, skipping.", section)

    muc_count  = sum(1 for t in message_types.values() if t == "groupchat")
    chat_count = sum(1 for t in message_types.values() if t == "chat")
    logging.info(
        "Loaded %d MUC(s), %d chat(s), %d badword(s), %d link badword(s), "
        "%d language filter(s) from %s",
        muc_count, chat_count, len(badwords), len(badwords_links),
        len(languages), feeds_file
    )
    return feeds_config, message_types, badwords, badwords_links, languages