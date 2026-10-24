"""Tests for the config flow and options flow."""

from __future__ import annotations

import asyncio

import pytest
from fakes import FakeConfigEntry, make_device
from homeassistant.data_entry_flow import section as form_section
from homeassistant.exceptions import HomeAssistantError

from custom_components.rts_gateway import config_flow
from custom_components.rts_gateway.config_flow import (
    SomfyRTSConfigFlow,
    SomfyRTSOptionsFlow,
)
from custom_components.rts_gateway.const import (
    DEFAULT_FRAME_REPEATS,
    DOMAIN,
    PROG_MODE_REPEATS,
    SomfyCommand,
)
from custom_components.rts_gateway.rolling_code import RollingCodeStore

TRANSMITTER = "radio_frequency.test_radio"

DEVICE_INPUT = {
    "name": "Volet salon",
    "address": "00000001",
    "device_type": "roller_shutter",
    "protocol": "rts_56bit",
    "open_time_ms": 20000,
    "close_time_ms": 20000,
    "is_group": False,
    "tilt": False,
    "tilt_step_percent": 7,
    "step_size": 100,
    "toggle_command": "MY",
    "brightness_step_percent": 5,
}


def form_defaults(schema):
    """Schema defaults, section payloads filled in and flattened."""
    payloads = {
        key.schema: {}
        for key, value in schema.schema.items()
        if isinstance(value, form_section)
    }
    flat: dict = {}
    for key, value in schema(payloads).items():
        if isinstance(value, dict):
            flat.update(value)
        else:
            flat[key] = value
    return flat


@pytest.fixture
def flow(hass, fake_rf):
    flow = SomfyRTSConfigFlow()
    flow.hass = hass
    hass.data["rts_gateway"] = {"rolling_codes": RollingCodeStore(hass)}
    return flow


def complete_device_input(**overrides):
    data = dict(DEVICE_INPUT)
    data.update(overrides)
    return data


def test_user_step_shows_form(hass, fake_rf):
    """Le pas initial affiche le formulaire quand des emetteurs existent."""
    async def scenario():
        flow = SomfyRTSConfigFlow()
        flow.hass = hass
        result = await flow.async_step_user(None)
        assert result["type"] == "form"
        assert result["step_id"] == "user"

    asyncio.run(scenario())


def test_reconfigure_without_transmitters(hass, monkeypatch):
    """Reconfiguration sans emetteur disponible : abort."""
    from custom_components.rts_gateway import rf as rf_module

    monkeypatch.setattr(rf_module, "get_transmitters", lambda hass: [])

    async def scenario():
        entry = FakeConfigEntry(
            data={"transmitter_id": TRANSMITTER, "devices": [], "sensors": []}
        )
        hass.config_entries.entries.append(entry)

        flow = SomfyRTSConfigFlow()
        flow.hass = hass
        flow.context = {"source": "reconfigure", "entry_id": entry.entry_id}
        result = await flow.async_step_reconfigure(None)
        assert result["type"] == "abort"
        assert result["reason"] == "no_transmitters"

    asyncio.run(scenario())


def test_add_device_rejects_invalid_address(flow):
    """Adresse invalide au wizard : erreur de champ."""
    async def scenario():
        result = await flow.async_step_add_device(
            complete_device_input(address="123")
        )
        assert result["type"] == "form"
        assert result["errors"]["address"] == "invalid_address"

    asyncio.run(scenario())


def test_add_device_generates_address(flow):
    """Adresse vide : generation automatique."""
    async def scenario():
        flow._transmitter_id = TRANSMITTER
        result = await flow.async_step_add_device(
            complete_device_input(address="")
        )
        assert result["step_id"] == "device_options"
        assert len(flow._pending_device["address"]) == 8

    asyncio.run(scenario())


def test_pair_creates_missing_store(hass, fake_rf):
    """Appairage sans store de codes : creation a la volee."""
    async def scenario():
        hass.data.pop("rts_gateway", None)
        flow = SomfyRTSConfigFlow()
        flow.hass = hass
        flow._transmitter_id = TRANSMITTER
        flow._pending_device = complete_device_input()

        await flow.async_step_pair({})
        assert "rolling_codes" in hass.data["rts_gateway"]
        assert fake_rf.sent[-1]["command"] == "PROG"

    asyncio.run(scenario())


def test_options_flow_factory(hass):
    """La fabrique d'options flow renvoie bien une instance."""
    flow = SomfyRTSConfigFlow.async_get_options_flow(None)
    assert isinstance(flow, SomfyRTSOptionsFlow)


def test_options_edit_remove_steps_without_devices(hass, fake_rf):
    """Edit/remove/remotes sans appareil : retour direct au menu."""
    async def scenario():
        entry = FakeConfigEntry(
            data={"transmitter_id": TRANSMITTER, "enable_rx": True,
                  "devices": [], "sensors": []}
        )
        hass.config_entries.entries.append(entry)

        flow = SomfyRTSOptionsFlow()
        flow.hass = hass
        flow.handler = entry.entry_id

        for step in (
            "edit_device",
            "remove_device",
            "add_remote",
            "remove_remote",
        ):
            result = await getattr(flow, f"async_step_{step}")(None)
            assert result["type"] == "menu", step

    asyncio.run(scenario())


def test_options_add_device_generates_address(hass, fake_rf):
    """Wizard d'options : adresse vide generee."""
    async def scenario():
        entry = FakeConfigEntry(
            data={"transmitter_id": TRANSMITTER, "enable_rx": True,
                  "devices": [], "sensors": []}
        )
        hass.config_entries.entries.append(entry)

        flow = SomfyRTSOptionsFlow()
        flow.hass = hass
        flow.handler = entry.entry_id

        result = await flow.async_step_add_device(
            complete_device_input(address="")
        )
        assert result["step_id"] == "device_options"
        assert len(flow._pending_device["address"]) == 8

    asyncio.run(scenario())


def test_options_pair_failure_shows_error(hass, fake_rf, monkeypatch):
    """Echec radio a l'appairage (options) : erreur transmission_failed."""
    from custom_components.rts_gateway import config_flow as cf

    async def boom(*args, **kwargs):
        raise HomeAssistantError("RF transmission failed")

    async def scenario():
        entry = FakeConfigEntry(
            data={"transmitter_id": TRANSMITTER, "enable_rx": True,
                  "devices": [], "sensors": []}
        )
        hass.config_entries.entries.append(entry)
        monkeypatch.setattr(cf.rf, "async_send_command", boom)

        flow = SomfyRTSOptionsFlow()
        flow.hass = hass
        flow.handler = entry.entry_id
        flow._pending_device = complete_device_input()

        result = await flow.async_step_pair({})
        assert result["step_id"] == "pair"
        assert result["errors"]["base"] == "transmission_failed"

    asyncio.run(scenario())


def test_remove_device_confirm_stale_index(hass, fake_rf):
    """Indice de retrait perime : retour au choix d'appareil."""
    async def scenario():
        entry = FakeConfigEntry(
            data={"transmitter_id": TRANSMITTER, "enable_rx": True,
                  "devices": [make_device()], "sensors": []}
        )
        hass.config_entries.entries.append(entry)

        flow = SomfyRTSOptionsFlow()
        flow.hass = hass
        flow.handler = entry.entry_id
        flow._remove_index = 5

        result = await flow.async_step_remove_device_confirm(None)
        assert result["step_id"] == "remove_device"

    asyncio.run(scenario())


def test_remove_device_confirm_unpair_failure(hass, fake_rf, monkeypatch):
    """Echec radio du desappairage : erreur et rien de supprime."""
    from custom_components.rts_gateway import config_flow as cf

    async def boom(*args, **kwargs):
        raise HomeAssistantError("RF transmission failed")

    async def scenario():
        entry = FakeConfigEntry(
            data={"transmitter_id": TRANSMITTER, "enable_rx": True,
                  "devices": [make_device()], "sensors": []}
        )
        hass.config_entries.entries.append(entry)
        monkeypatch.setattr(cf.rf, "async_send_command", boom)

        flow = SomfyRTSOptionsFlow()
        flow.hass = hass
        flow.handler = entry.entry_id
        flow._remove_index = 0

        result = await flow.async_step_remove_device_confirm({"unpair": True})
        assert result["step_id"] == "remove_device_memory"
        assert result["errors"]["base"] == "transmission_failed"
        # Rien n'a ete supprime.
        assert len(flow._devices()) == 1

    asyncio.run(scenario())


def test_remove_remote_confirm_stale_index(hass, fake_rf):
    """Indice de telecommande perime : retour au choix d'appareil."""
    async def scenario():
        entry = FakeConfigEntry(
            data={"transmitter_id": TRANSMITTER, "enable_rx": True,
                  "devices": [make_device()], "sensors": []}
        )
        hass.config_entries.entries.append(entry)

        flow = SomfyRTSOptionsFlow()
        flow.hass = hass
        flow.handler = entry.entry_id
        flow._remove_remote_index = 9

        result = await flow.async_step_remove_remote_confirm(None)
        assert result["step_id"] == "remove_remote"

    asyncio.run(scenario())


