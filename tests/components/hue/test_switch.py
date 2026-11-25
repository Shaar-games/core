"""Philips Hue switch platform tests for V2 bridge/api."""

from unittest.mock import Mock, patch

import pytest
from pytest_homeassistant.common import MockConfigEntry

from homeassistant.components.hue import bridge
from homeassistant.components.hue.const import DOMAIN
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import STATE_OFF, STATE_ON, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.setup import async_setup_component
from homeassistant.util.json import JsonArrayType

from .conftest import replace_resources, setup_platform
from .const import (
    FAKE_BEHAVIOR_INSTANCE,
    FAKE_BEHAVIOR_SCRIPT,
    FAKE_BINARY_SENSOR,
    FAKE_BRIDGE,
    FAKE_DEVICE,
    FAKE_LIGHT,
    FAKE_PRESENCE_MIMICKING_INSTANCE,
    FAKE_PRESENCE_MIMICKING_SCRIPT,
    FAKE_ZIGBEE_CONNECTIVITY,
)

TEST_ROOM_ID = "6ddc9066-7e7d-4a03-a773-c73937968296"
BRIDGE_HOME_ID = "a3fbc86a-bf4c-4c69-899d-d6eafc37e288"
MOTION_AREA_CONFIGURATION_ID = "5e6f7a8b-9c1d-4e2f-b3a4-5c6d7e8f9a0b"
SMART_PLUG_DEVICE_ID = "smart_plug_device"
SMART_PLUG_LIGHT_ID = "smart_plug_light"
SMART_PLUG_SWITCH = "switch.hue_smart_plug"
SMART_PLUG_LEGACY_LIGHT = "light.hue_smart_plug"
SMART_PLUG_DEVICE = {
    **FAKE_DEVICE,
    "id": SMART_PLUG_DEVICE_ID,
    "metadata": {"archetype": "classic_bulb", "name": "Hue Smart Plug"},
    "product_data": {
        **FAKE_DEVICE["product_data"],
        "model_id": "LOM010",
        "product_archetype": "plug",
        "product_name": "Hue smart plug",
    },
    "services": [{"rid": SMART_PLUG_LIGHT_ID, "rtype": "light"}],
}
SMART_PLUG_LIGHT = {
    **FAKE_LIGHT,
    "id": SMART_PLUG_LIGHT_ID,
    "owner": {"rid": SMART_PLUG_DEVICE_ID, "rtype": "device"},
}


@pytest.mark.parametrize(
    ("manufacturer", "model_id", "archetype"),
    [
        pytest.param("Signify Netherlands B.V.", "LOM010", "plug", id="hue"),
        pytest.param("innr", "SP 222", "classic_bulb", id="innr"),
    ],
)
async def test_smart_plug(
    hass: HomeAssistant,
    mock_api_v2: Mock,
    mock_config_entry_v2: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    manufacturer: str,
    model_id: str,
    archetype: str,
) -> None:
    """Test automatic outlet registration and switching on the bridge."""
    await mock_api_v2.load_test_data(
        [
            FAKE_DEVICE,
            FAKE_LIGHT,
            {
                **SMART_PLUG_DEVICE,
                "product_data": {
                    **SMART_PLUG_DEVICE["product_data"],
                    "manufacturer_name": manufacturer,
                    "model_id": model_id,
                    "product_archetype": archetype,
                },
            },
            SMART_PLUG_LIGHT,
        ]
    )
    mock_config_entry_v2.add_to_hass(hass)

    with patch.object(bridge, "HueBridgeV2", return_value=mock_api_v2):
        assert await async_setup_component(hass, DOMAIN, {})
        await hass.async_block_till_done()

    assert entity_registry.async_get(SMART_PLUG_LEGACY_LIGHT) is None
    assert hass.states.get(SMART_PLUG_LEGACY_LIGHT) is None
    assert hass.states.is_state(SMART_PLUG_SWITCH, STATE_OFF)
    assert hass.states.get(SMART_PLUG_SWITCH).attributes["device_class"] == "outlet"
    assert hass.states.is_state("light.hue_mocked_device", STATE_OFF)
    assert not mock_api_v2.mock_requests

    for service, on, state in (
        ("turn_on", True, STATE_ON),
        ("turn_off", False, STATE_OFF),
    ):
        await hass.services.async_call(
            SWITCH_DOMAIN, service, {"entity_id": SMART_PLUG_SWITCH}, blocking=True
        )
        assert mock_api_v2.mock_requests.pop() == {
            "method": "put",
            "path": f"clip/v2/resource/light/{SMART_PLUG_LIGHT_ID}",
            "json": {"on": {"on": on}},
        }
        mock_api_v2.emit_event(
            "update", {"id": SMART_PLUG_LIGHT_ID, "type": "light", "on": {"on": on}}
        )
        await hass.async_block_till_done()
        assert hass.states.is_state(SMART_PLUG_SWITCH, state)


