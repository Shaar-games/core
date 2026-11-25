"""Support for switch platform for Hue resources (V2 only)."""

from collections.abc import Callable
from typing import Any, override

from aiohue.v2 import HueBridgeV2
from aiohue.v2.controllers.config import (
    BehaviorInstance,
    BehaviorInstanceController,
    MotionAreaConfiguration,
    MotionAreaConfigurationController,
)
from aiohue.v2.controllers.events import EventType
from aiohue.v2.controllers.lights import LightsController
from aiohue.v2.controllers.sensors import (
    LightLevel,
    LightLevelController,
    Motion,
    MotionController,
)
from aiohue.v2.models.behavior_instance import PresenceMimickingState
from aiohue.v2.models.behavior_script import BehaviorScriptCategory
from aiohue.v2.models.device import Device
from aiohue.v2.models.light import Light

from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .bridge import HueBridge, HueConfigEntry
from .const import CONF_SMART_PLUGS_AS_SWITCHES, DOMAIN
from .v2.entity import HueBaseEntity
from .v2.helpers import get_motion_area_device_info, is_configured_as_switch


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: HueConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Hue switch platform from Hue resources."""
    bridge = config_entry.runtime_data
    api: HueBridgeV2 = bridge.api

    if bridge.api_version == 1:
        # should not happen, but just in case
        raise NotImplementedError("Switch support is only available for V2 bridges")

    @callback
    def register_items(
        controller: BehaviorInstanceController
        | LightLevelController
        | MotionAreaConfigurationController
        | MotionController,
        switch_class: type[
            HueBehaviorInstanceEnabledEntity
            | HueLightSensorEnabledEntity
            | HueMotionAreaConfigurationEnabledEntity
            | HueMotionSensorEnabledEntity
        ],
        resource_filter: Callable[[Any], bool] | None = None,
    ):
        @callback
        def async_add_entity(
            event_type: EventType,
            resource: BehaviorInstance | LightLevel | MotionAreaConfiguration | Motion,
        ) -> None:
            """Add entity from Hue resource."""
            if resource_filter is not None and not resource_filter(resource):
                return
            async_add_entities([switch_class(bridge, controller, resource)])

        # add all current items in controller
        for item in controller:
            async_add_entity(EventType.RESOURCE_ADDED, item)

        # register listener for new items only
        config_entry.async_on_unload(
            controller.subscribe(
                async_add_entity, event_filter=EventType.RESOURCE_ADDED
            )
        )

    @callback
    def is_user_automation(resource: BehaviorInstance) -> bool:
        """Return if the behavior instance is an automation from the Hue app.

        Anything else is device configuration, which the bridge keeps running
        even after it accepts switching it off. Categories we do not recognise
        are skipped too, better no switch than one that does nothing.
        """
        script = api.config.behavior_script.get(resource.script_id)
        return (
            script is not None
            and script.metadata.category is BehaviorScriptCategory.AUTOMATION
        )

    # clean up entities previously created for internal behavior instances
    entity_registry = er.async_get(hass)
    for resource in api.config.behavior_instance:
        if is_user_automation(resource):
            continue
        if entity_id := entity_registry.async_get_entity_id(
            Platform.SWITCH, DOMAIN, resource.id
        ):
            entity_registry.async_remove(entity_id)

    # setup for each switch-type hue resource
    register_items(api.sensors.motion, HueMotionSensorEnabledEntity)
    register_items(api.sensors.light_level, HueLightSensorEnabledEntity)
    register_items(
        api.config.behavior_instance,
        HueBehaviorInstanceEnabledEntity,
        is_user_automation,
    )
    register_items(
        api.config.motion_area_configuration, HueMotionAreaConfigurationEnabledEntity
    )
    if config_entry.options.get(CONF_SMART_PLUGS_AS_SWITCHES):
        _async_register_smart_plugs(bridge, api, async_add_entities)


@callback
def _async_register_smart_plugs(
    bridge: HueBridge,
    api: HueBridgeV2,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Register opted-in plug resources as switches."""
    config_entry = bridge.config_entry
    lights = api.lights
    pending: dict[str, set[str]] = {}

    @callback
    def async_add_plug(event_type: EventType, light: Light) -> None:
        if not is_configured_as_switch(light, config_entry):
            return
        if lights.get_device(light.id) is None:
            pending.setdefault(light.owner.rid, set()).add(light.id)
            return
        async_add_entities([HueSmartPlug(bridge, lights, light)])

    @callback
    def async_retry_plugs(event_type: EventType, device: Device) -> None:
        for light_id in pending.pop(device.id, set()):
            if (light := lights.get(light_id)) is not None:
                async_add_plug(event_type, light)

    for light in lights:
        async_add_plug(EventType.RESOURCE_ADDED, light)
    config_entry.async_on_unload(
        lights.subscribe(async_add_plug, event_filter=EventType.RESOURCE_ADDED)
    )
    config_entry.async_on_unload(
        api.devices.subscribe(
            async_retry_plugs,
            event_filter=(EventType.RESOURCE_ADDED, EventType.RESOURCE_UPDATED),
        )
    )