def test_add_sensor_rejects_invalid_address(flow):
    """Adresse de capteur invalide : erreur de champ."""
    async def scenario():
        result = await flow.async_step_add_sensor(
            {"name": "Capteur", "address": "123"}
        )
        assert result["type"] == "form"
        assert result["errors"]["address"] == "invalid_address"

    asyncio.run(scenario())


def test_options_add_device_rejects_invalid_address(hass, fake_rf):
    """Wizard d'options : adresse invalide -> erreur de champ."""
    async def scenario():
        entry = FakeConfigEntry(
            data={"transmitter_id": TRANSMITTER, "enable_rx": True,
                  "devices": [], "sensors": []}
        )
        hass.config_entries.entries.append(entry)

        flow = SomfyRTSOptionsFlow()
        flow.hass = hass
        flow.handler = entry.entry_id

        result = await flow.async_step_add_device(
            complete_device_input(address="123")
        )
        assert result["type"] == "form"
        assert result["errors"]["address"] == "invalid_address"

    asyncio.run(scenario())


def test_device_options_empty_fields_pair_directly(hass, fake_rf, monkeypatch):
    """Combinaison sans champs specifiques : appairage immediat (defensif)."""
    from custom_components.rts_gateway import config_flow as cf

    monkeypatch.setattr(cf, "_device_option_fields", lambda device: {})

    async def scenario():
        # Flow de configuration.
        flow = SomfyRTSConfigFlow()
        flow.hass = hass
        flow._transmitter_id = TRANSMITTER
        flow._pending_device = complete_device_input()

        result = await flow.async_step_device_options(None)
        # Appairage immediat : formulaire pair (pas d'options intermediaires).
        assert result["step_id"] == "pair"
        assert result["type"] == "form"

    asyncio.run(scenario())


def test_options_device_options_empty_fields_pair_directly(hass, fake_rf, monkeypatch):
    """Idem dans le flow de reconfiguration."""
    from custom_components.rts_gateway import config_flow as cf

    monkeypatch.setattr(cf, "_device_option_fields", lambda device: {})

    async def scenario():
        entry = FakeConfigEntry(
            data={"transmitter_id": TRANSMITTER, "enable_rx": True,
                  "devices": [], "sensors": []}
        )
        hass.config_entries.entries.append(entry)

        flow = SomfyRTSOptionsFlow()
        flow.hass = hass
        flow.handler = entry.entry_id
        flow._pending_device = complete_device_input()

        result = await flow.async_step_device_options(None)
        # Appairage immediat : formulaire pair (pas d'options intermediaires).
        assert result["step_id"] == "pair"
        assert result["type"] == "form"

    asyncio.run(scenario())


def test_reconfigure_changes_transmitter(hass, fake_rf):
    """Le flow Reconfigure change l'emetteur et repropage partout."""
    async def scenario():
        entry = FakeConfigEntry(
            data={
                "transmitter_id": TRANSMITTER,
                "devices": [make_device()],
                "sensors": [],
            }
        )
        hass.config_entries.entries.append(entry)

        flow = SomfyRTSConfigFlow()
        flow.hass = hass
        flow.context = {"source": "reconfigure", "entry_id": entry.entry_id}
        flow.handler = entry.entry_id
        # Formulaire pre-rempli avec l'emetteur courant.
        result = await flow.async_step_reconfigure(None)
        assert result["step_id"] == "reconfigure"

        result = await flow.async_step_reconfigure(
            {"transmitter_id": "radio_frequency.other_radio"}
        )
        assert result["type"] == "abort"
        assert result["reason"] == "reconfigure_successful"
        data = hass.config_entries.updated[-1][1]["data"]
        assert data["transmitter_id"] == "radio_frequency.other_radio"
        assert (
            data["devices"][0]["transmitter_id"]
            == "radio_frequency.other_radio"
        )

    asyncio.run(scenario())


def test_used_transmitter_not_offered_again(hass):
    """Un transmetteur deja configure ne peut pas creer une seconde entree."""
    async def scenario():
        entry = FakeConfigEntry(
            data={"transmitter_id": TRANSMITTER, "devices": [], "sensors": []}
        )
        hass.config_entries.entries.append(entry)

        flow = SomfyRTSConfigFlow()
        flow.hass = hass
        result = await flow.async_step_user(None)
        assert result["type"] == "abort"
        assert result["reason"] == "no_transmitters"

    asyncio.run(scenario())


def test_user_step_without_transmitters(hass, monkeypatch):
    async def scenario():
        from custom_components.rts_gateway import rf

        monkeypatch.setattr(rf, "get_transmitters", lambda hass: [])
        flow = SomfyRTSConfigFlow()
        flow.hass = hass
        result = await flow.async_step_user(None)
        assert result["type"] == "abort"
        assert result["reason"] == "no_transmitters"

    asyncio.run(scenario())


def test_full_flow(hass, fake_rf, flow):
    async def scenario():
        # Select the transmitter.
        result = await flow.async_step_user({"transmitter_id": TRANSMITTER})
        assert result["type"] == "menu"
        assert result["menu_options"] == [
            "add_device", "add_group", "add_sensor", "manage_group", "done"
        ]

        # Add a device: conditional options (invert only for a 56-bit
        # roller), then the pairing step.
        result = await flow.async_step_add_device(complete_device_input())
        assert result["step_id"] == "device_options"
        assert set(form_defaults(result["data_schema"])) == {
            "invert", "invert_position", "my_position",
            "simulate_my", "stop_method",
        }
        result = await flow.async_step_device_options(
            {"invert": False, "invert_position": False, "my_position": 50}
        )
        assert result["step_id"] == "pair"
        assert result["description_placeholders"]["address"] == "00000001"

        # Send the pairing command.
        result = await flow.async_step_pair({})
        assert fake_rf.sent[-1]["command"] == SomfyCommand.PROG.value
        assert result["step_id"] == "pair_result"

        # Confirm the motor reacted.
        result = await flow.async_step_pair_result({"confirm": True})
        assert result["type"] == "menu"
        assert flow._devices[0]["name"] == "Volet salon"

        # Add a sensor.
        result = await flow.async_step_add_sensor(
            {"name": "Capteur", "address": "11112222"}
        )
        assert result["type"] == "menu"
        assert flow._sensors[0]["address"] == "11112222"

        # Finish: the entry contains the transmitter, devices, sensors.
        result = await flow.async_step_done(None)
        assert result["type"] == "create_entry"
        data = result["data"]
        assert data["transmitter_id"] == TRANSMITTER
        assert data["devices"][0]["address"] == "00000001"
        assert data["sensors"][0]["address"] == "11112222"
        assert data["enable_rx"] is True

    asyncio.run(scenario())


def test_pair_failure_shows_error(flow, fake_rf, monkeypatch):
    async def scenario():
        async def boom(*args, **kwargs):
            raise HomeAssistantError("RF transmission failed")

        monkeypatch.setattr(
            config_flow.rf, "async_send_command", boom
        )
        flow._transmitter_id = TRANSMITTER
        flow._pending_device = complete_device_input()
        result = await flow.async_step_pair({})
        assert result["step_id"] == "pair"
        assert result["errors"]["base"] == "transmission_failed"
        # Not confirmed: retrying sends again.
        result = await flow.async_step_pair_result({"confirm": False})
        assert result["step_id"] == "pair"

    asyncio.run(scenario())


def test_add_device_rejects_address_of_existing_entry(hass, flow):
    async def scenario():
        entry = FakeConfigEntry(
            data={"devices": [{"address": "11111111"}], "sensors": []}
        )
        hass.config_entries.entries.append(entry)
        result = await flow.async_step_add_device(complete_device_input(address="11111111"))
        assert result["errors"]["address"] == "address_in_use"

    asyncio.run(scenario())


def test_add_device_rejects_in_session_duplicate(flow):
    async def scenario():
        flow._transmitter_id = TRANSMITTER
        # A device added earlier in the same flow session.
        result = await flow.async_step_add_device(complete_device_input())
        await flow.async_step_pair({})
        await flow.async_step_pair_result({"confirm": True})

        result = await flow.async_step_add_device(complete_device_input())
        assert result["errors"]["address"] == "address_in_use"

    asyncio.run(scenario())


def test_add_device_rejects_in_session_sensor_address(flow):
    """Regression: device addresses must not collide with in-session sensors."""
    async def scenario():
        flow._transmitter_id = TRANSMITTER
        await flow.async_step_add_sensor({"name": "Capteur", "address": "02222222"})
        result = await flow.async_step_add_device(complete_device_input(address="02222222"))
        assert result["errors"]["address"] == "address_in_use"

    asyncio.run(scenario())


def test_add_sensor_rejects_used_addresses(hass, flow):
    async def scenario():
        flow._transmitter_id = TRANSMITTER
        entry = FakeConfigEntry(
            data={"devices": [{"address": "03333333"}], "sensors": []}
        )
        hass.config_entries.entries.append(entry)

        result = await flow.async_step_add_sensor({"name": "C", "address": "03333333"})
        assert result["errors"]["address"] == "address_in_use"

        result = await flow.async_step_add_sensor({"name": "C", "address": "04444444"})
        assert result["type"] == "menu"

    asyncio.run(scenario())


def test_sensor_address_defaults_to_generated(flow):
    async def scenario():
        flow._transmitter_id = TRANSMITTER
        result = await flow.async_step_add_sensor({"name": "Capteur", "address": ""})
        assert result["type"] == "menu"
        address = flow._sensors[0]["address"]
        assert len(address) == 8 and address.isdigit()

    asyncio.run(scenario())


