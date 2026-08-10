"""The registry of gym platforms the server knows how to fetch.

One place that answers three questions for every provider: how to fetch it,
when a stored connection is complete enough to try, and what to call its
fields in front of a human. Everything else — the cache, the refresh sweep, the
feed route, the settings screen — is written against this rather than against
any particular platform.

That last question is why the registry exists rather than a `match` statement.
`GymConnection` stores two generic text fields because Wodify calls them
location and program while SugarWOD calls the second one a track, and baking
either vocabulary into the schema would mean a migration every time a platform
disagrees. The vocabulary lives here, travels to the browser as data, and the
settings screen renders whatever it's given — so adding a provider is a change
to this file plus a client module, and no frontend change at all.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import BaseModel

from shortimer.model.gym import PROVIDER_PRIORITY, GymConnection, GymProvider, GymWod
from shortimer.service import sugarwod, wodify

#: What a fetcher is handed. `location` and `program` are whatever the provider
#: declared them to mean; a provider that uses neither simply ignores them.
Fetcher = Callable[..., Awaitable[list[GymWod]]]


class GymFieldInfo(BaseModel):
    """How to render one of the two generic text fields."""

    label: str
    placeholder: str = ""
    #: Whether a connection is incomplete without it. Drives both the form and
    #: `GymProviderSpec.is_usable`, so the UI can't disagree with the server
    #: about what "connected" means.
    required: bool = False


class GymProviderInfo(BaseModel):
    """A provider as the settings screen sees it.

    Deliberately all presentation plus the two field declarations — no URLs, no
    fetch details. A client that renders this can configure any provider we
    ever add without knowing anything about the platform behind it.
    """

    provider: GymProvider
    platform: str
    label: str
    blurb: str
    #: Card label for the "see it at the source" link on a feed entry.
    link_label: str
    credential_label: str
    credential_hint: str
    help_text: str
    location: GymFieldInfo | None = None
    program: GymFieldInfo | None = None


@dataclass(frozen=True)
class GymProviderSpec:
    """Everything the server needs about one provider."""

    info: GymProviderInfo
    fetch: Fetcher

    def is_usable(self, connection: GymConnection) -> bool:
        """Whether this connection is complete enough to fetch with.

        Derived from the field declarations rather than hand-written per
        provider, so the form's asterisks and the server's "is it connected"
        answer can't drift apart.
        """
        if not connection.is_usable():
            return False
        return not any(
            field is not None and field.required and not value
            for field, value in (
                (self.info.location, connection.location),
                (self.info.program, connection.program),
            )
        )


async def _fetch_wodify_member(
    days: int, *, credential: str, location: str, program: str
) -> list[GymWod]:
    """`Fetcher` for `GymProvider.WODIFY_MEMBER`."""
    return await wodify.fetch_recent_member_wods(
        days, whiteboard_key=credential, location=location, program=program
    )


async def _fetch_wodify_owner(
    days: int, *, credential: str, location: str, program: str
) -> list[GymWod]:
    """`Fetcher` for `GymProvider.WODIFY_OWNER`."""
    return await wodify.fetch_recent_owner_wods(
        days, api_key=credential, location=location, program=program
    )


async def _fetch_sugarwod_owner(
    days: int, *, credential: str, location: str, program: str
) -> list[GymWod]:
    """`Fetcher` for `GymProvider.SUGARWOD_OWNER`."""
    # SugarWOD scopes by track, not by location — a gym with two sites still
    # has one programming calendar — so `location` is not declared and not read.
    return await sugarwod.fetch_recent_owner_wods(days, api_key=credential, track_id=program)


PROVIDERS: dict[GymProvider, GymProviderSpec] = {
    GymProvider.WODIFY_MEMBER: GymProviderSpec(
        info=GymProviderInfo(
            provider=GymProvider.WODIFY_MEMBER,
            platform="Wodify",
            label="My gym's Wodify whiteboard",
            blurb=(
                "For gym members. Works only if your gym has turned on Wodify's public "
                "whiteboard — the key comes from the link they publish."
            ),
            link_label="View on Wodify ↗",
            credential_label="Whiteboard key",
            credential_hint="The WhiteboardKey value from your gym's public whiteboard link.",
            help_text=(
                "Ask your gym to enable WOD → Settings → Web Integration → Public Whiteboard."
            ),
            location=GymFieldInfo(label="Location", placeholder="e.g. Main"),
            program=GymFieldInfo(label="Program", placeholder="e.g. CrossFit"),
        ),
        fetch=_fetch_wodify_member,
    ),
    GymProvider.WODIFY_OWNER: GymProviderSpec(
        info=GymProviderInfo(
            provider=GymProvider.WODIFY_OWNER,
            platform="Wodify",
            label="Wodify gym owner API key",
            blurb=(
                "For gym owners and admins. Location and program must match your Wodify "
                "setup exactly."
            ),
            link_label="View on Wodify ↗",
            credential_label="API key",
            credential_hint="Sent to Wodify as the x-api-key header. Stored encrypted.",
            help_text="Generate a key in Wodify under Automations → Integrations → API Keys.",
            location=GymFieldInfo(
                label="Location", placeholder="Exact location name in Wodify", required=True
            ),
            program=GymFieldInfo(
                label="Program", placeholder="Exact program name in Wodify", required=True
            ),
        ),
        fetch=_fetch_wodify_owner,
    ),
    GymProvider.SUGARWOD_OWNER: GymProviderSpec(
        info=GymProviderInfo(
            provider=GymProvider.SUGARWOD_OWNER,
            platform="SugarWOD",
            label="SugarWOD gym owner API key",
            blurb=(
                "For gym owners and admins. Pulls your posted workouts straight from "
                "SugarWOD's calendar."
            ),
            link_label="View on SugarWOD ↗",
            credential_label="API key",
            credential_hint="Sent to SugarWOD as the Authorization header. Stored encrypted.",
            help_text=(
                "Generate a key in SugarWOD under Settings → Developer Keys. Leave the track "
                "blank to take every track you publish."
            ),
            program=GymFieldInfo(label="Track", placeholder="Track id — blank for all tracks"),
        ),
        fetch=_fetch_sugarwod_owner,
    ),
}


def spec_for(provider: GymProvider) -> GymProviderSpec:
    """The registered `GymProviderSpec` for `provider`."""
    return PROVIDERS[provider]


def all_info() -> list[GymProviderInfo]:
    """Every provider, in the order a settings screen should offer them."""
    return [PROVIDERS[provider].info for provider in PROVIDER_PRIORITY if provider in PROVIDERS]
