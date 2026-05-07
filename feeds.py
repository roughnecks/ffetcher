import html
import logging
import re

import feedparser
from bs4 import BeautifulSoup
from markdownify import markdownify

from db import is_new_entry, is_known_feed

# Image extensions considered valid for inline preview.
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".svg")


def get_new_articles(feed_url, muc, summary_max_length=300, badwords=None,
                     user_agent=None, languages=None):
    """
    Parse a feed and return a list of new articles for the given MUC.
    Each article is a dict with keys: title, summary, link, images.
    Articles are returned in chronological order (oldest first).

    On the very first run for a (feed_url, muc) pair, all existing articles
    are recorded in the database but not returned, to avoid flooding the MUC.
    Articles whose body or link contain a badword are silently dropped.
    If a languages list is provided, articles not matching any of the allowed
    languages are silently dropped.
    """
    if badwords is None:
        badwords = []
    if languages is None:
        languages = []

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
        summary = _extract_summary(entry, summary_max_length, link)
        images  = _extract_images(entry)

        # Drop articles containing a badword in body or link.
        if _contains_badword(title + " " + summary, link, badwords):
            logging.debug("Filtered by badword: %s", link)
            continue

        # Drop articles whose detected language is not in the allowed list.
        # The language filter is only active when languages is non-empty.
        # langdetect is imported lazily here so that its language profiles
        # are not loaded into memory when the feature is not in use.
        if languages and not _is_allowed_language(title + " " + summary, languages):
            logging.debug("Filtered by language: %s", link)
            continue

        new_articles.append({
            "title":   title,
            "summary": summary,
            "link":    link,
            "images":  images,
        })

    return new_articles


def _extract_summary(entry, summary_max_length, article_link=None):
    """
    Extract a plain-text summary from a feed entry.
    Tries the summary field first, then content, then gives up.

    If article_link is provided, any occurrence of that URL in the summary
    is removed to avoid duplicating the link that is already shown separately
    at the end of the message (e.g. Lemmy embeds the post link in the summary).
    """
    raw = ""

    if hasattr(entry, "summary") and entry.summary:
        raw = entry.summary
    elif hasattr(entry, "content") and entry.content:
        raw = entry.content[0].value

    if not raw:
        return ""

    text = _clean_text(raw)

    # Remove the article link if it appears verbatim in the summary text,
    # since it will be appended separately at the end of the message.
    if article_link and article_link in text:
        text = text.replace(article_link, "").strip()

    # Truncate long summaries and add ellipsis.
    if len(text) > summary_max_length:
        text = text[:summary_max_length].rsplit(" ", 1)[0] + " ..."

    return text


def _extract_images(entry):
    """
    Extract image URLs from a feed entry.
    Looks in three places:
    - entry.enclosures (standard RSS attachments)
    - entry.media_content (Media RSS, used by Mastodon and others)
    - img tags inside the entry content or summary HTML
    Returns a deduplicated list of image URL strings.
    """
    images = []
    seen = set()

    def _add(url):
        if url and url not in seen:
            # Only include URLs that look like images.
            url_lower = url.lower().split("?")[0]
            if url_lower.endswith(IMAGE_EXTENSIONS):
                seen.add(url)
                images.append(url)

    # RSS enclosures.
    for enc in getattr(entry, "enclosures", []):
        mime = enc.get("type", "")
        if mime.startswith("image/"):
            _add(enc.get("url", ""))

    # Media RSS (Mastodon, YouTube, etc.).
    for media in getattr(entry, "media_content", []):
        mime = media.get("type", "")
        if mime.startswith("image/") or media.get("url", "").lower().split("?")[0].endswith(IMAGE_EXTENSIONS):
            _add(media.get("url", ""))

    # Inline <img> tags in the HTML content.
    raw = ""
    if hasattr(entry, "content") and entry.content:
        raw = entry.content[0].value
    elif hasattr(entry, "summary") and entry.summary:
        raw = entry.summary

    if raw and not _is_url(raw.strip()):
        soup = BeautifulSoup(html.unescape(raw), "lxml")
        for img in soup.find_all("img", src=True):
            mime = img.get("type", "")
            if not mime or mime.startswith("image/"):
                _add(img["src"])

    return images


def _is_url(text):
    """
    Return True if the text is a bare URL with no surrounding markup.
    Matches any valid URI scheme (http, https, gemini, ftp, etc.) per RFC 3986.
    Used to skip passing plain URLs to BeautifulSoup, which would otherwise
    print a spurious warning about receiving a URL instead of HTML.
    """
    return bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+\-.]*://\S+$", text.strip()))


def _is_allowed_language(text, languages):
    """
    Return True if the detected language of the text is in the allowed list.
    If detection fails (text too short or ambiguous), the article is allowed
    through to avoid dropping legitimate content.

    langdetect is imported here rather than at module level so that its
    language profiles (~40-50 MB) are only loaded when the feature is
    actually in use, i.e. when a [languages] section is present in feeds.ini.
    """
    if not text.strip():
        return True
    try:
        from langdetect import detect, LangDetectException
        detected = detect(text)
        logging.debug("Detected language: %s", detected)
        return detected in languages
    except LangDetectException:
        # Detection failed, allow the article through.
        logging.debug("Language detection failed, allowing article through")
        return True


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


def _fix_mastodon_links(soup):
    """
    Fix Mastodon's habit of splitting URLs and hashtags across multiple
    <span> tags inside <a> elements. We do this directly in the DOM so
    that the structure is clean before markdownify processes it.

    - Hashtag links (/tags/ in href) and mentions are replaced with their
      visible text (e.g. #IRC, @user).
    - All other links are replaced with their full href.
    - Custom emoji <img> tags are replaced with their alt text.
    """
    for a in soup.find_all("a", href=True):
        href = a["href"]
        classes = a.get("class", [])
        if "/tags/" in href or "mention" in classes:
            a.replace_with(a.get_text(separator=""))
        else:
            a.replace_with(href)

    for img in soup.find_all("img", alt=True):
        img.replace_with(img["alt"])


def _clean_text(raw):
    """
    Convert HTML to Markdown using markdownify, preserving the author's
    intended paragraph structure. Mastodon-specific link and emoji quirks
    are fixed in the DOM before conversion.

    If the input is a bare URL with no HTML markup, it is returned as-is
    to avoid passing it to BeautifulSoup, which would print a spurious warning.
    """
    stripped = raw.strip()

    # Return bare URLs as-is — no HTML to parse.
    if _is_url(stripped):
        return stripped

    unescaped = html.unescape(stripped)
    soup = BeautifulSoup(unescaped, "lxml")

    # Fix Mastodon-specific markup before converting to Markdown.
    _fix_mastodon_links(soup)

    # Convert the cleaned HTML to Markdown.
    # heading_style="ATX" uses # for headings.
    # strip=["img"] removes any remaining image tags without alt text.
    # Disable escaping of * and _ to avoid cluttering chat output
    # with backslashes on plain text containing these characters.
    text = markdownify(
        str(soup),
        heading_style="ATX",
        strip=["img"],
        escape_asterisks=False,
        escape_underscores=False,
    )

    # Collapse runs of more than two consecutive blank lines.
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Remove whitespace that sometimes appears between '#' and the tag word,
    # caused by Mastodon wrapping the symbol and word in separate spans.
    text = re.sub(r"#\s+(\S)", r"#\1", text)

    return text.strip()