def test_options_flow(hass, fake_rf):
    async def scenario():
        entry = FakeConfigEntry(
            data={
                "transmitter_id": TRANSMITTER,
                "enable_rx": True,
                "devices": [make_device()],
                "sensors": [],
            }
        )
        hass.config_entries.entries.append(entry)

        flow = SomfyRTSOptionsFlow()
        flow.hass = hass
        flow.handler = entry.entry_id

        # Menu d'entree.
        result = await flow.async_step_init(None)
        assert result["type"] == "menu"
        assert result["menu_options"] == [
            "general", "transmitter", "add_device", "edit_device", "remove_device",
            "add_group", "manage_group", "remove_group",
            "add_remote", "remove_remote", "done",
        ]

        # Reglages generaux : defaut issus de l'entree.
        result = await flow.async_step_general(None)
        assert result["step_id"] == "general"
        assert form_defaults(result["data_schema"])["enable_rx"] is True
        result = await flow.async_step_general({"enable_rx": False})
        assert result["type"] == "create_entry"
        assert result["data"] == {"enable_rx": False}

    asyncio.run(scenario())


def test_options_edit_device(hass, fake_rf):
    async def scenario():
        blind = make_device(
            name="Store vénitien", device_type="blind", protocol="rts_80bit",
            tilt=True, tilt_step_percent=12, step_size=90,
        )
        entry = FakeConfigEntry(
            data={
                "transmitter_id": TRANSMITTER,
                "enable_rx": True,
                "devices": [make_device(), blind],
                "sensors": [],
            }
        )
        hass.config_entries.entries.append(entry)

        flow = SomfyRTSOptionsFlow()
        flow.hass = hass
        flow.handler = entry.entry_id

        # Selection de l'appareil a modifier.
        result = await flow.async_step_edit_device(None)
        assert result["step_id"] == "edit_device"
        result = await flow.async_step_edit_device({"device_index": "1"})
        assert result["step_id"] == "edit_device_params"

        # Champs pre-remplis, y compris les champs conditionnels 80 bits.
        defaults = form_defaults(result["data_schema"])
        assert defaults["name"] == "Store vénitien"
        assert defaults["open_time_ms"] == 1000
        assert defaults["tilt"] is True
        assert defaults["tilt_step_percent"] == 12
        assert defaults["step_size"] == 90

        # Le formulaire propose les adresses liees courantes.
        defaults = form_defaults(flow._edit_device_schema(flow._devices()[1]))
        assert defaults["linked_remote_addresses"] == []

        # Modification : retour a la liste, persistance immediate.
        result = await flow.async_step_edit_device_params({
            "name": "Store vénitien",
            "open_time_ms": 25000,
            "close_time_ms": 30000,
            "is_group": False,
            "tilt": True,
            "tilt_step_percent": 15,
            "step_size": 90,
            "linked_remote_addresses": ["00500002", "00700007"],
        })
        assert result["step_id"] == "edit_device"
        assert len(hass.config_entries.updated) == 1  # persistance immediate

        # done : aucune ecriture supplementaire, options conservees.
        result = await flow.async_step_done(None)
        assert result["type"] == "create_entry"
        assert result["data"] == {}  # options inchangees
        assert len(hass.config_entries.updated) == 1
        devices = hass.config_entries.updated[0][1]["data"]["devices"]
        assert devices[1]["open_time_ms"] == 25000
        assert devices[1]["tilt_step_percent"] == 15
        assert devices[1]["linked_remote_addresses"] == ["00500002", "00700007"]
        assert devices[0]["name"] == "Test device"  # les autres intacts
        assert entry.data["devices"][1]["open_time_ms"] == 25000

    asyncio.run(scenario())


def test_add_device_56bit_cover_shows_only_inversions(flow):
    """Un volet 56 bits n'a que les inversions comme parametres specifiques."""
    async def scenario():
        result = await flow.async_step_add_device(complete_device_input())
        assert result["step_id"] == "device_options"
        assert set(form_defaults(result["data_schema"])) == {
            "invert", "invert_position", "my_position",
            "simulate_my", "stop_method",
        }

        result = await flow.async_step_device_options(
            {"invert": True, "invert_position": True, "my_position": 40}
        )
        assert result["step_id"] == "pair"
        assert flow._pending_device["invert"] is True
        assert flow._pending_device["invert_position"] is True
        assert flow._pending_device["my_position"] == 40
        # Les parametres non applicables recoivent leurs defauts.
        assert flow._pending_device["toggle_command"] == "MY"
        assert flow._pending_device["step_size"] == 100
        assert flow._pending_device["tilt"] is False

    asyncio.run(scenario())


def test_add_device_80bit_cover_shows_tilt_fields(flow):
    async def scenario():
        result = await flow.async_step_add_device(
            complete_device_input(device_type="blind", protocol="rts_80bit")
        )
        assert result["step_id"] == "device_options"
        defaults = form_defaults(result["data_schema"])
        assert set(defaults) == {
            "invert", "invert_position", "my_position",
            "simulate_my", "stop_method",
            "tilt", "tilt_step_percent", "step_size",
        }

        result = await flow.async_step_device_options(
            {
                "invert": False,
                "invert_position": False,
                "my_position": 50,
                "tilt": True,
                "tilt_step_percent": 10,
                "step_size": 50,
            }
        )
        assert result["step_id"] == "pair"
        assert flow._pending_device["tilt"] is True
        assert flow._pending_device["tilt_step_percent"] == 10
        assert flow._pending_device["step_size"] == 50

    asyncio.run(scenario())


def test_add_blind_56bit_shows_timed_tilt_fields(flow):
    """Un store venitien 56 bits se pilote par moteur temporise."""
    async def scenario():
        result = await flow.async_step_add_device(
            complete_device_input(device_type="blind", protocol="rts_56bit")
        )
        assert result["step_id"] == "device_options"
        defaults = form_defaults(result["data_schema"])
        assert set(defaults) == {
            "invert", "invert_position", "my_position",
            "simulate_my", "stop_method", "tilt", "tilt_time_ms",
        }
        assert defaults["tilt_time_ms"] == 7000

        result = await flow.async_step_device_options(
            {
                "invert": False,
                "invert_position": False,
                "my_position": 50,
                "tilt": True,
                "tilt_time_ms": 9000,
            }
        )
        assert result["step_id"] == "pair"
        assert flow._pending_device["tilt"] is True
        assert flow._pending_device["tilt_time_ms"] == 9000
        assert flow._pending_device["tilt_step_percent"] == 7  # defaut

    asyncio.run(scenario())


def test_add_roller_shutter_80bit_hides_tilt_fields(flow):
    """Un volet roulant n'a pas de lamelles : pas de champs d'inclinaison."""
    async def scenario():
        result = await flow.async_step_add_device(
            complete_device_input(device_type="roller_shutter", protocol="rts_80bit")
        )
        assert result["step_id"] == "device_options"
        defaults = form_defaults(result["data_schema"])
        assert set(defaults) == {
            "invert", "invert_position", "my_position",
            "simulate_my", "stop_method", "step_size",
        }

        result = await flow.async_step_device_options(
            {
                "invert": False,
                "invert_position": False,
                "my_position": 50,
                "step_size": 50,
            }
        )
        assert result["step_id"] == "pair"
        # Le tilt reste desactive meme si l'utilisateur le forcerait.
        assert flow._pending_device["tilt"] is False
        assert flow._pending_device["step_size"] == 50

    asyncio.run(scenario())


def test_add_garage1_hides_invert(flow):
    """Le garage 1-bouton n'a pas de commandes montee/descente distinctes."""
    async def scenario():
        result = await flow.async_step_add_device(
            complete_device_input(device_type="garage1")
        )
        assert result["step_id"] == "device_options"
        defaults = form_defaults(result["data_schema"])
        assert set(defaults) == {
            "invert_position", "my_position",
            "simulate_my", "stop_method",
        }

        result = await flow.async_step_device_options(
            {"invert_position": False, "my_position": 50}
        )
        assert result["step_id"] == "pair"
        # L'inversion de commandes reste desactivee meme si on la forcerait.
        assert flow._pending_device["invert"] is False

    asyncio.run(scenario())


def test_add_socket_shows_only_toggle(flow):
    async def scenario():
        result = await flow.async_step_add_device(
            complete_device_input(device_type="socket")
        )
        assert result["step_id"] == "device_options"
        defaults = form_defaults(result["data_schema"])
        assert set(defaults) == {"toggle_command"}

        result = await flow.async_step_device_options({"toggle_command": "DOWN"})
        assert result["step_id"] == "pair"
        assert flow._pending_device["toggle_command"] == "DOWN"
        # Les parametres 80 bits restent aux defauts.
        assert flow._pending_device["tilt"] is False

    asyncio.run(scenario())


