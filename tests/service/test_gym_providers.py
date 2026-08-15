"""The gym provider registry: every platform is registered, offered, and gated on usability."""

from shortimer.cache.crypto import SecretBox
from shortimer.model.gym import GymConnection, GymProvider
from shortimer.service.gym_providers import PROVIDERS, all_info, spec_for


def test_every_provider_is_registered() -> None:
    """A member of the enum with no spec would 500 the moment someone saved it."""
    assert set(PROVIDERS) == set(GymProvider)


def test_every_provider_is_offered_in_settings() -> None:
    """Every `GymProvider` appears in the settings-screen listing."""
    assert {info.provider for info in all_info()} == set(GymProvider)


def test_a_connection_needs_its_required_fields_to_be_usable() -> None:
    """Wodify's owner route filters on exact names, so both are mandatory."""
    spec = spec_for(GymProvider.WODIFY_OWNER)

    bare = GymConnection(
        provider=GymProvider.WODIFY_OWNER,
        credential=SecretBox(ciphertext="x"),
        enabled=True,
    )
    assert spec.is_usable(bare) is False
    assert spec.is_usable(bare.model_copy(update={"location": "Main"})) is False
    complete = bare.model_copy(update={"location": "Main", "program": "CrossFit"})
    assert spec.is_usable(complete) is True


def test_a_provider_with_no_required_fields_needs_only_a_credential() -> None:
    """SugarWOD declares no required location/program field, so a bare credential is usable."""
    connection = GymConnection(
        provider=GymProvider.SUGARWOD_OWNER,
        credential=SecretBox(ciphertext="x"),
        enabled=True,
    )
    assert spec_for(GymProvider.SUGARWOD_OWNER).is_usable(connection) is True


def test_a_disabled_connection_is_never_usable() -> None:
    """A connection with `enabled=False` is unusable regardless of what else is set."""
    connection = GymConnection(
        provider=GymProvider.SUGARWOD_OWNER,
        credential=SecretBox(ciphertext="x"),
        enabled=False,
    )
    assert spec_for(GymProvider.SUGARWOD_OWNER).is_usable(connection) is False


def test_sugarwod_does_not_advertise_a_location_field() -> None:
    """It scopes by track; offering a location box would just confuse people."""
    info = spec_for(GymProvider.SUGARWOD_OWNER).info
    assert info.location is None
    assert info.program is not None and info.program.label == "Track"
