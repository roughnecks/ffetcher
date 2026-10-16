import html
import io
import logging
import re

import asyncio
import feedparser
from bs4 import BeautifulSoup
from markdownify import markdownify

from .db import is_new_entry, is_known_feed

# Image extensions considered valid for inline preview.
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".svg")

# Bold header line (XEP-0393) shown above the options of a poll, and the
# marker prepended to each option. A plain hollow circle is used instead of
# an emoji so the result does not depend on the client's font.
POLL_HEADER = "*Poll:*"
POLL_OPTION_MARKER = "\u25cb"


def _allow_poll_markup():
    """
    Let <radio> elements survive feedparser's HTML sanitizer.

    Mastodon renders the options of a poll in its feeds as non-standard
    <radio> elements. feedparser's sanitizer only keeps tags from a fixed
    allow-list and silently drops everything else, which would turn the
    options into plain lines indistinguishable from ordinary text. Adding
    "radio" to the allow-list keeps them intact so that _split_poll() can
    recognise them, while every other tag is sanitized exactly as before.

    This relies on feedparser internals, whose names differ between
    releases, so the result is verified at the end with a tiny in-memory
    feed. If it did not work, a warning is logged and the bot keeps running,
    showing polls as plain lines, as it did before.
    """
    try:
        import feedparser.sanitizer as sanitizer_module

        # The class is called HTMLSanitizer in recent feedparser releases
        # and _HTMLSanitizer in older ones.
        sanitizer_class = (
            getattr(sanitizer_module, "HTMLSanitizer", None)
            or getattr(sanitizer_module, "_HTMLSanitizer")
        )
        elements = sanitizer_class.acceptable_elements

        # Keep the original container type (set, frozenset, list or tuple).
        if isinstance(elements, (set, frozenset)):
            sanitizer_class.acceptable_elements = elements | {"radio"}
        else:
            sanitizer_class.acceptable_elements = type(elements)(
                list(elements) + ["radio"]
            )
    except (ImportError, AttributeError, TypeError) as e:
        logging.warning("Poll formatting disabled, feedparser internals changed: %s", e)
        return

    if not _poll_markup_survives():
        logging.warning(
            "Poll formatting disabled: feedparser still strips <radio> elements"
        )


def _poll_markup_survives():
    """
    Return True if a <radio> element survives feedparser's sanitizer.

    Parses a minimal feed held in memory, using only feedparser's public API,
    so the check works whatever the internals of the installed release are.
    """
    probe = (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<rss version="2.0"><channel><title>t</title><item><title>t</title>'
        b"<description>&lt;p&gt;&lt;radio&gt;x&lt;/radio&gt;&lt;/p&gt;</description>"
        b"</item></channel></rss>"
    )
    try:
        summary = feedparser.parse(io.BytesIO(probe)).entries[0].summary
    except Exception:
        # Anything unexpected means the result cannot be confirmed.
        return False
    return "<radio" in summary.lower()


# Must run before any feed is parsed.
_allow_poll_markup()


async def fetch_feed(feed_url, user_agent=None):
    """
    Download and parse a feed, returning the feedparser result.
    Returns None on timeout or error so the caller can skip processing.
    """
    try:
        loop = asyncio.get_running_loop()
        feed = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: feedparser.parse(feed_url, agent=user_agent)
            ),
            timeout=30
        )
        return feed

    except asyncio.TimeoutError:
        logging.warning("Timeout fetching feed: %s", feed_url)
        return None

    except Exception as e:
        logging.warning("Error fetching feed %s: %s", feed_url, e)
        return None


