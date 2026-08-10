""""""

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field

from shortimer.cache.crypto import SecretBox, SecretStatus

# --- Gym providers -----------------------------------------------------------
# A gym's programming lives on whichever platform the gym pays for, reached by
# a route that differs by whether you run the gym or attend it. Those two axes
# multiply, so each *combination* is a provider rather than trying to model
# platform and role separately — "the SugarWOD owner route" is the unit that
# has a URL, a credential and a response shape.


class GymProvider(StrEnum):
    """One way of reaching one gym platform.

    Closed on purpose, like `FeedKind`: these are the routes the server knows
    how to fetch, not free-form user input. `gym_providers.py` holds the
    human-facing metadata and the fetch dispatch for each member here.
    """

    WODIFY_MEMBER = "wodify_member"
    WODIFY_OWNER = "wodify_owner"
    SUGARWOD_OWNER = "sugarwod_owner"


#: Which provider is tried first when a user has configured more than one.
#: Member routes outrank owner routes: someone who is both an admin and an
#: athlete sees the same gym either way, and a public whiteboard costs no API
#: quota. Beyond that it's the order providers shipped in, which is arbitrary
#: but stable — and a user who wants a different answer switches the other
#: connection off.
PROVIDER_PRIORITY: tuple[GymProvider, ...] = (
    GymProvider.WODIFY_MEMBER,
    GymProvider.WODIFY_OWNER,
    GymProvider.SUGARWOD_OWNER,
)


class GymConnection(BaseModel):
    """One stored gym credential, plus the settings that qualify it.

    `location` and `program` are deliberately generic rather than named per
    platform: Wodify calls them location and program, SugarWOD calls the second
    one a track, and inventing a field per platform would put the registry's
    job (labelling) into the storage schema. What each one *means* is declared
    in `gym_providers.py` and rendered from there.
    """

    provider: GymProvider
    credential: SecretBox | None = None
    location: str | None = Field(default=None, max_length=200)
    program: str | None = Field(default=None, max_length=200)
    enabled: bool = False

    def is_usable(self) -> bool:
        """Whether this is complete enough to fetch with.

        Only the credential is required here. Whether a provider *also* needs
        a location or program is a property of that provider, so the check
        lives with it — see `GymProviderSpec.is_usable`.
        """
        return bool(self.enabled and self.credential)


class GymWod(BaseModel):
    """One day's workout from a gym, whichever platform it came from.

    Mirrors `crossfit.Wod` rather than reusing it: the two intakes share a
    shape today but not a lifecycle — crossfit.com has rest days and a public
    permalink per day, a gym has neither — and coupling them would mean every
    change to one route rippling into the other.

    Lives here rather than beside a client because every provider produces
    one; `provider` records which did, so a card can say "View on SugarWOD"
    without the frontend having to infer it from the URL.
    """

    date: date
    title: str
    text: str
    #: A "see it at the source" pointer, or empty when the platform has no
    #: page we can link a member to without leaking their credential. The UI
    #: drops the link rather than rendering a dead one.
    url: str = ""
    provider: GymProvider


class GymConnectionView(BaseModel):
    """One stored connection, with its credential reduced to set/not-set."""

    provider: GymProvider
    credential: SecretStatus = Field(default_factory=SecretStatus)
    location: str | None = None
    program: str | None = None
    enabled: bool = False


class GymConnectionUpdate(BaseModel):
    """A requested change to one gym connection.

    `credential` is three-way: absent leaves the stored value alone (so the UI
    can fix a typo'd location without re-entering the key), an empty string
    clears it, and anything else replaces it.
    """

    credential: str | None = Field(default=None, max_length=500)
    location: str | None = Field(default=None, max_length=200)
    program: str | None = Field(default=None, max_length=200)
    enabled: bool | None = None
