"""Tests for the link/unlink remote services."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from conftest import make_runtime_data
from fakes import FakeConfigEntry, make_device, prepare_entity
from homeassistant.exceptions import ServiceValidationError

from custom_components.somfy_rts import rf
from custom_components.somfy_rts.const import (
    DOMAIN,
    SERVICE_ADD_TO_GROUP,
    SERVICE_ENTER_PROGRAMMING_MODE,
    SERVICE_GOTO_MY_POSITION,
    SERVICE_LINK_REMOTE,
    SERVICE_MEMORIZE_MY_POSITION,
    SERVICE_RECALIBRATE,
    SERVICE_REMOVE_FROM_GROUP,
    SERVICE_SEND_COMMAND,
    SERVICE_SEND_PROG,
    SERVICE_SEND_STEP_COMMAND,
    SERVICE_SET_MY_POSITION,
    SERVICE_SET_POSITION,
    SERVICE_SET_TILT_POSITION,
    SERVICE_UNLINK_REMOTE,
)
from custom_components.somfy_rts.cover import SomfyRTSCover
from custom_components.somfy_rts.rolling_code import RollingCodeStore
from custom_components.somfy_rts.services import (
    async_setup_services,
    async_unload_services,
)


def setup_env(hass, linked=()):
    device = {
        "name": "Volet",
        "address": "10000001",
        "device_type": "roller_shutter",
        "linked_remote_addresses": list(linked),
    }
    entry = FakeConfigEntry(data={"devices": [device], "sensors": []})
    hass.config_entries.entries.append(entry)
    entity = SimpleNamespace(
        _entry=entry,
        _address="10000001",
        entity_id="cover.volet",
        async_update_linked_remotes=MagicMock(),
    )
    entry.runtime_data = make_runtime_data(
        entities={entity.entity_id: entity}
    )
    return entry, entity


def call(hass, service, data):
    handler = hass.services.registered[(DOMAIN, service)][0]
    asyncio.run(handler(SimpleNamespace(data=data)))


def test_setup_and_unload_services(hass):
    asyncio.run(async_setup_services(hass))
    assert (DOMAIN, SERVICE_LINK_REMOTE) in hass.services.registered
    assert (DOMAIN, SERVICE_UNLINK_REMOTE) in hass.services.registered

    asyncio.run(async_unload_services(hass))
    assert not hass.services.registered


def test_link_remote_persists_and_updates_entity(hass):
    asyncio.run(async_setup_services(hass))
    entry, entity = setup_env(hass)

    call(hass, SERVICE_LINK_REMOTE, {"entity_id": "cover.volet", "remote_address": "00500002"})

    device = entry.data["devices"][0]
    assert device["linked_remote_addresses"] == ["00500002"]
    entity.async_update_linked_remotes.assert_called_once_with(["00500002"])
    assert hass.config_entries.updated  # entry data persisted


def test_link_remote_rejects_duplicate(hass):
    asyncio.run(async_setup_services(hass))
    entry, entity = setup_env(hass, linked=["00500002"])

    with pytest.raises(ServiceValidationError) as err:
        call(hass, SERVICE_LINK_REMOTE, {"entity_id": "cover.volet", "remote_address": "00500002"})
    assert err.value.translation_key == "address_already_linked"
    assert entry.data["devices"][0]["linked_remote_addresses"] == ["00500002"]
    entity.async_update_linked_remotes.assert_not_called()


def test_link_remote_rejects_invalid_address(hass):
    asyncio.run(async_setup_services(hass))
    setup_env(hass)

    with pytest.raises(ServiceValidationError) as err:
        call(hass, SERVICE_LINK_REMOTE, {"entity_id": "cover.volet", "remote_address": "123"})
    assert err.value.translation_key == "invalid_address"


def test_link_remote_rejects_unknown_entity(hass):
    asyncio.run(async_setup_services(hass))
    setup_env(hass)

    with pytest.raises(ServiceValidationError) as err:
        call(hass, SERVICE_LINK_REMOTE, {"entity_id": "cover.autre", "remote_address": "00500002"})
    assert err.value.translation_key == "entity_not_found"


def test_unlink_remote(hass):
    asyncio.run(async_setup_services(hass))
    entry, entity = setup_env(hass, linked=["00500002"])

    call(hass, SERVICE_UNLINK_REMOTE, {"entity_id": "cover.volet", "remote_address": "00500002"})
    assert entry.data["devices"][0]["linked_remote_addresses"] == []
    entity.async_update_linked_remotes.assert_called_once_with([])


def test_unlink_remote_rejects_unknown_address(hass):
    asyncio.run(async_setup_services(hass))
    setup_env(hass, linked=["00500002"])

    with pytest.raises(ServiceValidationError) as err:
        call(hass, SERVICE_UNLINK_REMOTE, {"entity_id": "cover.volet", "remote_address": "03000003"})
    assert err.value.translation_key == "address_not_linked"


def test_services_work_for_switch_and_light_entities(hass):
    """Regression: the services must not be cover-only."""
    asyncio.run(async_setup_services(hass))
    entry, _ = setup_env(hass, linked=["00500002"])

    # A switch entity with the same shape as a real SomfyRTSSwitch.
    switch = SimpleNamespace(
        _entry=entry,
        _address="10000001",
        entity_id="switch.prise",
        async_update_linked_remotes=MagicMock(),
    )
    entry.runtime_data.entities["switch.prise"] = switch

    call(hass, SERVICE_LINK_REMOTE, {"entity_id": "switch.prise", "remote_address": "04000004"})
    switch.async_update_linked_remotes.assert_called_once_with(["00500002", "04000004"])
    assert entry.data["devices"][0]["linked_remote_addresses"] == ["00500002", "04000004"]


# =============================================================================
# Services cover : favorite, recalage, commandes brutes
# =============================================================================
def build_real_cover(hass, **overrides):
    """A real SomfyRTSCover wired like in production."""
    device = make_device(**overrides)
    entry = FakeConfigEntry(
        data={
            "devices": [device],
            "sensors": [],
            "transmitter_id": "radio_frequency.test_radio",
            "enable_rx": True,
        }
    )
    hass.config_entries.entries.append(entry)
    hass.data.setdefault(DOMAIN, {})["rolling_codes"] = RollingCodeStore(hass)
    entry.runtime_data = make_runtime_data()
    entity = SomfyRTSCover(hass, entry, device)
    prepare_entity(entity, "cover.test")
    entry.runtime_data.entities["cover.test"] = entity
    return entry, entity


def call_service(hass, service, data):
    handler = hass.services.registered[(DOMAIN, service)][0]
    asyncio.run(handler(SimpleNamespace(data=data)))


def test_set_my_position_persists(hass):
    asyncio.run(async_setup_services(hass))
    entry, entity = build_real_cover(hass)

    call_service(hass, SERVICE_SET_MY_POSITION, {
        "entity_id": "cover.test", "position": 35,
    })
    assert entity._my_position == 35
    assert entry.data["devices"][0]["my_position"] == 35


def test_set_my_position_native_is_pure_config(hass, fake_rf, no_timer):
    """En mode natif aussi, le service ne fait qu'enregistrer."""
    asyncio.run(async_setup_services(hass))
    _, entity = build_real_cover(hass, simulate_my=False)

    call_service(hass, SERVICE_SET_MY_POSITION, {
        "entity_id": "cover.test", "position": 35,
    })
    assert fake_rf.sent == []  # aucune trame
    assert entity._my_position == 35
    assert entity._entry.data["devices"][0]["my_position"] == 35


