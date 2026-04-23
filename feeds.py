import html
import logging
import re

import feedparser
from bs4 import BeautifulSoup
from markdownify import markdownify

from db import is_new_entry, is_known_feed


def get_new_articles(feed_url, muc, summary_max_length=300, badwords=None, user_agent=None):
    """
    Parse a feed and return a list of new articles for the given MUC.
    Each article is a dict with keys: title, summary, link.
    Articles are returned in chronological order (oldest first).

    On the very first run for a (feed_url, muc) pair, all existing articles
    are recorded in the database but not returned, to avoid flooding the MUC.
    Articles whose body or link contain a badword are silently dropped.
    """
    if badwords is None:
        badwords = []

    # Pass a custom User-Agent to feedparser so servers can identify the
    # client and are less likely to block it as an anonymous scraper.
    feed = feedparser.parse(feed_url, agent=user_agent)

    # feed.bozo is set for any parsing anomaly, including minor ones like a
    # missing Content-Type header. Only treat it as an error if there are no
    # entries at all, since a feed with entries is usable regardless.
    if not feed.entries:
        if feed.bozo:
            reason = feed.get("bozo_exception", "unknown error")
            logging.warning("Could not parse feed: %s (%s)", feed_url, reason)
        else:
            logging.debug("Feed is empty: %s", feed_url)
        return []

    # Check if this feed is new. is_known_feed() registers it on first call.
    known = is_known_feed(feed_url, muc)

    if not known:
        logging.info("New feed detected, seeding without posting: %s -> %s", feed_url, muc)

    new_articles = []

    # Reverse so we process oldest entries first.
    for entry in reversed(feed.entries):
        link = getattr(entry, "link", None)
        if not link:
            continue

        if not is_new_entry(link, muc):
            continue

        # Feed is new: record the article but do not post it.
        if not known:
            continue

        title   = _clean_text(getattr(entry, "title", "(no title)"))
        summary = _extract_summary(entry, summary_max_length)

        # Drop articles containing a badword in body or link.
        if _contains_badword(title + " " + summary, link, badwords):
            logging.debug("Filtered by badword: %s", link)
            continue

        new_articles.append({
            "title":   title,
            "summary": summary,
            "link":    link,
        })

    return new_articles


def _extract_summary(entry, summary_max_length):
    """
    Extract a plain-text summary from a feed entry.
    Tries the summary field first, then content, then gives up.
    """
    raw = ""

    if hasattr(entry, "summary") and entry.summary:
        raw = entry.summary
    elif hasattr(entry, "content") and entry.content:
        raw = entry.content[0].value

    if not raw:
        return ""

    text = _clean_text(raw)

    # Truncate long summaries and add ellipsis.
    if len(text) > summary_max_length:
        text = text[:summary_max_length].rsplit(" ", 1)[0] + " ..."

    return text


def _contains_badword(text, link, badwords):
    """
    Return True if any badword is found in the text or in the link.
    - Text matching uses word boundaries (case-insensitive whole-word match).
    - Link matching uses plain substring search, which is more reliable for
      URL paths where delimiters like / and @ are not word boundary characters.
    """
    haystack_text = text.lower()
    haystack_link = link.lower()
    for word in badwords:
        w = word.lower()
        pattern = re.compile(r"\b" + re.escape(w) + r"\b")
        if pattern.search(haystack_text):
            return True
        if w in haystack_link:
            return True
    return False


def _starts_with_emoji(line):
    """
    Return True if the line starts with a Unicode emoji character.
    """
    if not line:
        return False
    # Emoji occupy code points above U+00FF outside the basic Latin range.
    return ord(line[0]) > 0x00FF


def _is_url_fragment(line):
    """
    Return True if the line looks like the continuation of a broken URL:
    no spaces, no URL scheme, not a hashtag or standalone word.
    """
    if " " in line:
        return False
    if line.startswith(("http://", "https://", "#")):
        return False
    # Looks like a path fragment (letters, digits, slashes, dots, hyphens...)
    return bool(re.match(r"^[A-Za-z0-9\-._~:/?#\[\]@!$&\'()*+,;=%]+$", line))


def _clean_text(raw):
    """
    Convert HTML to Markdown using markdownify, then clean up the result.

    markdownify handles structural tags (headings, lists, code blocks,
    bold, italic) natively and produces readable Markdown. We then handle
    Mastodon-specific quirks on top of it: hashtag links are replaced with
    their visible text, and other links use their href.
    """
    unescaped = html.unescape(raw)
    soup = BeautifulSoup(unescaped, "lxml")

    # Replace <a> tags selectively before converting to Markdown:
    # - hashtag links (href contains /tags/) are replaced with their visible text
    # - mention links (class contains "mention") are replaced with their visible text
    # - all other links are replaced with their href to get the full URL
    for a in soup.find_all("a", href=True):
        href = a["href"]
        classes = a.get("class", [])
        if "/tags/" in href or "mention" in classes:
            a.replace_with(a.get_text(separator=""))
        else:
            a.replace_with(href)

    # Replace custom emoji <img> tags with their alt text.
    # Mastodon emoji have a "emojione" class and a descriptive alt attribute.
    for img in soup.find_all("img", alt=True):
        img.replace_with(img["alt"])

    # Convert the cleaned HTML to Markdown.
    # Disable escaping of * and _ so that plain text containing these
    # characters is not cluttered with backslashes in the chat output.
    text = markdownify(
        str(soup),
        heading_style="ATX",
        strip=["img"],
        escape_asterisks=False,
        escape_underscores=False,
    )

    # Remove any residual blank lines beyond a single separator.
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Remove whitespace that sometimes appears between '#' and the tag word.
    text = re.sub(r"#\s+(\S)", r"#\1", text)

    return text.strip()