def test_add_light_80bit_shows_dim_fields(flow):
    async def scenario():
        result = await flow.async_step_add_device(
            complete_device_input(device_type="light", protocol="rts_80bit")
        )
        assert result["step_id"] == "device_options"
        defaults = form_defaults(result["data_schema"])
        assert set(defaults) == {
            "toggle_command", "brightness_step_percent", "step_size",
        }

        result = await flow.async_step_device_options({
            "toggle_command": "MY",
            "brightness_step_percent": 10,
            "step_size": 100,
        })
        assert result["step_id"] == "pair"
        assert flow._pending_device["brightness_step_percent"] == 10

    asyncio.run(scenario())


# =============================================================================
# Groupes RTS (plage reservee 1 000 000 - 2 097 151)
# =============================================================================
def test_group_address_range_validation():
    from custom_components.rts_gateway import address_manager
    from custom_components.rts_gateway.const import GROUP_ADDRESS_MAX, GROUP_ADDRESS_MIN

    # Une adresse de groupe doit etre dans la plage reservee.
    assert not address_manager.is_address_valid("12345678", group=True)
    assert address_manager.is_address_valid("00999999", group=False)
    assert not address_manager.is_address_valid("00999999", group=True)
    low = f"{GROUP_ADDRESS_MIN:08d}"
    high = f"{GROUP_ADDRESS_MAX:08d}"
    assert address_manager.is_address_valid(low, group=True)
    assert address_manager.is_address_valid(high, group=True)


def test_generate_group_address(hass):
    from custom_components.rts_gateway import address_manager
    from custom_components.rts_gateway.const import GROUP_ADDRESS_MAX, GROUP_ADDRESS_MIN

    address = address_manager.generate_address(hass, group=True)
    assert GROUP_ADDRESS_MIN <= int(address) <= GROUP_ADDRESS_MAX
    # Une adresse hors plage ne serait jamais generee pour un groupe.
    assert int(address) >= 1_000_000


def test_add_group(flow):
    """Un groupe se cree depuis son propre wizard, sans temps de course."""
    async def scenario():
        result = await flow.async_step_add_group(None)
        assert result["step_id"] == "add_group"
        # Champs minimaux : nom, type, adresse. Ni protocole, ni temps.
        fields = {m.schema for m in result["data_schema"].schema}
        assert fields == {"name", "device_type", "address"}

        result = await flow.async_step_add_group({
            "name": "Groupe salon",
            "device_type": "roller_shutter",
            "address": "01500000",
        })
        assert result["type"] == "menu"
        group = flow._devices[0]
        assert group["is_group"] is True
        assert group["address"] == "01500000"
        assert group["protocol"] == "rts_56bit"
        # Configuration legere : pas de temps de montee/descente.
        assert "open_time_ms" not in group
        assert "close_time_ms" not in group

        # Une adresse hors plage de groupe est refusee pour un groupe.
        result = await flow.async_step_add_group({
            "name": "Groupe", "device_type": "roller_shutter",
            "address": "00999999",
        })
        assert result["errors"]["address"] == "invalid_address"

        # Une adresse de plage groupe est refusee pour un appareil simple.
        result = await flow.async_step_add_device(
            complete_device_input(address="01500000")
        )
        assert result["errors"]["address"] == "invalid_address"

    asyncio.run(scenario())


# =============================================================================
# Telecommandes liees via le formulaire d'edition
# =============================================================================
def test_edit_linked_remotes_validation(hass, fake_rf):
    async def scenario():
        entry = FakeConfigEntry(
            data={
                "transmitter_id": TRANSMITTER,
                "enable_rx": True,
                "devices": [make_device(linked_remote_addresses=[])],
                "sensors": [],
            }
        )
        hass.config_entries.entries.append(entry)

        flow = SomfyRTSOptionsFlow()
        flow.hass = hass
        flow.handler = entry.entry_id

        flow._edit_index = 0
        # Adresse invalide : le formulaire est reaffiche avec l'erreur.
        result = await flow.async_step_edit_device_params({
            "name": "Volet",
            "open_time_ms": 20000,
            "close_time_ms": 20000,
            "is_group": False,
            "invert": False,
            "invert_position": False,
            "my_position": 50,
            "linked_remote_addresses": ["123"],
        })
        assert result["step_id"] == "edit_device_params"
        assert result["errors"]["linked_remote_addresses"] == "invalid_address"
        assert not hass.config_entries.updated

        # Adresse de plage de groupe : refusee aussi.
        result = await flow.async_step_edit_device_params({
            "name": "Volet",
            "open_time_ms": 20000,
            "close_time_ms": 20000,
            "is_group": False,
            "invert": False,
            "invert_position": False,
            "my_position": 50,
            "linked_remote_addresses": ["01500000"],
        })
        assert result["errors"]["linked_remote_addresses"] == "invalid_address"

        # Adresses valides : dedupliquees, normalisees, persistees.
        result = await flow.async_step_edit_device_params({
            "name": "Volet",
            "open_time_ms": 20000,
            "close_time_ms": 20000,
            "is_group": False,
            "invert": False,
            "invert_position": False,
            "my_position": 50,
            "linked_remote_addresses": ["00500002", "00500002 ", "00600003"],
        })
        assert result["step_id"] == "edit_device"
        await flow.async_step_done(None)
        devices = hass.config_entries.updated[0][1]["data"]["devices"]
        assert devices[0]["linked_remote_addresses"] == ["00500002", "00600003"]

        # Liste vide : les liaisons sont retirees.
        await flow.async_step_edit_device_params({
            "name": "Volet",
            "open_time_ms": 20000,
            "close_time_ms": 20000,
            "is_group": False,
            "invert": False,
            "invert_position": False,
            "my_position": 50,
            "linked_remote_addresses": [],
        })
        await flow.async_step_done(None)
        devices = hass.config_entries.updated[-1][1]["data"]["devices"]
        assert devices[0]["linked_remote_addresses"] == []

    asyncio.run(scenario())


# =============================================================================
# Detection des telecommandes (options flow)
# =============================================================================
class _FakeRxHandler:
    def __init__(self, addresses):
        self._addresses = addresses

    def recent_addresses(self, max_age_s=120.0):
        return [
            {"address": a, "command": "UP", "age": 3.0} for a in self._addresses
        ]


def test_add_remote_detection_flow(hass, fake_rf):
    async def scenario():
        entry = FakeConfigEntry(
            data={
                "transmitter_id": TRANSMITTER,
                "enable_rx": True,
                "devices": [make_device()],
                "sensors": [],
            }
        )
        hass.config_entries.entries.append(entry)
        hass.data.setdefault(DOMAIN, {})["rx_handler"] = _FakeRxHandler(
            [3502923, 7611744]
        )

        flow = SomfyRTSOptionsFlow()
        flow.hass = hass
        flow.handler = entry.entry_id

        # 1. Choix de l'appareil.
        result = await flow.async_step_add_remote(None)
        assert result["step_id"] == "add_remote"
        result = await flow.async_step_add_remote({"device_index": "0"})
        assert result["step_id"] == "add_remote_capture"

        # 2. Candidats proposes, tries, deja lieses exclus.
        schema = result["data_schema"]
        defaults = schema({})
        assert defaults["linked_remote_addresses"] == []
        options_list = schema.schema[
            next(k for k in schema.schema if str(k).startswith("linked"))
        ].config["options"]
        values = [o["value"] for o in options_list]
        assert values == ["03502923", "07611744"]

        # 3. Soumission vide : rafraichit sans rien lier.
        result = await flow.async_step_add_remote_capture({})
        assert result["step_id"] == "add_remote_capture"
        assert not hass.config_entries.updated

        # 4. Selection : enregistree, retour au menu.
        result = await flow.async_step_add_remote_capture({
            "linked_remote_addresses": ["07611744"],
        })
        assert result["type"] == "menu"
        await flow.async_step_done(None)
        devices = hass.config_entries.updated[0][1]["data"]["devices"]
        assert devices[0]["linked_remote_addresses"] == ["07611744"]

    asyncio.run(scenario())


def test_add_remote_detection_excludes_already_linked(hass, fake_rf):
    async def scenario():
        entry = FakeConfigEntry(
            data={
                "transmitter_id": TRANSMITTER,
                "enable_rx": True,
                "devices": [make_device(linked_remote_addresses=["07611744"])],
                "sensors": [],
            }
        )
        hass.config_entries.entries.append(entry)
        hass.data.setdefault(DOMAIN, {})["rx_handler"] = _FakeRxHandler(
            [3502923, 7611744]
        )

        flow = SomfyRTSOptionsFlow()
        flow.hass = hass
        flow.handler = entry.entry_id
        await flow.async_step_add_remote({"device_index": "0"})

        result = await flow.async_step_add_remote_capture(None)
        schema = result["data_schema"]
        key = next(k for k in schema.schema if str(k).startswith("linked"))
        values = [o["value"] for o in schema.schema[key].config["options"]]
        assert values == ["03502923"]  # 07611744 deja liee

    asyncio.run(scenario())


def test_add_remote_detection_requires_rx(hass, fake_rf):
    async def scenario():
        entry = FakeConfigEntry(
            data={
                "transmitter_id": TRANSMITTER,
                "enable_rx": False,
                "devices": [make_device()],
                "sensors": [],
            }
        )
        hass.config_entries.entries.append(entry)
        # Pas de rx_handler (reception desactivee).

        flow = SomfyRTSOptionsFlow()
        flow.hass = hass
        flow.handler = entry.entry_id
        await flow.async_step_add_remote({"device_index": "0"})

        result = await flow.async_step_add_remote_capture(None)
        assert result["errors"]["base"] == "rx_disabled"

    asyncio.run(scenario())