def test_set_my_position_native_accepts_any_value(hass, fake_rf, no_timer):
    """En mode natif, on peut saisir librement la valeur supposee."""
    asyncio.run(async_setup_services(hass))
    _, entity = build_real_cover(hass, simulate_my=False)
    entity.async_set_current_position(80)  # position courante sans rapport

    call_service(hass, SERVICE_SET_MY_POSITION, {
        "entity_id": "cover.test", "position": 35,
    })
    assert fake_rf.sent == []
    assert entity._my_position == 35  # enregistre, meme loin de 80


def test_set_my_position_simulated_sends_nothing(hass, fake_rf, no_timer):
    """MY simule (defaut) : simple enregistrement, pas de trame."""
    asyncio.run(async_setup_services(hass))
    build_real_cover(hass)

    call_service(hass, SERVICE_SET_MY_POSITION, {
        "entity_id": "cover.test", "position": 35,
    })
    assert fake_rf.sent == []


def test_enter_programming_mode_long_press(hass, fake_rf, no_timer):
    """Appui long : 35 repetitions de PROG."""
    asyncio.run(async_setup_services(hass))
    build_real_cover(hass)

    call_service(hass, SERVICE_ENTER_PROGRAMMING_MODE, {
        "entity_id": "cover.test"
    })
    assert fake_rf.sent[-1]["command"] == "PROG"
    assert fake_rf.sent[-1]["repeats"] == 35


