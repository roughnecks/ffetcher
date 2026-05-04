import configparser
import logging

# Section name reserved for badwords in feeds.ini.
BADWORDS_SECTION = "badwords"

# Section name reserved for allowed languages in feeds.ini.
LANGUAGES_SECTION = "languages"


def load_feeds(feeds_file):
    """
    Load feeds, badwords and allowed languages from an INI file.

    Each section name is a MUC JID, except for the reserved [badwords] and
    [languages] sections. Each key in a MUC section is an arbitrary label;
    its value is a feed URL.

    The [badwords] section contains strings that, if found in an article's
    body or link, cause the article to be silently dropped.

    The [languages] section contains ISO 639-1 language codes (e.g. en, it,
    de) that are accepted. If the section is absent or empty, all languages
    are accepted.

    Example:
        [room@conference.example.com]
        feed1 = https://example.com/rss

        [badwords]
        word1 = casino

        [languages]
        lang1 = en
        lang2 = it

    Returns a tuple: ({muc_jid: [feed_url, ...]}, [badword, ...], [lang, ...])
    """
    parser = configparser.ConfigParser()

    try:
        with open(feeds_file, "r", encoding="utf-8") as f:
            parser.read_file(f)
    except FileNotFoundError:
        logging.error("Feeds file not found: %s", feeds_file)
        return {}, [], []
    except configparser.Error as e:
        logging.error("Error parsing feeds file: %s", e)
        return {}, [], []

    feeds_config = {}
    badwords = []
    languages = []

    for section in parser.sections():
        if section.strip() == BADWORDS_SECTION:
            badwords = [v.strip() for _, v in parser.items(section) if v.strip()]
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
        "Loaded %d MUC(s), %d badword(s), %d language filter(s) from %s",
        len(feeds_config), len(badwords), len(languages), feeds_file
    )
    return feeds_config, badwords, languages