# =============================================================================
# Changement d'emetteur RF (reparation apres renommage)
# =============================================================================
def test_change_transmitter(hass, fake_rf):
    async def scenario():
        entry = FakeConfigEntry(
            data={
                "transmitter_id": "radio_frequency.old_id",
                "enable_rx": True,
                "devices": [make_device()],
                "sensors": [],
            }
        )
        hass.config_entries.entries.append(entry)

        flow = SomfyRTSOptionsFlow()
        flow.hass = hass
        flow.handler = entry.entry_id

        result = await flow.async_step_transmitter(None)
        assert result["step_id"] == "transmitter"
        defaults = form_defaults(result["data_schema"])
        # L'ancien émetteur n'existe plus : défaut = premier disponible.
        assert defaults["transmitter_id"] == "radio_frequency.test_radio"

        result = await flow.async_step_transmitter({
            "transmitter_id": "radio_frequency.test_radio",
        })
        assert result["type"] == "create_entry"
        assert entry.data["transmitter_id"] == "radio_frequency.test_radio"
        assert hass.config_entries.updated[-1][0] is entry

    asyncio.run(scenario())


def test_change_transmitter_none_available(hass, monkeypatch):
    async def scenario():
        from custom_components.rts_gateway import rf as rf_module

        monkeypatch.setattr(rf_module, "get_transmitters", lambda hass: [])
        entry = FakeConfigEntry(
            data={
                "transmitter_id": "radio_frequency.old_id",
                "enable_rx": True,
                "devices": [make_device()],
                "sensors": [],
            }
        )
        hass.config_entries.entries.append(entry)

        flow = SomfyRTSOptionsFlow()
        flow.hass = hass
        flow.handler = entry.entry_id

        result = await flow.async_step_transmitter(None)
        assert result["errors"]["base"] == "no_transmitters"

    asyncio.run(scenario())


# =============================================================================
# Ajout d'appareils via le flux de reconfiguration
# =============================================================================
def test_options_add_device_full_wizard(hass, fake_rf):
    async def scenario():
        entry = FakeConfigEntry(
            data={
                "transmitter_id": TRANSMITTER,
                "enable_rx": True,
                "devices": [make_device()],
                "sensors": [],
            }
        )
        hass.config_entries.entries.append(entry)

        flow = SomfyRTSOptionsFlow()
        flow.hass = hass
        flow.handler = entry.entry_id

        # 1. Formulaire de base (adresse generee, appareil 56 bits).
        result = await flow.async_step_add_device(complete_device_input())
        assert result["step_id"] == "device_options"
        assert set(form_defaults(result["data_schema"])) == {
            "invert", "invert_position", "my_position",
            "simulate_my", "stop_method",
        }

        # 2. Options conditionnelles -> appairage.
        result = await flow.async_step_device_options(
            {"invert": False, "invert_position": False, "my_position": 50}
        )
        assert result["step_id"] == "pair"
        assert result["description_placeholders"]["address"] == "00000001"

        # 3. Envoi du PROG avec l'emetteur de l'entree.
        result = await flow.async_step_pair({})
        assert fake_rf.sent[-1]["command"] == SomfyCommand.PROG.value
        assert fake_rf.sent[-1]["transmitter_id"] == TRANSMITTER
        assert result["step_id"] == "pair_result"

        # 4. Confirmation -> retour au menu, persistance immediate.
        result = await flow.async_step_pair_result({"confirm": True})
        assert result["type"] == "menu"
        assert len(hass.config_entries.updated) == 1

        # 5. Done : aucune ecriture supplementaire.
        await flow.async_step_done(None)
        devices = hass.config_entries.updated[0][1]["data"]["devices"]
        assert len(devices) == 2
        assert devices[1]["address"] == "00000001"
        assert devices[0]["name"] == "Test device"  # l'existant est intact

    asyncio.run(scenario())


def test_options_add_device_rejects_duplicates(hass, fake_rf):
    async def scenario():
        entry = FakeConfigEntry(
            data={
                "transmitter_id": TRANSMITTER,
                "enable_rx": True,
                "devices": [make_device(address="12345678")],
                "sensors": [],
            }
        )
        hass.config_entries.entries.append(entry)

        flow = SomfyRTSOptionsFlow()
        flow.hass = hass
        flow.handler = entry.entry_id

        # Adresse deja utilisee par un appareil de l'entree.
        result = await flow.async_step_add_device(
            complete_device_input(address="12345678")
        )
        assert result["errors"]["address"] == "address_in_use"

        # Adresse de plage de groupe refusee pour un appareil simple.
        result = await flow.async_step_add_device(
            complete_device_input(address="01500000")
        )
        assert result["errors"]["address"] == "invalid_address"

    asyncio.run(scenario())


def test_options_add_group_then_members(hass, fake_rf):
    """Le wizard cree le groupe puis enchaine sur le choix des membres."""
    async def scenario():
        entry = _make_entry(hass, [make_device(name="Volet", address="10000001")])
        flow = _make_options_flow(hass, entry)

        result = await flow.async_step_add_group({
            "name": "Groupe",
            "device_type": "roller_shutter",
            "address": "01500000",
        })
        # Enchainement direct sur la selection des membres.
        assert result["step_id"] == "manage_group_members"
        assert form_defaults(result["data_schema"])["device_index"] == []

        # On coche le volet existant : deroule interactif par volet.
        result = await flow.async_step_manage_group_members(
            {"device_index": ["0"]}
        )
        assert result["step_id"] == "member_memory"

        result = await flow.async_step_member_memory({"jogged": True})
        assert result["step_id"] == "member_pair_confirm"
        result = await flow.async_step_member_pair_confirm({"paired": True})
        assert result["type"] == "menu"

        assert [(f["address"], f["repeats"]) for f in fake_rf.sent] == [
            (10000001, PROG_MODE_REPEATS), (1500000, 3),
        ]
        devices = entry.data["devices"]
        assert len(devices) == 2
        assert devices[1]["is_group"] is True
        assert devices[1]["address"] == "01500000"
        assert "open_time_ms" not in devices[1]
        assert devices[0]["group_addresses"] == ["01500000"]

    asyncio.run(scenario())

def test_options_edit_group_name_only(hass, fake_rf):
    """Un groupe n'est pas un appareil : l'edition n'offre que le nom."""
    async def scenario():
        entry = FakeConfigEntry(
            data={
                "transmitter_id": TRANSMITTER,
                "enable_rx": True,
                "devices": [
                    make_device(name="Groupe", address="1500000", is_group=True)
                ],
                "sensors": [],
            }
        )
        hass.config_entries.entries.append(entry)

        flow = SomfyRTSOptionsFlow()
        flow.hass = hass
        flow.handler = entry.entry_id

        result = await flow.async_step_edit_device({"device_index": "0"})
        assert result["step_id"] == "edit_device_params"
        fields = {m.schema for m in result["data_schema"].schema}
        assert fields == {"name"}

        result = await flow.async_step_edit_device_params({"name": "Groupe salon"})
        assert result["step_id"] == "edit_device"
        await flow.async_step_done(None)
        devices = hass.config_entries.updated[0][1]["data"]["devices"]
        assert devices[0]["name"] == "Groupe salon"
        assert devices[0]["is_group"] is True

    asyncio.run(scenario())


def test_options_add_device_pair_retry(hass, fake_rf):
    async def scenario():
        entry = FakeConfigEntry(
            data={
                "transmitter_id": TRANSMITTER,
                "enable_rx": True,
                "devices": [],
                "sensors": [],
            }
        )
        hass.config_entries.entries.append(entry)

        flow = SomfyRTSOptionsFlow()
        flow.hass = hass
        flow.handler = entry.entry_id

        await flow.async_step_add_device(complete_device_input())
        await flow.async_step_device_options(
            {"invert": False, "invert_position": False, "my_position": 50}
        )
        await flow.async_step_pair({})
        # Pas de reaction : retour a l'envoi du PROG.
        result = await flow.async_step_pair_result({"confirm": False})
        assert result["step_id"] == "pair"

    asyncio.run(scenario())