@pytest.mark.parametrize(
    ("disabled_by", "legacy_name", "expected_state", "expected_name"),
    [
        pytest.param(
            None, "Computer outlet", STATE_OFF, "Computer outlet", id="enabled"
        ),
        pytest.param(None, None, STATE_OFF, "Hue Smart Plug", id="unnamed_with_area"),
        pytest.param(
            er.RegistryEntryDisabler.USER,
            "Computer outlet",
            None,
            "Computer outlet",
            id="disabled",
        ),
    ],
)
async def test_smart_plug_migration(
    hass: HomeAssistant,
    mock_api_v2: Mock,
    mock_config_entry_v2: MockConfigEntry,
    area_registry: ar.AreaRegistry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
    issue_registry: ir.IssueRegistry,
    disabled_by: er.RegistryEntryDisabler | None,
    legacy_name: str | None,
    expected_state: str | None,
    expected_name: str,
) -> None:
    """Move user settings from the previous light entity to the outlet."""
    await mock_api_v2.load_test_data([SMART_PLUG_DEVICE, SMART_PLUG_LIGHT])
    mock_config_entry_v2.add_to_hass(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=mock_config_entry_v2.entry_id,
        identifiers={(DOMAIN, SMART_PLUG_DEVICE_ID)},
    )
    area = area_registry.async_create("Office")
    legacy = entity_registry.async_get_or_create(
        Platform.LIGHT,
        DOMAIN,
        SMART_PLUG_LIGHT_ID,
        config_entry=mock_config_entry_v2,
        device_id=device.id,
        suggested_object_id="computer_outlet",
        disabled_by=disabled_by,
        hidden_by=er.RegistryEntryHider.USER,
        has_entity_name=True,
        original_name="Legacy light",
    )
    entity_registry.async_update_entity(
        legacy.entity_id, name=legacy_name, area_id=area.id, icon="mdi:power-plug"
    )
    entity_registry.async_get_or_create(
        Platform.LIGHT,
        DOMAIN,
        SMART_PLUG_LIGHT_ID,
        config_entry=mock_config_entry_v2,
        has_entity_name=True,
        original_name=None,
    )
    entity_registry.async_update_entity_options(
        legacy.entity_id, "conversation", {"should_expose": False}
    )

    with patch.object(bridge, "HueBridgeV2", return_value=mock_api_v2):
        assert await async_setup_component(hass, DOMAIN, {})
        await hass.async_block_till_done()

    assert getattr(hass.states.get(legacy.entity_id), "state", None) == expected_state
    issue_id = f"deprecated_smart_plug_light_{mock_config_entry_v2.entry_id}_{SMART_PLUG_LIGHT_ID}"
    assert bool(issue_registry.async_get_issue(DOMAIN, issue_id)) == (
        expected_state is not None
    )
    replacement = entity_registry.async_get("switch.computer_outlet")
    assert replacement is not None
    assert replacement.device_id == device.id
    assert replacement.name == expected_name
    assert replacement.area_id == area.id
    assert replacement.icon == "mdi:power-plug"
    assert replacement.disabled_by == disabled_by
    assert replacement.hidden_by is er.RegistryEntryHider.USER
    assert replacement.options["conversation"] == {"should_expose": False}
    assert (
        getattr(hass.states.get(replacement.entity_id), "state", None) == expected_state
    )
    assert not mock_api_v2.mock_requests