def test_send_prog_short_press(hass, fake_rf, no_timer):
    """Appui court : 3 repetitions de PROG (bascule appairage)."""
    asyncio.run(async_setup_services(hass))
    build_real_cover(hass)

    call_service(hass, SERVICE_SEND_PROG, {"entity_id": "cover.test"})
    assert fake_rf.sent[-1]["command"] == "PROG"
    assert fake_rf.sent[-1]["repeats"] == 3


def test_memorize_my_position_service(hass, fake_rf, no_timer):
    """Memorisation : long MY + position courante persistee."""
    asyncio.run(async_setup_services(hass))
    entry, entity = build_real_cover(hass)
    entity.async_set_current_position(42)

    call_service(hass, SERVICE_MEMORIZE_MY_POSITION, {
        "entity_id": "cover.test"
    })
    assert fake_rf.sent[-1]["command"] == "MY"
    assert fake_rf.sent[-1]["repeats"] == 35
    assert entity._my_position == 42
    assert entry.data["devices"][0]["my_position"] == 42


def test_recalibrate_service_starts_cycle(hass, fake_rf, no_timer):
    """Le service recalibrate lance le cycle sur le volet."""
    asyncio.run(async_setup_services(hass))
    _, entity = build_real_cover(hass)

    call_service(hass, SERVICE_RECALIBRATE, {"entity_id": "cover.test"})
    assert entity._recal_task is not None
    entity._recal_task.cancel()


def test_goto_my_position_moves_cover(hass, fake_rf, no_timer):
    asyncio.run(async_setup_services(hass))
    _, entity = build_real_cover(hass, my_position=35)

    call_service(hass, SERVICE_GOTO_MY_POSITION, {"entity_id": "cover.test"})
    assert fake_rf.sent[-1]["command"] == "DOWN"
    assert entity.is_closing is True


def test_goto_my_position_requires_configured_position(hass):
    import pytest
    from homeassistant.exceptions import ServiceValidationError

    asyncio.run(async_setup_services(hass))
    build_real_cover(hass)  # pas de my_position

    with pytest.raises(ServiceValidationError) as err:
        call_service(hass, SERVICE_GOTO_MY_POSITION, {"entity_id": "cover.test"})
    assert err.value.translation_key == "my_position_not_set"


def test_set_position_realigns_without_sending(hass, fake_rf):
    asyncio.run(async_setup_services(hass))
    _, entity = build_real_cover(hass)

    call_service(hass, SERVICE_SET_POSITION, {
        "entity_id": "cover.test", "position": 42,
    })
    assert entity.current_cover_position == 42
    assert fake_rf.sent == []  # aucun mouvement


def test_set_tilt_position_realigns(hass, fake_rf):
    asyncio.run(async_setup_services(hass))
    _, entity = build_real_cover(hass, protocol="rts_80bit", tilt=True)

    call_service(hass, SERVICE_SET_TILT_POSITION, {
        "entity_id": "cover.test", "tilt_position": 60,
    })
    assert entity.current_cover_tilt_position == 60
    assert fake_rf.sent == []


def test_set_tilt_position_rejected_without_tilt(hass):
    import pytest
    from homeassistant.exceptions import ServiceValidationError

    asyncio.run(async_setup_services(hass))
    build_real_cover(hass)  # pas d'inclinaison

    with pytest.raises(ServiceValidationError) as err:
        call_service(hass, SERVICE_SET_TILT_POSITION, {
            "entity_id": "cover.test", "tilt_position": 60,
        })
    assert err.value.translation_key == "tilt_not_supported"


def test_send_command_raw(hass, fake_rf):
    asyncio.run(async_setup_services(hass))
    build_real_cover(hass, invert=True)  # inversion logique active

    call_service(hass, SERVICE_SEND_COMMAND, {
        "entity_id": "cover.test", "command": "PROG", "repeat": 1,
    })
    # Commande brute radio : PROG, insensible a l'inversion logique.
    assert fake_rf.sent[-1]["command"] == "PROG"
    assert fake_rf.sent[-1]["rolling_code"] == 1