def test_options_remove_device_with_unpair(hass, fake_rf):
    """Retrait + trame PROG de desappairage vers le moteur."""
    async def scenario():
        entry = FakeConfigEntry(
            data={
                "transmitter_id": TRANSMITTER,
                "enable_rx": True,
                "devices": [
                    make_device(address="11111111"),
                    make_device(address="22222222"),
                ],
                "sensors": [],
            }
        )
        hass.config_entries.entries.append(entry)

        flow = SomfyRTSOptionsFlow()
        flow.hass = hass
        flow.handler = entry.entry_id

        result = await flow.async_step_remove_device(None)
        assert result["step_id"] == "remove_device"
        result = await flow.async_step_remove_device({"device_index": "0"})
        assert result["step_id"] == "remove_device_confirm"
        assert result["description_placeholders"]["address"] == "11111111"

        result = await flow.async_step_remove_device_confirm({"unpair": True})
        assert result["step_id"] == "remove_device_memory"
        # Appui PROG long sur l'adresse du volet : ouverture memoire.
        assert fake_rf.sent[-1]["command"] == "PROG"
        assert fake_rf.sent[-1]["address"] == 11111111
        assert fake_rf.sent[-1]["transmitter_id"] == TRANSMITTER
        assert fake_rf.sent[-1]["repeats"] == PROG_MODE_REPEATS

        result = await flow.async_step_remove_device_memory({"jogged": True})
        assert result["step_id"] == "remove_device_unpair"
        # Appui PROG court : desappairage de l'adresse.
        assert fake_rf.sent[-1]["command"] == "PROG"
        assert fake_rf.sent[-1]["address"] == 11111111
        assert fake_rf.sent[-1]["repeats"] == DEFAULT_FRAME_REPEATS

        result = await flow.async_step_remove_device_unpair({"unpaired": True})
        assert result["type"] == "menu"  # retour au menu
        assert len(hass.config_entries.updated) == 1  # persistance immediate

        await flow.async_step_done(None)
        devices = hass.config_entries.updated[0][1]["data"]["devices"]
        assert len(devices) == 1
        assert devices[0]["address"] == "22222222"

    asyncio.run(scenario())


def test_options_remove_device_unpairs_group_addresses_first(hass, fake_rf):
    """Membre de groupe supprime : l'adresse du groupe est desappairee
    avant celle de l'appareil (la sienne ouvre la memoire)."""
    async def scenario():
        devices = [
            make_device(name="Groupe", address="1500000", is_group=True),
            make_device(
                name="Volet",
                address="10000001",
                group_addresses=["1500000"],
            ),
        ]
        entry = _make_entry(hass, devices)
        flow = _make_options_flow(hass, entry)

        await flow.async_step_remove_device({"device_index": "1"})
        result = await flow.async_step_remove_device_confirm({"unpair": True})
        assert result["step_id"] == "remove_device_memory"
        # Appui long sur l'adresse du volet (canal encore appairé).
        assert fake_rf.sent[-1]["address"] == 10000001
        assert fake_rf.sent[-1]["repeats"] == PROG_MODE_REPEATS

        result = await flow.async_step_remove_device_memory({"jogged": True})
        assert result["step_id"] == "remove_device_unpair"
        # PROG court sur l'adresse du groupe, pas celle du volet.
        assert fake_rf.sent[-1]["address"] == 1500000
        assert fake_rf.sent[-1]["repeats"] == DEFAULT_FRAME_REPEATS
        assert result["description_placeholders"]["target"] == "Groupe"

        result = await flow.async_step_remove_device_unpair({"unpaired": True})
        assert result["step_id"] == "remove_device_memory"
        # Sequence suivante : nouvel appui long sur le volet.
        assert fake_rf.sent[-1]["address"] == 10000001
        assert fake_rf.sent[-1]["repeats"] == PROG_MODE_REPEATS

        result = await flow.async_step_remove_device_memory({"jogged": True})
        assert result["step_id"] == "remove_device_unpair"
        # PROG court sur l'adresse du volet, la derniere de la file.
        assert fake_rf.sent[-1]["address"] == 10000001
        assert fake_rf.sent[-1]["repeats"] == DEFAULT_FRAME_REPEATS
        assert result["description_placeholders"]["target"] == "Volet"

        result = await flow.async_step_remove_device_unpair({"unpaired": True})
        assert result["type"] == "menu"
        assert len(hass.config_entries.updated) == 1
        remaining = hass.config_entries.updated[0][1]["data"]["devices"]
        assert [d["address"] for d in remaining] == ["1500000"]

    asyncio.run(scenario())


def test_options_remove_device_without_unpair(hass, fake_rf):
    """Retrait simple : aucune trame PROG n'est emise."""
    async def scenario():
        entry = FakeConfigEntry(
            data={"transmitter_id": TRANSMITTER, "enable_rx": True,
                  "devices": [make_device()], "sensors": []}
        )
        hass.config_entries.entries.append(entry)

        flow = SomfyRTSOptionsFlow()
        flow.hass = hass
        flow.handler = entry.entry_id

        await flow.async_step_remove_device({"device_index": "0"})
        result = await flow.async_step_remove_device_confirm({"unpair": False})
        assert result["type"] == "menu"
        assert fake_rf.sent == []

        await flow.async_step_done(None)
        assert hass.config_entries.updated[0][1]["data"]["devices"] == []

    asyncio.run(scenario())


def test_options_remove_device_confirm_form(hass, fake_rf):
    """Le formulaire ne propose que le desappairage guide, coche par defaut."""
    async def scenario():
        entry = FakeConfigEntry(
            data={"transmitter_id": TRANSMITTER, "enable_rx": True,
                  "devices": [make_device()], "sensors": []}
        )
        hass.config_entries.entries.append(entry)

        flow = SomfyRTSOptionsFlow()
        flow.hass = hass
        flow.handler = entry.entry_id

        await flow.async_step_remove_device({"device_index": "0"})
        result = await flow.async_step_remove_device_confirm(None)
        assert result["step_id"] == "remove_device_confirm"
        fields = {m.schema for m in result["data_schema"].schema}
        assert fields == {"unpair"}
        assert result["data_schema"]({}) == {"unpair": True}
        assert fake_rf.sent == []

    asyncio.run(scenario())


def test_options_remove_device_memory_forms(hass, fake_rf):
    """Les etapes guidees affichent une case a cocher, sinon
    la soumission est vide et l'etape boucle sur son erreur."""
    async def scenario():
        entry = FakeConfigEntry(
            data={"transmitter_id": TRANSMITTER, "enable_rx": True,
                  "devices": [make_device()], "sensors": []}
        )
        hass.config_entries.entries.append(entry)

        flow = SomfyRTSOptionsFlow()
        flow.hass = hass
        flow.handler = entry.entry_id

        await flow.async_step_remove_device({"device_index": "0"})
        await flow.async_step_remove_device_confirm({"unpair": True})
        result = await flow.async_step_remove_device_memory(None)
        assert result["step_id"] == "remove_device_memory"
        assert {m.schema for m in result["data_schema"].schema} == {"jogged"}

        result = await flow.async_step_remove_device_memory({"jogged": True})
        assert result["step_id"] == "remove_device_unpair"
        assert {m.schema for m in result["data_schema"].schema} == {"unpaired"}

    asyncio.run(scenario())


def test_options_remove_remote(hass, fake_rf):
    """Dettacher une telecommande liee d'un appareil."""
    async def scenario():
        entry = FakeConfigEntry(
            data={"transmitter_id": TRANSMITTER, "enable_rx": True,
                  "sensors": [],
                  "devices": [make_device(
                      address="11111111",
                      linked_remote_addresses=["07611744", "03502923"],
                  )]}
        )
        hass.config_entries.entries.append(entry)

        flow = SomfyRTSOptionsFlow()
        flow.hass = hass
        flow.handler = entry.entry_id

        result = await flow.async_step_remove_remote(None)
        assert result["step_id"] == "remove_remote"
        result = await flow.async_step_remove_remote({"device_index": "0"})
        assert result["step_id"] == "remove_remote_confirm"
        # Le schema attend l'adresse parmi les deux liees.
        result["data_schema"]({"remote_address": "07611744"})

        result = await flow.async_step_remove_remote_confirm(
            {"remote_address": "07611744"}
        )
        assert result["type"] == "menu"
        assert fake_rf.sent == []  # dettachement purement comptable

        await flow.async_step_done(None)
        devices = hass.config_entries.updated[0][1]["data"]["devices"]
        assert devices[0]["linked_remote_addresses"] == ["03502923"]

    asyncio.run(scenario())


def test_options_remove_remote_without_remotes(hass, fake_rf):
    """Un appareil sans telecommande liee ramene au menu."""
    async def scenario():
        entry = FakeConfigEntry(
            data={"transmitter_id": TRANSMITTER, "enable_rx": True,
                  "devices": [make_device()], "sensors": []}
        )
        hass.config_entries.entries.append(entry)

        flow = SomfyRTSOptionsFlow()
        flow.hass = hass
        flow.handler = entry.entry_id

        # Un appareil sans telecommande liee ramene directement au menu.
        result = await flow.async_step_remove_remote({"device_index": "0"})
        assert result["type"] == "menu"

    asyncio.run(scenario())


# =============================================================================
# Wizard : gestion des membres d'un groupe RTS
# =============================================================================
def _make_entry(hass, devices):
    entry = FakeConfigEntry(
        data={
            "transmitter_id": TRANSMITTER,
            "enable_rx": True,
            "devices": devices,
            "sensors": [],
        }
    )
    hass.config_entries.entries.append(entry)
    return entry


def _make_options_flow(hass, entry):
    flow = SomfyRTSOptionsFlow()
    flow.hass = hass
    flow.handler = entry.entry_id
    return flow


def test_options_manage_group_without_groups(hass, fake_rf):
    """Aucun groupe dans l'entree : retour direct au menu."""
    async def scenario():
        entry = _make_entry(hass, [make_device()])
        flow = _make_options_flow(hass, entry)

        result = await flow.async_step_manage_group(None)
        assert result["type"] == "menu"

    asyncio.run(scenario())


