import configparser
import logging

# Section name reserved for text badwords in feeds.ini.
BADWORDS_SECTION = "badwords"

# Section name reserved for link badwords in feeds.ini.
BADWORDS_LINKS_SECTION = "badwords_links"

# Section name reserved for allowed languages in feeds.ini.
LANGUAGES_SECTION = "languages"


def load_feeds(feeds_file):
    """
    Load feeds, badwords and allowed languages from an INI file.

    Each section name is a MUC JID, except for the reserved [badwords],
    [badwords_links] and [languages] sections. Each key in a MUC section is
    an arbitrary label; its value is a feed URL.

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

        [badwords]
        word1 = casino

        [badwords_links]
        word1 = @someaccount

        [languages]
        lang1 = en

    Returns a tuple:
        ({muc_jid: [feed_url, ...]}, [badword, ...], [badword_link, ...], [lang, ...])
    """
    parser = configparser.ConfigParser()

    try:
        with open(feeds_file, "r", encoding="utf-8") as f:
            parser.read_file(f)
    except FileNotFoundError:
        logging.error("Feeds file not found: %s", feeds_file)
        return {}, [], [], []
    except configparser.Error as e:
        logging.error("Error parsing feeds file: %s", e)
        return {}, [], [], []

    feeds_config = {}
    badwords = []
    badwords_links = []
    languages = []

    for section in parser.sections():
        if section.strip() == BADWORDS_SECTION:
            badwords = [v.strip() for _, v in parser.items(section) if v.strip()]
            continue

        if section.strip() == BADWORDS_LINKS_SECTION:
            badwords_links = [v.strip() for _, v in parser.items(section) if v.strip()]
            continue

        if section.strip() == LANGUAGES_SECTION:
            languages = [v.strip().lower() for _, v in parser.items(section) if v.strip()]
            continue

        muc = section.strip()
        urls = [url.strip() for _, url in parser.items(section) if url.strip()]
        if urls:
            feeds_config[muc] = urls
        else:
            logging.warning("MUC section [%s] has no feed URLs, skipping.", muc)

    logging.info(
        "Loaded %d MUC(s), %d badword(s), %d link badword(s), %d language filter(s) from %s",
        len(feeds_config), len(badwords), len(badwords_links), len(languages), feeds_file
    )
    return feeds_config, badwords, badwords_links, languages