def test_send_step_command_with_size(hass, fake_rf):
    asyncio.run(async_setup_services(hass))
    build_real_cover(hass, protocol="rts_80bit", tilt=True)

    call_service(hass, SERVICE_SEND_STEP_COMMAND, {
        "entity_id": "cover.test", "direction": "up",
        "step_size": 42, "repeat": 1,
    })
    assert fake_rf.sent[-1]["command"] == "STEP_UP"
    assert fake_rf.sent[-1]["step_size"] == 42


def test_send_step_command_requires_80bit(hass, fake_rf):
    import pytest
    from homeassistant.exceptions import ServiceValidationError

    asyncio.run(async_setup_services(hass))
    build_real_cover(hass)  # 56 bits

    with pytest.raises(ServiceValidationError) as err:
        call_service(hass, SERVICE_SEND_STEP_COMMAND, {
            "entity_id": "cover.test", "direction": "up",
            "step_size": 42, "repeat": 1,
        })
    assert err.value.translation_key == "requires_80bit"
    assert fake_rf.sent == []


def test_new_services_unloaded(hass):
    asyncio.run(async_setup_services(hass))
    asyncio.run(async_unload_services(hass))
    for service in (
        SERVICE_SET_MY_POSITION,
        SERVICE_GOTO_MY_POSITION,
        SERVICE_SET_POSITION,
        SERVICE_RECALIBRATE,
        SERVICE_ENTER_PROGRAMMING_MODE,
        SERVICE_MEMORIZE_MY_POSITION,
        SERVICE_SEND_PROG,
    SERVICE_SET_TILT_POSITION,
        SERVICE_SEND_COMMAND,
        SERVICE_SEND_STEP_COMMAND,
        SERVICE_ADD_TO_GROUP,
        SERVICE_REMOVE_FROM_GROUP,
    ):
        assert (DOMAIN, service) not in hass.services.registered


def test_service_entity_not_found(hass, fake_rf):
    """Une entite inconnue leve une erreur de service."""
    import pytest
    from homeassistant.exceptions import ServiceValidationError

    asyncio.run(async_setup_services(hass))
    build_real_cover(hass)

    with pytest.raises(ServiceValidationError) as err:
        call_service(hass, SERVICE_RECALIBRATE, {"entity_id": "cover.ghost"})
    assert err.value.translation_key == "entity_not_found"


def test_memorize_requires_cover(hass, fake_rf):
    """memorize_my_position sur une entite non cover -> erreur."""
    from types import SimpleNamespace

    import pytest
    from homeassistant.exceptions import ServiceValidationError

    asyncio.run(async_setup_services(hass))
    entry, _ = build_real_cover(hass)
    not_a_cover = SimpleNamespace(
        entity_id="switch.prise",
        async_memorize_my_position=None,
    )
    entry.runtime_data.entities["switch.prise"] = not_a_cover

    with pytest.raises(ServiceValidationError) as err:
        call_service(hass, SERVICE_MEMORIZE_MY_POSITION, {
            "entity_id": "switch.prise"
        })
    assert err.value.translation_key == "not_supported"


def test_send_prog_transmission_failure(hass, fake_rf, monkeypatch):
    """Echec radio du PROG -> command_failed, pas d'exception brute."""
    import pytest
    from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

    asyncio.run(async_setup_services(hass))
    build_real_cover(hass)

    async def boom(*args, **kwargs):
        raise HomeAssistantError("transmitter gone")

    monkeypatch.setattr(rf, "async_send_command", boom)

    with pytest.raises(ServiceValidationError) as err:
        call_service(hass, SERVICE_SEND_PROG, {"entity_id": "cover.test"})
    assert err.value.translation_key == "command_failed"


def test_find_entity_skips_unloaded_entry(hass, fake_rf):
    """Une entree sans runtime_data est ignoree pendant la recherche."""
    import pytest
    from homeassistant.exceptions import ServiceValidationError

    asyncio.run(async_setup_services(hass))
    build_real_cover(hass)
    # Une seconde entree "dechargeee" sans runtime_data.
    ghost = FakeConfigEntry(data={"devices": [], "sensors": []})
    hass.config_entries.entries.append(ghost)

    # L'entite reste trouvable a travers l'entree chargee.
    call_service(hass, SERVICE_RECALIBRATE, {"entity_id": "cover.test"})
    # Et une entite inconnue leve bien l'erreur apres avoir skipe ghost.
    with pytest.raises(ServiceValidationError):
        call_service(hass, SERVICE_RECALIBRATE, {"entity_id": "cover.ghost"})