def test_options_manage_group_selects_group(hass, fake_rf):
    """Le wizard liste les groupes puis les membres avec defauts."""
    async def scenario():
        entry = _make_entry(hass, [
            make_device(name="Volet", address="10000001"),
            make_device(name="Groupe", address="1500000", is_group=True),
        ])
        flow = _make_options_flow(hass, entry)

        result = await flow.async_step_manage_group(None)
        assert result["step_id"] == "manage_group"

        result = await flow.async_step_manage_group({"device_index": "1"})
        assert result["step_id"] == "manage_group_members"
        # Aucun membre courant par defaut.
        assert form_defaults(result["data_schema"])["device_index"] == []

    asyncio.run(scenario())


def test_options_manage_group_members_toggle(hass, fake_rf):
    """Chaque volet passe par les deux mouvements confirmes."""
    async def scenario():
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from conftest import make_runtime_data

        devices = [
            make_device(name="Groupe", address="1500000", is_group=True),
            make_device(
                name="Volet 1",
                address="10000001",
                group_addresses=["1500000"],
            ),
            make_device(name="Volet 2", address="10000002"),
        ]
        entry = _make_entry(hass, devices)

        entities = {}
        for entity_id, address in (
            ("cover.groupe", "1500000"),
            ("cover.volet_1", "10000001"),
            ("cover.volet_2", "10000002"),
        ):
            entities[entity_id] = SimpleNamespace(
                _address=address,
                async_update_group_addresses=MagicMock(),
                async_write_ha_state=MagicMock(),
            )
        entry.runtime_data = make_runtime_data(entities=entities)

        flow = _make_options_flow(hass, entry)
        await flow.async_step_manage_group({"device_index": "0"})
        # Volet 2 coche (ajout), Volet 1 decoche (retrait).
        result = await flow.async_step_manage_group_members(
            {"device_index": ["2"]}
        )

        # File d'attente : ajout de Volet 2 puis retrait de Volet 1.
        assert result["step_id"] == "member_memory"
        assert [(f["address"], f["repeats"]) for f in fake_rf.sent] == [
            (10000002, PROG_MODE_REPEATS),
        ]

        # Premier mouvement confirme : appui PROG du groupe.
        result = await flow.async_step_member_memory({"jogged": True})
        assert result["step_id"] == "member_pair_confirm"
        assert [(f["address"], f["repeats"]) for f in fake_rf.sent] == [
            (10000002, PROG_MODE_REPEATS), (1500000, 3),
        ]

        # Second mouvement confirme : volet suivant.
        result = await flow.async_step_member_pair_confirm({"paired": True})
        assert result["step_id"] == "member_memory"
        assert [(f["address"], f["repeats"]) for f in fake_rf.sent][-1] == (
            10000001, PROG_MODE_REPEATS,
        )

        result = await flow.async_step_member_memory({"jogged": True})
        result = await flow.async_step_member_pair_confirm({"paired": True})
        assert result["type"] == "menu"

        # Persistance immediate et entites rafraichies.
        assert entry.data["devices"][1]["group_addresses"] == []
        assert entry.data["devices"][2]["group_addresses"] == ["1500000"]
        entities["cover.volet_1"].async_update_group_addresses.assert_called_once_with([])
        entities["cover.volet_2"].async_update_group_addresses.assert_called_once_with(
            ["1500000"]
        )
        entities["cover.groupe"].async_write_ha_state.assert_called_once()

    asyncio.run(scenario())

def test_options_manage_group_transmission_failure(hass, fake_rf, monkeypatch):
    """Echec radio : erreur traduite, aucune modification persistee."""
    async def scenario():
        from custom_components.rts_gateway import rf

        async def boom(*args, **kwargs):
            raise HomeAssistantError("transmitter gone")

        monkeypatch.setattr(rf, "async_send_command", boom)

        devices = [
            make_device(name="Groupe", address="1500000", is_group=True),
            make_device(name="Volet", address="10000001"),
        ]
        entry = _make_entry(hass, devices)
        flow = _make_options_flow(hass, entry)

        await flow.async_step_manage_group({"device_index": "0"})
        result = await flow.async_step_manage_group_members(
            {"device_index": ["1"]}
        )

        assert result["type"] == "form"
        assert result["step_id"] == "member_memory"
        assert result["errors"] == {"base": "transmission_failed"}
        assert entry.data["devices"][1].get("group_addresses", []) == []

    asyncio.run(scenario())

def test_options_manage_group_persists_pending_edits(hass, fake_rf):
    """Les editions en attente restent coherentes apres une modification."""
    async def scenario():
        devices = [
            make_device(name="Groupe", address="1500000", is_group=True),
            make_device(name="Volet", address="10000001"),
        ]
        entry = _make_entry(hass, devices)
        flow = _make_options_flow(hass, entry)

        # Une edition en attente (non persistee) : renommage du volet.
        pending = [dict(d) for d in devices]
        pending[1]["name"] = "Volet renomme"
        flow._edited_devices = pending

        await flow.async_step_manage_group({"device_index": "0"})
        await flow.async_step_manage_group_members({"device_index": ["1"]})
        await flow.async_step_member_memory({"jogged": True})
        result = await flow.async_step_member_pair_confirm({"paired": True})
        assert result["type"] == "menu"

        # L'edition en attente et l'appartenance sont toutes deux
        # conservees dans l'entree.
        assert entry.data["devices"][1]["name"] == "Volet renomme"
        assert entry.data["devices"][1]["group_addresses"] == ["1500000"]

    asyncio.run(scenario())

# =============================================================================
# Wizard d'installation : gestion des membres d'un groupe
# =============================================================================
def test_init_manage_group_without_groups(hass, fake_rf):
    """Aucun groupe cree : retour direct au menu."""
    async def scenario():
        flow = SomfyRTSConfigFlow()
        flow.hass = hass
        flow._transmitter_id = TRANSMITTER
        flow._devices = [make_device(name="Volet", address="10000001")]
        flow._sensors = []

        result = await flow.async_step_manage_group(None)
        assert result["type"] == "menu"

    asyncio.run(scenario())


def test_init_manage_group_members(hass, fake_rf):
    """Le wizard d'installation gere l'ajout et le retrait de membres."""
    async def scenario():
        flow = SomfyRTSConfigFlow()
        flow.hass = hass
        flow._transmitter_id = TRANSMITTER
        flow._devices = [
            make_device(name="Groupe", address="1500000", is_group=True),
            make_device(name="Volet", address="10000001"),
        ]
        flow._sensors = []

        # Choix du groupe.
        result = await flow.async_step_manage_group(None)
        assert result["step_id"] == "manage_group"

        result = await flow.async_step_manage_group({"device_index": "0"})
        assert result["step_id"] == "manage_group_members"
        assert form_defaults(result["data_schema"])["device_index"] == []

        # Ajout du volet : deroule interactif avec mouvements confirmes.
        result = await flow.async_step_manage_group_members(
            {"device_index": ["1"]}
        )
        assert result["step_id"] == "member_memory"
        assert [(f["address"], f["repeats"]) for f in fake_rf.sent] == [
            (10000001, PROG_MODE_REPEATS),
        ]

        result = await flow.async_step_member_memory({"jogged": True})
        assert result["step_id"] == "member_pair_confirm"
        assert [(f["address"], f["repeats"]) for f in fake_rf.sent] == [
            (10000001, PROG_MODE_REPEATS), (1500000, 3),
        ]

        result = await flow.async_step_member_pair_confirm({"paired": True})
        assert result["type"] == "menu"
        assert flow._devices[1]["group_addresses"] == ["1500000"]

        # Retrait du volet : meme sequence, appartenance retiree.
        await flow.async_step_manage_group({"device_index": "0"})
        await flow.async_step_manage_group_members({"device_index": []})
        await flow.async_step_member_memory({"jogged": True})
        result = await flow.async_step_member_pair_confirm({"paired": True})
        assert result["type"] == "menu"
        assert flow._devices[1]["group_addresses"] == []

    asyncio.run(scenario())

def test_init_manage_group_transmission_failure(hass, fake_rf, monkeypatch):
    """Echec radio : erreur traduite, appartenance inchangee."""
    async def scenario():
        from custom_components.rts_gateway import rf

        async def boom(*args, **kwargs):
            raise HomeAssistantError("transmitter gone")

        monkeypatch.setattr(rf, "async_send_command", boom)
        flow = SomfyRTSConfigFlow()
        flow.hass = hass
        flow._transmitter_id = TRANSMITTER
        flow._devices = [
            make_device(name="Groupe", address="1500000", is_group=True),
            make_device(name="Volet", address="10000001"),
        ]
        flow._sensors = []

        await flow.async_step_manage_group({"device_index": "0"})
        result = await flow.async_step_manage_group_members(
            {"device_index": ["1"]}
        )
        assert result["type"] == "form"
        assert result["errors"] == {"base": "transmission_failed"}
        assert flow._devices[1].get("group_addresses", []) == []

    asyncio.run(scenario())