async def test_smart_plug_repair_follows_entity_changes(
    hass: HomeAssistant,
    mock_api_v2: Mock,
    mock_config_entry_v2: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Update the repair after a rename and clear it after disabling the light."""
    await mock_api_v2.load_test_data([SMART_PLUG_DEVICE, SMART_PLUG_LIGHT])
    mock_config_entry_v2.add_to_hass(hass)
    legacy = entity_registry.async_get_or_create(
        Platform.LIGHT,
        DOMAIN,
        SMART_PLUG_LIGHT_ID,
        config_entry=mock_config_entry_v2,
        suggested_object_id="hue_smart_plug",
    )
    with patch.object(bridge, "HueBridgeV2", return_value=mock_api_v2):
        assert await async_setup_component(hass, DOMAIN, {})
        await hass.async_block_till_done()

    issue_id = f"deprecated_smart_plug_light_{mock_config_entry_v2.entry_id}_{SMART_PLUG_LIGHT_ID}"
    issue = issue_registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.breaks_in_ha_version == "2027.7.0"
    assert issue.translation_placeholders == {
        "light": legacy.entity_id,
        "switch": SMART_PLUG_SWITCH,
    }

    entity_registry.async_update_entity(
        legacy.entity_id, new_entity_id="light.old_outlet"
    )
    await hass.async_block_till_done()
    assert (
        issue_registry.async_get_issue(DOMAIN, issue_id).translation_placeholders[
            "light"
        ]
        == "light.old_outlet"
    )

    entity_registry.async_update_entity(
        "light.old_outlet", disabled_by=er.RegistryEntryDisabler.USER
    )
    await hass.async_block_till_done()
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None
    assert hass.states.get("light.old_outlet") is None
    assert hass.states.is_state(SMART_PLUG_SWITCH, STATE_OFF)
    assert not mock_api_v2.mock_requests


async def test_existing_smart_plug_switch_respects_legacy_preferences(
    hass: HomeAssistant,
    mock_api_v2: Mock,
    mock_config_entry_v2: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Preserve restrictions saved on a light when its switch already exists."""
    await mock_api_v2.load_test_data([SMART_PLUG_DEVICE, SMART_PLUG_LIGHT])
    mock_config_entry_v2.add_to_hass(hass)
    legacy = entity_registry.async_get_or_create(
        Platform.LIGHT,
        DOMAIN,
        SMART_PLUG_LIGHT_ID,
        config_entry=mock_config_entry_v2,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    replacement = entity_registry.async_get_or_create(
        Platform.SWITCH,
        DOMAIN,
        SMART_PLUG_LIGHT_ID,
        config_entry=mock_config_entry_v2,
    )
    entity_registry.async_update_entity_options(
        legacy.entity_id, "conversation", {"should_expose": False}
    )
    entity_registry.async_update_entity_options(
        replacement.entity_id, "conversation", {"should_expose": True}
    )

    with patch.object(bridge, "HueBridgeV2", return_value=mock_api_v2):
        assert await async_setup_component(hass, DOMAIN, {})
        await hass.async_block_till_done()

    switch = entity_registry.async_get(replacement.entity_id)
    assert switch is not None
    assert switch.disabled_by is er.RegistryEntryDisabler.USER
    assert switch.options["conversation"]["should_expose"] is False
    assert hass.states.get(replacement.entity_id) is None
    assert not mock_api_v2.mock_requests


async def test_smart_plug_light_arrives_before_device(
    hass: HomeAssistant,
    mock_api_v2: Mock,
    mock_config_entry_v2: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Wait for the plug device to advertise its light before creating the switch."""
    await mock_api_v2.load_test_data([])
    mock_config_entry_v2.add_to_hass(hass)
    with patch.object(bridge, "HueBridgeV2", return_value=mock_api_v2):
        assert await async_setup_component(hass, DOMAIN, {})
        await hass.async_block_till_done()

    mock_api_v2.emit_event("add", SMART_PLUG_LIGHT)
    await hass.async_block_till_done()
    assert hass.states.get(SMART_PLUG_SWITCH) is None
    assert hass.states.get(SMART_PLUG_LEGACY_LIGHT) is None

    mock_api_v2.emit_event("add", {**SMART_PLUG_DEVICE, "services": []})
    await hass.async_block_till_done()
    assert hass.states.get(SMART_PLUG_SWITCH) is None

    mock_api_v2.emit_event("update", SMART_PLUG_DEVICE)
    await hass.async_block_till_done()
    assert hass.states.is_state(SMART_PLUG_SWITCH, STATE_OFF)
    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, SMART_PLUG_DEVICE_ID), mock_config_entry_v2.entry_id
    )
    assert device is not None
    assert entity_registry.async_get(SMART_PLUG_SWITCH).device_id == device.id

    mock_api_v2.emit_event("add", SMART_PLUG_LIGHT)
    mock_api_v2.emit_event(
        "update", {"id": SMART_PLUG_LIGHT_ID, "type": "light", "on": {"on": True}}
    )
    await hass.async_block_till_done()
    assert hass.states.is_state(SMART_PLUG_SWITCH, STATE_ON)


async def test_hue_room_reports_smart_plug_switch(
    hass: HomeAssistant,
    mock_api_v2: Mock,
    mock_config_entry_v2: MockConfigEntry,
    v2_resources_test_data: JsonArrayType,
    entity_registry: er.EntityRegistry,
) -> None:
    """Report the switch as the member of the Hue room, even on initial setup."""
    room = next(
        resource
        for resource in v2_resources_test_data
        if resource["id"] == TEST_ROOM_ID
    )
    grouped_light_id = room["grouped_services"][0]["rid"]
    grouped_light = next(
        resource
        for resource in v2_resources_test_data
        if resource["id"] == grouped_light_id
    )
    await mock_api_v2.load_test_data(
        [
            SMART_PLUG_DEVICE,
            SMART_PLUG_LIGHT,
            {**grouped_light, "owner": {"rid": TEST_ROOM_ID, "rtype": "room"}},
            {
                **room,
                "children": [{"rid": SMART_PLUG_DEVICE_ID, "rtype": "device"}],
                "services": [
                    {"rid": SMART_PLUG_LIGHT_ID, "rtype": "light"},
                    {"rid": grouped_light_id, "rtype": "grouped_light"},
                ],
            },
        ]
    )
    mock_config_entry_v2.add_to_hass(hass)
    with patch.object(bridge, "HueBridgeV2", return_value=mock_api_v2):
        assert await async_setup_component(hass, DOMAIN, {})
        await hass.async_block_till_done()

    switch_id = entity_registry.async_get_entity_id(
        Platform.SWITCH, DOMAIN, SMART_PLUG_LIGHT_ID
    )
    room_id = entity_registry.async_get_entity_id(
        Platform.LIGHT, DOMAIN, grouped_light_id
    )
    assert switch_id is not None
    assert room_id is not None
    assert hass.states.get(room_id).attributes["entity_id"] == {switch_id}

    entity_registry.async_update_entity(switch_id, new_entity_id="switch.pc")
    await hass.async_block_till_done()
    assert hass.states.get(room_id).attributes["entity_id"] == {"switch.pc"}


async def test_switch(
    hass: HomeAssistant, mock_bridge_v2: Mock, v2_resources_test_data: JsonArrayType
) -> None:
    """Test if (config) switches get created."""
    await mock_bridge_v2.api.load_test_data(v2_resources_test_data)

    await setup_platform(hass, mock_bridge_v2, Platform.SWITCH)
    # there shouldn't have been any requests at this point
    assert len(mock_bridge_v2.mock_requests) == 0
    # 5 entities should be created from test data
    assert len(hass.states.async_all()) == 5

    # test config switch to enable/disable motion sensor
    test_entity = hass.states.get("switch.hue_motion_sensor_motion_sensor_enabled")
    assert test_entity is not None
    assert test_entity.name == "Hue motion sensor Motion sensor enabled"
    assert test_entity.state == "on"
    assert test_entity.attributes["device_class"] == "switch"

    # test config switch to enable/disable a behavior_instance resource (=builtin
    # automation)
    test_entity = hass.states.get("switch.philips_hue_automation_timer_test")
    assert test_entity is not None
    assert test_entity.name == "Philips hue Automation: Timer Test"
    assert test_entity.state == "on"
    assert test_entity.attributes["device_class"] == "switch"

    # test config switch to enable/disable a MotionAware zone
    test_entity = hass.states.get("switch.test_room_test_room_motionaware")
    assert test_entity is not None
    assert test_entity.name == "Test Room MotionAware"
    assert test_entity.state == "on"
    assert test_entity.attributes["device_class"] == "switch"


@pytest.mark.parametrize(
    ("group", "device_identifier"),
    [
        pytest.param(
            {"rid": TEST_ROOM_ID, "rtype": "room"},
            (DOMAIN, TEST_ROOM_ID),
            id="room",
        ),
        pytest.param(
            {"rid": BRIDGE_HOME_ID, "rtype": "bridge_home"},
            (DOMAIN, FAKE_BRIDGE["bridge_id"]),
            id="whole_home",
        ),
    ],
)
async def test_motionaware_switch_device(
    hass: HomeAssistant,
    mock_bridge_v2: Mock,
    v2_resources_test_data: JsonArrayType,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
    group: dict[str, str],
    device_identifier: tuple[str, str],
) -> None:
    """Test the MotionAware switch is attached to its room or zone, or to the bridge.

    A MotionAware zone that covers the whole home points at `bridge_home`, which
    has no device of its own.
    """
    motion_area_configuration = next(
        resource
        for resource in v2_resources_test_data
        if resource["id"] == MOTION_AREA_CONFIGURATION_ID
    )
    await mock_bridge_v2.api.load_test_data(
        replace_resources(
            v2_resources_test_data, [{**motion_area_configuration, "group": group}]
        )
    )
    await setup_platform(hass, mock_bridge_v2, Platform.SWITCH)

    entity_id = entity_registry.async_get_entity_id(
        Platform.SWITCH, DOMAIN, MOTION_AREA_CONFIGURATION_ID
    )
    assert entity_id is not None
    assert hass.states.get(entity_id).state == "on"

    device = device_registry.async_get_device_by_identifier(
        device_identifier, mock_bridge_v2.config_entry.entry_id
    )
    assert device is not None
    assert entity_registry.async_get(entity_id).device_id == device.id


async def test_switch_turn_on_service(
    hass: HomeAssistant, mock_bridge_v2: Mock, v2_resources_test_data: JsonArrayType
) -> None:
    """Test calling the turn on service on a switch."""
    await mock_bridge_v2.api.load_test_data(v2_resources_test_data)

    await setup_platform(hass, mock_bridge_v2, Platform.SWITCH)

    test_entity_id = "switch.hue_motion_sensor_motion_sensor_enabled"

    # call the HA turn_on service
    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": test_entity_id},
        blocking=True,
    )

    # PUT request should have been sent to device with correct params
    assert len(mock_bridge_v2.mock_requests) == 1
    assert mock_bridge_v2.mock_requests[0]["method"] == "put"
    assert mock_bridge_v2.mock_requests[0]["json"]["enabled"] is True


async def test_switch_turn_off_service(
    hass: HomeAssistant, mock_bridge_v2: Mock, v2_resources_test_data: JsonArrayType
) -> None:
    """Test calling the turn off service on a switch."""
    await mock_bridge_v2.api.load_test_data(v2_resources_test_data)

    await setup_platform(hass, mock_bridge_v2, Platform.SWITCH)

    test_entity_id = "switch.hue_motion_sensor_motion_sensor_enabled"

    # verify the switch is on before we start
    assert hass.states.get(test_entity_id).state == "on"

    # now call the HA turn_off service
    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": test_entity_id},
        blocking=True,
    )

    # PUT request should have been sent to device with correct params
    assert len(mock_bridge_v2.mock_requests) == 1
    assert mock_bridge_v2.mock_requests[0]["method"] == "put"
    assert mock_bridge_v2.mock_requests[0]["json"]["enabled"] is False

    # Now generate update event by emitting the json we've sent as incoming event
    event = {
        "id": "b6896534-016d-4052-8cb4-ef04454df62c",
        "type": "motion",
        **mock_bridge_v2.mock_requests[0]["json"],
    }
    mock_bridge_v2.api.emit_event("update", event)
    await hass.async_block_till_done()

    # the switch should now be off
    test_entity = hass.states.get(test_entity_id)
    assert test_entity is not None
    assert test_entity.state == "off"


async def test_motionaware_switch_turn_on_off_service(
    hass: HomeAssistant, mock_bridge_v2: Mock, v2_resources_test_data: JsonArrayType
) -> None:
    """Test enabling/disabling a MotionAware zone."""
    await mock_bridge_v2.api.load_test_data(v2_resources_test_data)

    await setup_platform(hass, mock_bridge_v2, Platform.SWITCH)

    test_entity_id = "switch.test_room_test_room_motionaware"

    # verify the switch is on before we start
    assert hass.states.get(test_entity_id).state == "on"

    # call the HA turn_off service
    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": test_entity_id},
        blocking=True,
    )

    # PUT request should have been sent to the motion_area_configuration resource
    assert len(mock_bridge_v2.mock_requests) == 1
    assert mock_bridge_v2.mock_requests[0]["method"] == "put"
    assert (
        mock_bridge_v2.mock_requests[0]["path"]
        == f"clip/v2/resource/motion_area_configuration/{MOTION_AREA_CONFIGURATION_ID}"
    )
    assert mock_bridge_v2.mock_requests[0]["json"]["enabled"] is False

    # Now generate update event by emitting the json we've sent as incoming event
    event = {
        "id": MOTION_AREA_CONFIGURATION_ID,
        "type": "motion_area_configuration",
        **mock_bridge_v2.mock_requests[0]["json"],
    }
    mock_bridge_v2.api.emit_event("update", event)
    await hass.async_block_till_done()

    # the switch should now be off
    assert hass.states.get(test_entity_id).state == "off"

    # call the HA turn_on service
    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": test_entity_id},
        blocking=True,
    )

    assert len(mock_bridge_v2.mock_requests) == 2
    assert mock_bridge_v2.mock_requests[1]["method"] == "put"
    assert mock_bridge_v2.mock_requests[1]["json"]["enabled"] is True


async def test_switch_added(hass: HomeAssistant, mock_bridge_v2: Mock) -> None:
    """Test new switch added to bridge."""
    await mock_bridge_v2.api.load_test_data([FAKE_DEVICE, FAKE_ZIGBEE_CONNECTIVITY])

    await setup_platform(hass, mock_bridge_v2, Platform.SWITCH)

    test_entity_id = "switch.hue_mocked_device_motion_sensor_enabled"

    # verify entity does not exist before we start
    assert hass.states.get(test_entity_id) is None

    # Add new fake entity (and attached device and zigbee_connectivity) by emitting
    # events
    mock_bridge_v2.api.emit_event("add", FAKE_BINARY_SENSOR)
    await hass.async_block_till_done()

    # the entity should now be available
    test_entity = hass.states.get(test_entity_id)
    assert test_entity is not None
    assert test_entity.state == "on"

    # test update
    updated_resource = {**FAKE_BINARY_SENSOR, "enabled": False}
    mock_bridge_v2.api.emit_event("update", updated_resource)
    await hass.async_block_till_done()
    test_entity = hass.states.get(test_entity_id)
    assert test_entity is not None
    assert test_entity.state == "off"


@pytest.mark.parametrize(
    "metadata",
    [
        pytest.param(
            {"name": "Hue Accessories", "category": "accessory"}, id="accessory"
        ),
        pytest.param(
            {"name": "Light state after streaming", "category": "entertainment"},
            id="entertainment",
        ),
        pytest.param({"name": "Old bridge script"}, id="no_category"),
    ],
)
async def test_internal_behavior_instance_not_added(
    hass: HomeAssistant,
    mock_bridge_v2: Mock,
    v2_resources_test_data: JsonArrayType,
    metadata: dict,
) -> None:
    """Test internal behavior instances are not exposed as switches.

    The bridge accepts a change to `enabled` on these but keeps running them,
    so a switch for them would silently do nothing. Bridges that do not report
    a category at all are skipped for the same reason.
    """
    internal_script = {**FAKE_BEHAVIOR_SCRIPT, "metadata": metadata}
    await mock_bridge_v2.api.load_test_data(
        [*v2_resources_test_data, internal_script, FAKE_BEHAVIOR_INSTANCE]
    )

    await setup_platform(hass, mock_bridge_v2, Platform.SWITCH)

    assert hass.states.get("switch.philips_hue_automation_wall_switch_hallway") is None
    assert hass.states.get("switch.philips_hue_automation_timer_test") is not None
    assert len(hass.states.async_all()) == 5


async def test_internal_behavior_instance_entity_removed(
    hass: HomeAssistant,
    mock_bridge_v2: Mock,
    v2_resources_test_data: JsonArrayType,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test a previously created entity for an internal instance is removed."""
    # Simulate an entity created with a previous version of the integration
    stale_entity = entity_registry.async_get_or_create(
        Platform.SWITCH, DOMAIN, FAKE_BEHAVIOR_INSTANCE["id"]
    )
    await mock_bridge_v2.api.load_test_data(
        [*v2_resources_test_data, FAKE_BEHAVIOR_SCRIPT, FAKE_BEHAVIOR_INSTANCE]
    )

    await setup_platform(hass, mock_bridge_v2, Platform.SWITCH)

    assert entity_registry.async_get(stale_entity.entity_id) is None


@pytest.mark.parametrize(
    ("pm_state", "expected_state"), [("started", "on"), ("stopped", "off")]
)
async def test_presence_mimicking_switch_state(
    hass: HomeAssistant,
    mock_bridge_v2: Mock,
    v2_resources_test_data: JsonArrayType,
    pm_state: str,
    expected_state: str,
) -> None:
    """Test the switch follows the run state instead of enabled."""
    instance = {**FAKE_PRESENCE_MIMICKING_INSTANCE, "state": {"pm_state": pm_state}}
    await mock_bridge_v2.api.load_test_data(
        [*v2_resources_test_data, FAKE_PRESENCE_MIMICKING_SCRIPT, instance]
    )

    await setup_platform(hass, mock_bridge_v2, Platform.SWITCH)

    test_entity = hass.states.get("switch.philips_hue_automation_mimic_presence")
    assert test_entity is not None
    assert test_entity.state == expected_state


@pytest.mark.parametrize(
    ("service", "expected_trigger"),
    [("turn_on", {"start": {}}), ("turn_off", {"stop": {}})],
)
async def test_presence_mimicking_switch_services(
    hass: HomeAssistant,
    mock_bridge_v2: Mock,
    v2_resources_test_data: JsonArrayType,
    service: str,
    expected_trigger: dict,
) -> None:
    """Test the switch starts and stops instead of touching enabled."""
    await mock_bridge_v2.api.load_test_data(
        [
            *v2_resources_test_data,
            FAKE_PRESENCE_MIMICKING_SCRIPT,
            FAKE_PRESENCE_MIMICKING_INSTANCE,
        ]
    )

    await setup_platform(hass, mock_bridge_v2, Platform.SWITCH)

    await hass.services.async_call(
        "switch",
        service,
        {"entity_id": "switch.philips_hue_automation_mimic_presence"},
        blocking=True,
    )

    assert len(mock_bridge_v2.mock_requests) == 1
    assert mock_bridge_v2.mock_requests[0]["method"] == "put"
    assert mock_bridge_v2.mock_requests[0]["json"] == {"trigger": expected_trigger}


async def test_regular_automation_switch_uses_enabled(
    hass: HomeAssistant, mock_bridge_v2: Mock, v2_resources_test_data: JsonArrayType
) -> None:
    """Test an automation without a run state still toggles enabled."""
    await mock_bridge_v2.api.load_test_data(v2_resources_test_data)

    await setup_platform(hass, mock_bridge_v2, Platform.SWITCH)

    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": "switch.philips_hue_automation_timer_test"},
        blocking=True,
    )

    assert len(mock_bridge_v2.mock_requests) == 1
    assert mock_bridge_v2.mock_requests[0]["json"] == {"enabled": False}