def test_find_device_missing_config_raises(hass, fake_rf):
    """Entite sans appareil correspondant dans entry.data : erreur."""
    from types import SimpleNamespace

    import pytest
    from homeassistant.exceptions import ServiceValidationError

    asyncio.run(async_setup_services(hass))
    build_real_cover(hass)
    orphan_entry = FakeConfigEntry(
        data={"devices": [], "sensors": []},
        runtime_data=make_runtime_data(),
    )
    hass.config_entries.entries.append(orphan_entry)
    orphan = SimpleNamespace(
        _entry=orphan_entry,
        _address="99999999",
        entity_id="cover.orphan",
        async_set_current_position=lambda position: None,
        async_set_my_position=lambda position: None,
    )
    orphan_entry.runtime_data.entities["cover.orphan"] = orphan

    with pytest.raises(ServiceValidationError) as err:
        call_service(hass, SERVICE_SET_MY_POSITION, {
            "entity_id": "cover.orphan", "position": 10,
        })
    assert err.value.translation_key == "entity_not_found"


def test_prog_press_creates_missing_store(hass, fake_rf):
    """PROG sans store de codes : creation a la volee."""
    asyncio.run(async_setup_services(hass))
    build_real_cover(hass)
    hass.data[DOMAIN].pop("rolling_codes", None)

    call_service(hass, SERVICE_SEND_PROG, {"entity_id": "cover.test"})
    assert "rolling_codes" in hass.data[DOMAIN]
    assert fake_rf.sent[-1]["command"] == "PROG"


def test_send_command_step_requires_80bit(hass, fake_rf):
    """Commande STEP sur un appareil 56 bits : erreur requires_80bit."""
    import pytest
    from homeassistant.exceptions import ServiceValidationError

    asyncio.run(async_setup_services(hass))
    build_real_cover(hass)

    with pytest.raises(ServiceValidationError) as err:
        call_service(hass, SERVICE_SEND_COMMAND, {
            "entity_id": "cover.test", "command": "STEP_UP", "repeat": 3,
        })
    assert err.value.translation_key == "requires_80bit"


def test_send_command_value_error_wrapped(hass, fake_rf, monkeypatch):
    """ValueError pendant une commande brute -> command_failed traduit."""
    import pytest
    from homeassistant.exceptions import ServiceValidationError

    asyncio.run(async_setup_services(hass))
    _, entity = build_real_cover(hass)

    async def boom(*args, **kwargs):
        raise ValueError("bad frame")

    monkeypatch.setattr(entity, "async_send_raw_command", boom)

    with pytest.raises(ServiceValidationError) as err:
        call_service(hass, SERVICE_SEND_COMMAND, {
            "entity_id": "cover.test", "command": "UP", "repeat": 3,
        })
    assert err.value.translation_key == "command_failed"


def test_send_step_command_value_error_wrapped(hass, fake_rf, monkeypatch):
    """ValueError pendant un pas explicite -> command_failed traduit."""
    import pytest
    from homeassistant.exceptions import ServiceValidationError

    asyncio.run(async_setup_services(hass))
    _, entity = build_real_cover(hass, protocol="rts_80bit")

    async def boom(*args, **kwargs):
        raise ValueError("bad step")

    monkeypatch.setattr(entity, "async_send_raw_command", boom)

    with pytest.raises(ServiceValidationError) as err:
        call_service(hass, SERVICE_SEND_STEP_COMMAND, {
            "entity_id": "cover.test", "direction": "up",
            "repeat": 3, "step_size": 100,
        })
    assert err.value.translation_key == "command_failed"


# =============================================================================
# Groupes RTS : ajout / retrait de membres
# =============================================================================
def build_cover(hass, entity_id, **overrides):
    """A real SomfyRTSCover with a custom entity id."""
    device = make_device(**overrides)
    entry = FakeConfigEntry(
        data={
            "devices": [device],
            "sensors": [],
            "transmitter_id": "radio_frequency.test_radio",
            "enable_rx": True,
        }
    )
    hass.config_entries.entries.append(entry)
    hass.data.setdefault(DOMAIN, {})["rolling_codes"] = RollingCodeStore(hass)
    entry.runtime_data = make_runtime_data()
    entity = SomfyRTSCover(hass, entry, device)
    prepare_entity(entity, entity_id)
    entry.runtime_data.entities[entity_id] = entity
    return entry, entity