def test_options_member_memory_retry(hass, fake_rf):
    """Pas de reaction : erreur traduite + nouvel appui long renvoye."""
    async def scenario():
        entry = _make_entry(hass, [
            make_device(name="Groupe", address="1500000", is_group=True),
            make_device(name="Volet", address="10000001"),
        ])
        flow = _make_options_flow(hass, entry)

        await flow.async_step_manage_group({"device_index": "0"})
        await flow.async_step_manage_group_members({"device_index": ["1"]})
        assert len(fake_rf.sent) == 1  # appui long a l'entree

        result = await flow.async_step_member_memory({"jogged": False})
        assert result["step_id"] == "member_memory"
        assert result["errors"] == {"base": "memory_not_opened"}
        assert len(fake_rf.sent) == 2  # nouvel appui long
        assert fake_rf.sent[-1]["repeats"] == PROG_MODE_REPEATS
        # Aucune modification persistee.
        assert entry.data["devices"][1].get("group_addresses", []) == []

    asyncio.run(scenario())

def test_options_member_pair_retry(hass, fake_rf):
    """Second mouvement absent : PROG du groupe renvoye."""
    async def scenario():
        entry = _make_entry(hass, [
            make_device(name="Groupe", address="1500000", is_group=True),
            make_device(name="Volet", address="10000001"),
        ])
        flow = _make_options_flow(hass, entry)

        await flow.async_step_manage_group({"device_index": "0"})
        await flow.async_step_manage_group_members({"device_index": ["1"]})
        await flow.async_step_member_memory({"jogged": True})
        assert len(fake_rf.sent) == 2

        result = await flow.async_step_member_pair_confirm({"paired": False})
        assert result["step_id"] == "member_pair_confirm"
        assert result["errors"] == {"base": "pair_not_confirmed"}
        assert len(fake_rf.sent) == 3  # PROG du groupe renvoye
        assert fake_rf.sent[-1]["address"] == 1500000

    asyncio.run(scenario())

def test_options_manage_group_members_persisted_per_member(hass, fake_rf):
    """Wizard abandonne en cours de file : les appartenances deja
    confirmees sont enregistrees, pas re-appairees au retour."""
    async def scenario():
        devices = [
            make_device(name="Groupe", address="1500000", is_group=True),
            make_device(name="Volet 1", address="10000001"),
            make_device(name="Volet 2", address="10000002"),
        ]
        entry = _make_entry(hass, devices)
        flow = _make_options_flow(hass, entry)

        await flow.async_step_manage_group({"device_index": "0"})
        await flow.async_step_manage_group_members({"device_index": ["1", "2"]})
        await flow.async_step_member_memory({"jogged": True})
        await flow.async_step_member_pair_confirm({"paired": True})
        # Abandon ici : le premier membre est confirme, le second attend.
        assert entry.data["devices"][1]["group_addresses"] == ["1500000"]
        assert entry.data["devices"][2].get("group_addresses", []) == []

        # Retour dans le wizard : le membre confirme n'est pas remis
        # en file, seul le second volet reste a apparier.
        flow2 = _make_options_flow(hass, entry)
        await flow2.async_step_manage_group({"device_index": "0"})
        result = await flow2.async_step_manage_group_members(
            {"device_index": ["1", "2"]}
        )
        assert result["step_id"] == "member_memory"
        assert flow2._member_queue == [(2, True)]

    asyncio.run(scenario())


def test_options_manage_group_no_changes(hass, fake_rf):
    """Aucune modification cochee : retour au menu, aucune trame."""
    async def scenario():
        entry = _make_entry(hass, [
            make_device(name="Groupe", address="1500000", is_group=True),
            make_device(
                name="Volet",
                address="10000001",
                group_addresses=["1500000"],
            ),
        ])
        flow = _make_options_flow(hass, entry)

        await flow.async_step_manage_group({"device_index": "0"})
        # Soumission sans changement (membres courants conserves).
        result = await flow.async_step_manage_group_members(
            {"device_index": ["1"]}
        )
        assert result["type"] == "menu"
        assert fake_rf.sent == []
        assert not hass.config_entries.updated

    asyncio.run(scenario())


# =============================================================================
# Suppression d'un groupe
# =============================================================================
def test_options_remove_group_without_groups(hass, fake_rf):
    """Aucun groupe : retour direct au menu."""
    async def scenario():
        entry = _make_entry(hass, [make_device(name="Volet", address="10000001")])
        flow = _make_options_flow(hass, entry)

        result = await flow.async_step_remove_group(None)
        assert result["type"] == "menu"

    asyncio.run(scenario())


def test_options_remove_group_without_unpair(hass, fake_rf):
    """Suppression sans desappairage : groupe retire, aucune trame."""
    async def scenario():
        entry = _make_entry(hass, [
            make_device(name="Volet", address="10000001"),
            make_device(name="Groupe", address="1500000", is_group=True),
        ])
        flow = _make_options_flow(hass, entry)

        result = await flow.async_step_remove_group({"device_index": "1"})
        assert result["step_id"] == "remove_group_confirm"

        result = await flow.async_step_remove_group_confirm(
            {"confirm": True, "unpair": False}
        )
        assert result["type"] == "menu"
        assert fake_rf.sent == []
        devices = entry.data["devices"]
        assert len(devices) == 1
        assert devices[0]["address"] == "10000001"

    asyncio.run(scenario())


def test_options_remove_group_confirm_false_returns(hass, fake_rf):
    """Sans confirmation : retour a la selection, rien ne change."""
    async def scenario():
        entry = _make_entry(hass, [
            make_device(name="Groupe", address="1500000", is_group=True),
        ])
        flow = _make_options_flow(hass, entry)

        await flow.async_step_remove_group({"device_index": "0"})
        result = await flow.async_step_remove_group_confirm(
            {"confirm": False, "unpair": True}
        )
        assert result["step_id"] == "remove_group"
        assert entry.data["devices"][0]["is_group"] is True

    asyncio.run(scenario())


def test_options_remove_group_empty_with_unpair(hass, fake_rf):
    """Groupe sans membre : suppression directe meme avec desappairage."""
    async def scenario():
        entry = _make_entry(hass, [
            make_device(name="Volet", address="10000001"),
            make_device(name="Groupe", address="1500000", is_group=True),
        ])
        flow = _make_options_flow(hass, entry)

        await flow.async_step_remove_group({"device_index": "1"})
        result = await flow.async_step_remove_group_confirm(
            {"confirm": True, "unpair": True}
        )
        assert result["type"] == "menu"
        assert fake_rf.sent == []
        assert len(entry.data["devices"]) == 1

    asyncio.run(scenario())


def test_options_remove_group_unpairs_members_then_deletes(hass, fake_rf):
    """Desappairage guide de chaque membre puis suppression du groupe."""
    async def scenario():
        entry = _make_entry(hass, [
            make_device(name="Groupe", address="1500000", is_group=True),
            make_device(
                name="Volet",
                address="10000001",
                group_addresses=["1500000"],
            ),
        ])
        flow = _make_options_flow(hass, entry)

        await flow.async_step_remove_group({"device_index": "0"})
        result = await flow.async_step_remove_group_confirm(
            {"confirm": True, "unpair": True}
        )

        # Sequence guidee du membre : memoire ouverte puis PROG du groupe.
        assert result["step_id"] == "member_memory"
        assert [(f["address"], f["repeats"]) for f in fake_rf.sent] == [
            (10000001, PROG_MODE_REPEATS),
        ]
        result = await flow.async_step_member_memory({"jogged": True})
        assert result["step_id"] == "member_pair_confirm"
        result = await flow.async_step_member_pair_confirm({"paired": True})

        # Dernier membre confirme : le groupe disparait.
        assert result["type"] == "menu"
        devices = entry.data["devices"]
        assert len(devices) == 1
        assert devices[0]["address"] == "10000001"
        assert devices[0].get("group_addresses", []) == []
        assert [(f["address"], f["repeats"]) for f in fake_rf.sent] == [
            (10000001, PROG_MODE_REPEATS), (1500000, 3),
        ]

    asyncio.run(scenario())


# =============================================================================
# Libelles des types d'appareils localises
# =============================================================================
def test_device_type_labels_english_fallback(hass):
    """Sans config de langue (tests), les libelles sont en anglais."""
    from custom_components.rts_gateway.config_flow import device_type_options

    options = device_type_options(hass)
    labels = {option["label"] for option in options}
    assert "Roller shutter" in labels
    assert "Drapery, opens left" in labels
    # Plus de libelles bilingues codes en dur.
    assert all("(volet" not in label for label in labels)


def test_device_type_labels_french(hass):
    """En francais, les libelles du wizard sont traduits."""
    from types import SimpleNamespace

    from custom_components.rts_gateway.config_flow import (
        device_type_options,
        step_add_group_schema,
    )
    from custom_components.rts_gateway.const import COVER_DEVICE_TYPES

    hass.config = SimpleNamespace(language="fr")
    options = device_type_options(hass)
    labels = {option["label"]: option["value"] for option in options}
    assert labels["Volet roulant"] == "roller_shutter"
    assert labels["Rideau, ouverture à gauche"] == "ldrapery"

    # Le wizard groupe ne propose que des types d'ouvrants, traduits.
    schema = step_add_group_schema(hass)
    field = next(
        validator
        for marker, validator in schema.schema.items()
        if getattr(marker, "schema", None) == "device_type"
    )
    group_labels = {option["label"] for option in field.config["options"]}
    assert "Volet roulant" in group_labels
    assert "Portail" in group_labels
    assert "Prise On/Off" not in group_labels
    assert len(group_labels) == len(COVER_DEVICE_TYPES)