class HueSmartPlug(HueBaseEntity, SwitchEntity):
    """Represent a Hue smart plug configured as an outlet switch."""

    controller: LightsController
    resource: Light
    _attr_device_class = SwitchDeviceClass.OUTLET
    _attr_has_entity_name = True
    _attr_name = None

    @property
    @override
    def is_on(self) -> bool:
        """Return whether the plug is on."""
        return self.resource.on.on

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the plug on."""
        await self.bridge.async_request_call(
            self.controller.set_state, self.resource.id, on=True
        )

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the plug off."""
        await self.bridge.async_request_call(
            self.controller.set_state, self.resource.id, on=False
        )


class HueResourceEnabledEntity(HueBaseEntity, SwitchEntity):
    """Represent a Switch entity from a Hue resource that toggles."""

    controller: (
        BehaviorInstanceController
        | LightLevelController
        | MotionAreaConfigurationController
        | MotionController
    )
    resource: BehaviorInstance | LightLevel | MotionAreaConfiguration | Motion

    entity_description = SwitchEntityDescription(
        key="sensing_service_enabled",
        device_class=SwitchDeviceClass.SWITCH,
        entity_category=EntityCategory.CONFIG,
        has_entity_name=True,
    )

    @property
    @override
    def is_on(self) -> bool:
        """Return true if the switch is on."""
        return self.resource.enabled

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        await self.bridge.async_request_call(
            self.controller.set_enabled, self.resource.id, enabled=True
        )

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        await self.bridge.async_request_call(
            self.controller.set_enabled, self.resource.id, enabled=False
        )


class HueBehaviorInstanceEnabledEntity(HueResourceEnabledEntity):
    """Representation of a Switch entity to enable/disable a Hue Behavior Instance.

    Automations that the Hue app runs with a play button, such as mimic
    presence, are started and stopped. All others are enabled and disabled.
    """

    controller: BehaviorInstanceController
    resource: BehaviorInstance

    entity_description = SwitchEntityDescription(
        key="behavior_instance",
        device_class=SwitchDeviceClass.SWITCH,
        entity_category=EntityCategory.CONFIG,
    )

    @property
    @override
    def name(self) -> str:
        """Return name for this entity."""
        return f"Automation: {self.resource.metadata.name}"

    @property
    @override
    def is_on(self) -> bool:
        """Return true if the switch is on."""
        if (run_state := self.resource.presence_mimicking_state) is not None:
            return run_state is PresenceMimickingState.STARTED
        return self.resource.enabled

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        if self.resource.presence_mimicking_state is None:
            await super().async_turn_on(**kwargs)
            return
        await self.bridge.async_request_call(self.controller.start, self.resource.id)

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
        if self.resource.presence_mimicking_state is None:
            await super().async_turn_off(**kwargs)
            return
        await self.bridge.async_request_call(self.controller.stop, self.resource.id)


class HueMotionAreaConfigurationEnabledEntity(HueResourceEnabledEntity):
    """Representation of a Switch entity to enable/disable a Hue MotionAware zone."""

    controller: MotionAreaConfigurationController
    resource: MotionAreaConfiguration

    entity_description = SwitchEntityDescription(
        key="motion_area_configuration",
        device_class=SwitchDeviceClass.SWITCH,
        entity_category=EntityCategory.CONFIG,
        has_entity_name=True,
        translation_key="motion_aware",
    )

    def __init__(
        self,
        bridge: HueBridge,
        controller: MotionAreaConfigurationController,
        resource: MotionAreaConfiguration,
    ) -> None:
        """Initialize the switch."""
        super().__init__(bridge, controller, resource)
        # link the switch to the room or zone the MotionAware zone is associated with
        self._attr_device_info = get_motion_area_device_info(bridge.api, resource)


class HueMotionSensorEnabledEntity(HueResourceEnabledEntity):
    """Representation of a Switch entity to enable/disable a Hue motion sensor."""

    entity_description = SwitchEntityDescription(
        key="motion_sensor_enabled",
        device_class=SwitchDeviceClass.SWITCH,
        entity_category=EntityCategory.CONFIG,
        has_entity_name=True,
        translation_key="motion_sensor_enabled",
    )


class HueLightSensorEnabledEntity(HueResourceEnabledEntity):
    """Representation of a Switch entity to enable/disable a Hue light sensor."""

    entity_description = SwitchEntityDescription(
        key="light_sensor_enabled",
        device_class=SwitchDeviceClass.SWITCH,
        entity_category=EntityCategory.CONFIG,
        has_entity_name=True,
        translation_key="light_sensor_enabled",
    )
