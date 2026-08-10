"""The shape every published-feed source (crossfit.com, Concept2, a gym) shares."""

from datetime import date

from pydantic import BaseModel


class DatedFeedItem(BaseModel):
    """One day's content from a published feed: crossfit.com, Concept2, a gym.

    Every feed publishes the same four things about a day, whatever the
    source — only how they're fetched and cached differs, which is why the
    sources keep separate models rather than sharing this one directly:
    collapsing them would invite an assumption from one source's lifecycle to
    leak into another's.
    """

    date: date
    title: str
    text: str
    url: str = ""