def build_group_env(hass, members_config=()):
    """A group entity plus one cover per (entity_id, overrides) pair."""
    _, group = build_cover(
        hass, "cover.groupe", address="1500000", is_group=True, name="Groupe"
    )
    members = []
    for entity_id, overrides in members_config:
        members.append(build_cover(hass, entity_id, **overrides)[1])
    return group, members


def test_add_to_group_pairs_cover(hass, fake_rf, no_timer, monkeypatch):
    """Ajout : PROG long sur le volet puis PROG court du groupe."""
    from custom_components.somfy_rts import services

    monkeypatch.setattr(services, "PROG_MODE_JOG_S", 0)
    monkeypatch.setattr(services, "PROG_PAIR_SETTLE_S", 0)
    asyncio.run(async_setup_services(hass))
    group, (member,) = build_group_env(
        hass, [("cover.volet", {"address": "10000001"})]
    )

    call_service(hass, SERVICE_ADD_TO_GROUP, {
        "entity_id": ["cover.volet"], "group": "cover.groupe",
    })

    # Sequence radio : mode programmation sur le volet, puis PROG groupe.
    assert [(f["address"], f["command"], f["repeats"]) for f in fake_rf.sent] == [
        (10000001, "PROG", 35),
        (1500000, "PROG", 3),
    ]
    # Suivi dans la config et sur l'entite.
    assert member._group_addresses == ["1500000"]
    assert member._entry.data["devices"][0]["group_addresses"] == ["1500000"]
    # L'entite groupe liste ses membres.
    assert group.extra_state_attributes["members"] == ["cover.volet"]


def test_remove_from_group_unpairs_cover(hass, fake_rf, no_timer, monkeypatch):
    """Retrait : meme sequence PROG, appartenance retiree."""
    from custom_components.somfy_rts import services

    monkeypatch.setattr(services, "PROG_MODE_JOG_S", 0)
    monkeypatch.setattr(services, "PROG_PAIR_SETTLE_S", 0)
    asyncio.run(async_setup_services(hass))
    group, (member,) = build_group_env(
        hass,
        [("cover.volet", {"address": "10000001", "group_addresses": ["1500000"]})],
    )

    call_service(hass, SERVICE_REMOVE_FROM_GROUP, {
        "entity_id": ["cover.volet"], "group": "cover.groupe",
    })

    assert [(f["address"], f["command"], f["repeats"]) for f in fake_rf.sent] == [
        (10000001, "PROG", 35),
        (1500000, "PROG", 3),
    ]
    assert member._group_addresses == []
    assert member._entry.data["devices"][0]["group_addresses"] == []
    assert group.extra_state_attributes["members"] == []


def test_add_to_group_accepts_single_entity_id(hass, fake_rf, no_timer, monkeypatch):
    """Le champ entity_id accepte aussi une chaine simple."""
    from custom_components.somfy_rts import services

    monkeypatch.setattr(services, "PROG_MODE_JOG_S", 0)
    monkeypatch.setattr(services, "PROG_PAIR_SETTLE_S", 0)
    asyncio.run(async_setup_services(hass))
    group, (member,) = build_group_env(
        hass, [("cover.volet", {"address": "10000001"})]
    )

    call_service(hass, SERVICE_ADD_TO_GROUP, {
        "entity_id": "cover.volet", "group": "cover.groupe",
    })
    assert member._group_addresses == ["1500000"]
    assert group.extra_state_attributes["members"] == ["cover.volet"]


def test_add_to_group_handles_several_members(hass, fake_rf, no_timer, monkeypatch):
    """Plusieurs volets : chaque moteur a sa sequence PROG."""
    from custom_components.somfy_rts import services

    monkeypatch.setattr(services, "PROG_MODE_JOG_S", 0)
    monkeypatch.setattr(services, "PROG_PAIR_SETTLE_S", 0)
    asyncio.run(async_setup_services(hass))
    group, members = build_group_env(
        hass,
        [
            ("cover.v1", {"address": "10000001"}),
            ("cover.v2", {"address": "10000002"}),
        ],
    )

    call_service(hass, SERVICE_ADD_TO_GROUP, {
        "entity_id": ["cover.v1", "cover.v2"], "group": "cover.groupe",
    })

    addresses = [(f["address"], f["repeats"]) for f in fake_rf.sent]
    assert addresses == [
        (10000001, 35), (1500000, 3),
        (10000002, 35), (1500000, 3),
    ]
    assert group.extra_state_attributes["members"] == ["cover.v1", "cover.v2"]
    for member in members:
        assert member._group_addresses == ["1500000"]


