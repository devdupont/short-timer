"""Pull the readable text out of an HTML page.

Both Wodify routes return HTML rather than plain text — the Program API wraps
the workout in a `FormattedWOD` fragment, and the public whiteboard is an
ordinary web page. The LLM parser wants text, so everything funnels through
here first.

This is deliberately generic: strip the tags that never carry workout content,
keep what's left, and collapse the blank lines. It's the same approach the
fixture scraper has always used (this is that code, moved somewhere the app can
import it), which is why it copes with two quite differently structured sources
without per-site selectors.
"""

from __future__ import annotations

from bs4 import BeautifulSoup

#: Tags whose text is always chrome rather than content.
_IGNORED_TAGS = ("script", "style", "noscript", "nav", "header", "footer")


def extract_text(html: str) -> str:
    """Visible text from an HTML document, one line per block, blanks dropped."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(list(_IGNORED_TAGS)):
        tag.decompose()
    text = soup.get_text("\n")
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)