def process_feed(feed, feed_url, muc, summary_max_length=300, badwords=None,
                 badwords_links=None, languages=None):
    """
    Process an already-downloaded feed for a specific destination JID.
    Returns a list of new articles as dicts with keys: title, summary, link, images.
    Articles are returned in chronological order (oldest first).

    On the very first run for a (feed_url, muc) pair, all existing articles
    are recorded in the database but not returned, to avoid flooding the MUC.
    Articles whose title/body contain a badword, or whose link contains a
    link badword, are silently dropped.
    If a languages list is provided, articles not matching any of the allowed
    languages are silently dropped.
    """
    if badwords is None:
        badwords = []
    if badwords_links is None:
        badwords_links = []
    if languages is None:
        languages = []

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

        if not is_new_entry(link, muc, feed_url):
            continue

        # Feed is new: record the article but do not post it.
        if not known:
            continue

        title   = _clean_text(getattr(entry, "title", "(no title)"))
        summary = _extract_summary(entry, summary_max_length, link)
        images  = _extract_images(entry)

        # Drop articles whose title or body contains a text badword.
        if _contains_badword(title + " " + summary, badwords):
            logging.debug("Filtered by badword: %s", link)
            continue

        # Drop articles whose link contains a link badword.
        if _contains_link_badword(link, badwords_links):
            logging.debug("Filtered by link badword: %s", link)
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

    If the entry contains a poll, its options are taken out of the text,
    formatted as a separate block and appended after the text has been
    truncated, so the poll is never cut off and does not count towards
    summary_max_length.
    """
    raw = ""

    if hasattr(entry, "summary") and entry.summary:
        raw = entry.summary
    elif hasattr(entry, "content") and entry.content:
        raw = entry.content[0].value

    if not raw:
        return ""

    # Separate the poll options (if any) from the rest of the post.
    raw, poll_options = _split_poll(raw)

    text = _clean_text(raw)

    # Remove the article link if it appears verbatim in the summary text,
    # since it will be appended separately at the end of the message.
    if article_link and article_link in text:
        text = text.replace(article_link, "").strip()

    # Truncate long summaries and add ellipsis.
    if len(text) > summary_max_length:
        text = text[:summary_max_length].rsplit(" ", 1)[0] + " ..."

    # Append the poll after truncation. A post may consist of the poll only.
    if poll_options:
        poll = _format_poll(poll_options)
        text = (text + "\n\n" + poll) if text else poll

    return text


def _split_poll(raw):
    """
    Separate the poll options from the HTML of a feed entry.

    Returns a tuple (html_without_poll, options), where options is a list
    of cleaned option strings. If the entry has no poll, the original HTML
    is returned untouched together with an empty list.

    Mastodon renders each option as a <radio> element, separated by <br>
    (see _allow_poll_markup() for why these tags are preserved at all).
    The feed carries neither the poll's expiry date nor its vote counts.
    """
    # Cheap check first, to avoid parsing HTML for ordinary entries. It looks
    # for the bare word so that entity-escaped markup (&lt;radio) is caught
    # too, since _clean_text() also tolerates escaped input.
    if "radio" not in raw.lower():
        return raw, []

    soup = BeautifulSoup(html.unescape(raw), "lxml")
    radios = soup.find_all("radio")
    if not radios:
        return raw, []

    options = []
    containers = []

    for radio in radios:
        # Reuse the regular cleanup so links and custom emoji inside an
        # option are handled like in the post text, then flatten whitespace.
        option = " ".join(_clean_text(radio.decode_contents()).split())
        if option:
            options.append(option)

        parent = radio.parent
        if parent is not None and not any(parent is c for c in containers):
            containers.append(parent)

        radio.decompose()

    # Remove the paragraphs that only held the options, so they do not leave
    # empty lines behind. Containers that still hold text are kept.
    for container in containers:
        if container.name in ("html", "body", "[document]"):
            continue
        if not container.get_text(strip=True):
            container.decompose()

    return str(soup), options


def _format_poll(options):
    """
    Render poll options as a header line followed by one option per line,
    each prefixed with a marker.
    """
    lines = [POLL_HEADER]
    lines.extend("%s %s" % (POLL_OPTION_MARKER, option) for option in options)
    return "\n".join(lines)


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
    return bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+\-.]*:/{0,2}\S+$", text.strip()))


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


def _contains_badword(text, badwords):
    """
    Return True if any badword is found as a whole word in the text.
    Matching is case-insensitive and word-boundary aware, so "casino"
    will not match "casinetto".
    """
    haystack_text = text.lower()
    for word in badwords:
        w = word.lower()
        pattern = re.compile(r"\b" + re.escape(w) + r"\b")
        if pattern.search(haystack_text):
            return True
    return False


def _contains_link_badword(link, badwords_links):
    """
    Return True if any link badword is found in the link.
    Matching is plain substring search, which is more reliable for URL
    paths where delimiters like / and @ are not word boundary characters
    (e.g. to silence a specific account by matching "@someaccount").
    """
    haystack_link = link.lower()
    for word in badwords_links:
        w = word.lower()
        if w in haystack_link:
            return True
    return False


def _fix_mastodon_links(soup):
    """
    Fix Mastodon's habit of splitting URLs and hashtags across multiple
    <span> tags inside <a> elements.

    - Hashtag links (/tags/ in href) and mentions are replaced with their
      visible text (e.g. #IRC, @user).
    - Links where the visible text is meaningful (different from the href
      and not itself a URL) are converted to Markdown [text](url) format,
      preserving the label for readability in generic RSS feeds.
    - Links where the visible text equals the href, or is itself a URL,
      are replaced with the bare href (Mastodon-style truncated links).
    - Links with an empty href (e.g. gemini:// URLs on some instances) are
      reconstructed from their visible and invisible text fragments.
    - Custom emoji <img> tags are replaced with their alt text.
    - Mastodon link origin tags are removed.
    """

    for a in soup.find_all("a", href=True):
        href = a["href"]
        classes = a.get("class", [])
        visible = a.get_text(separator="")

        # Hashtags and mentions: show visible text only.
        if "/tags/" in href or "mention" in classes:
            a.replace_with(visible)

        # Empty href: reconstruct URL from span contents (e.g. gemini://).
        elif not href:
            a.replace_with(visible)

        # Meaningful label: convert to Markdown [text](url).
        # A label is meaningful if it differs from the href and is not
        # itself a bare URL (which would add no information over the href).
        elif visible and visible != href and not _is_url(visible):
            a.replace_with("[%s](%s)" % (visible, href))

        # Bare URL or label identical to href: just use the href.
        else:
            a.replace_with(href)

    # Remove Mastodon's "[example.org]" decorations.
    for span in soup.find_all(
        "span",
        class_=lambda c: c and "link-origin-tag" in c
    ):
        span.decompose()

    # Replace custom emoji images with their textual alt.
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