def test_add_to_group_rejects_not_a_group(hass, fake_rf, no_timer):
    """L'entite cible doit etre un groupe."""
    asyncio.run(async_setup_services(hass))
    # Deux volets simples, aucun groupe.
    _, (member,) = build_group_env(hass, [("cover.volet", {"address": "10000001"})])
    build_cover(hass, "cover.autre", address="10000002")

    with pytest.raises(ServiceValidationError) as err:
        call_service(hass, SERVICE_ADD_TO_GROUP, {
            "entity_id": ["cover.volet"], "group": "cover.autre",
        })
    assert err.value.translation_key == "not_a_group"
    assert fake_rf.sent == []
    assert member._group_addresses == []


def test_add_to_group_rejects_group_in_itself(hass, fake_rf, no_timer):
    """Un groupe ne peut pas s'ajouter a lui-meme."""
    asyncio.run(async_setup_services(hass))
    build_group_env(hass)

    with pytest.raises(ServiceValidationError) as err:
        call_service(hass, SERVICE_ADD_TO_GROUP, {
            "entity_id": ["cover.groupe"], "group": "cover.groupe",
        })
    assert err.value.translation_key == "group_in_itself"
    assert fake_rf.sent == []


def test_add_to_group_rejects_already_member(hass, fake_rf, no_timer):
    """Ajouter un volet deja membre : erreur, aucune trame."""
    asyncio.run(async_setup_services(hass))
    _, (member,) = build_group_env(
        hass,
        [("cover.volet", {"address": "10000001", "group_addresses": ["1500000"]})],
    )

    with pytest.raises(ServiceValidationError) as err:
        call_service(hass, SERVICE_ADD_TO_GROUP, {
            "entity_id": ["cover.volet"], "group": "cover.groupe",
        })
    assert err.value.translation_key == "already_in_group"
    assert fake_rf.sent == []
    assert member._group_addresses == ["1500000"]


def test_remove_from_group_rejects_not_member(hass, fake_rf, no_timer):
    """Retirer un volet non membre : erreur, aucune trame."""
    asyncio.run(async_setup_services(hass))
    _, (member,) = build_group_env(
        hass, [("cover.volet", {"address": "10000001"})]
    )

    with pytest.raises(ServiceValidationError) as err:
        call_service(hass, SERVICE_REMOVE_FROM_GROUP, {
            "entity_id": ["cover.volet"], "group": "cover.groupe",
        })
    assert err.value.translation_key == "not_in_group"
    assert fake_rf.sent == []
    assert member._group_addresses == []


def test_group_membership_validated_before_any_transmission(
    hass, fake_rf, no_timer, monkeypatch
):
    """Toute la validation precede les emissions radio : aucun volet n'est
    appaire si un autre membre de la liste est invalide."""
    from custom_components.somfy_rts import services

    monkeypatch.setattr(services, "PROG_MODE_JOG_S", 0)
    monkeypatch.setattr(services, "PROG_PAIR_SETTLE_S", 0)
    asyncio.run(async_setup_services(hass))
    build_group_env(
        hass,
        [
            ("cover.v1", {"address": "10000001"}),
            ("cover.v2", {"address": "10000002", "group_addresses": ["1500000"]}),
        ],
    )

    with pytest.raises(ServiceValidationError) as err:
        call_service(hass, SERVICE_ADD_TO_GROUP, {
            "entity_id": ["cover.v1", "cover.v2"], "group": "cover.groupe",
        })
    assert err.value.translation_key == "already_in_group"
    assert fake_rf.sent == []


def test_non_group_cover_has_no_members(hass, fake_rf, no_timer):
    """L'attribut members est vide sur un volet simple."""
    _, (member,) = build_group_env(
        hass, [("cover.volet", {"address": "10000001"})]
    )
    # build_group_env's first entry is the group; check the plain cover.
    plain = member
    assert plain.extra_state_attributes["members"] == []
    assert plain.extra_state_attributes["group_addresses"] == []
