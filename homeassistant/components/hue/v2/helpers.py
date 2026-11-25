"""Helper functions for Philips Hue v2."""

from collections.abc import Callable
from typing import TYPE_CHECKING

from aiohue.v2 import HueBridgeV2
from aiohue.v2.controllers.events import EventType
from aiohue.v2.models.device import Device, DeviceArchetypes
from aiohue.v2.models.light import Light
from aiohue.v2.models.motion_area_configuration import MotionAreaConfiguration

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.util import color as color_util

from ..const import DOMAIN

if TYPE_CHECKING:
    from ..bridge import HueConfigEntry


def is_smart_plug(device: Device) -> bool:
    """Return whether the device is a smart plug."""
    product = device.product_data
    return product.product_archetype is DeviceArchetypes.PLUG or (
        # The bridge reports the innr SP 222 as a classic bulb.
        product.manufacturer_name == "innr" and product.model_id == "SP 222"
    )


@callback
def async_subscribe_lights(
    config_entry: HueConfigEntry,
    api: HueBridgeV2,
    add_light: Callable[[Light, Device], None],
) -> None:
    """Discover lights when their owning device advertises the light service."""
    lights = api.lights
    pending: dict[str, set[str]] = {}
    registered: set[str] = set()

    @callback
    def async_handle_light(event_type: EventType, light: Light) -> None:
        if event_type == EventType.RESOURCE_DELETED:
            registered.discard(light.id)
            if pending_lights := pending.get(light.owner.rid):
                pending_lights.discard(light.id)
                if not pending_lights:
                    del pending[light.owner.rid]
            return
        if light.id in registered:
            return
        if (device := lights.get_device(light.id)) is None:
            pending.setdefault(light.owner.rid, set()).add(light.id)
            return
        add_light(light, device)
        registered.add(light.id)

    @callback
    def async_handle_device(event_type: EventType, device: Device) -> None:
        pending_lights = pending.pop(device.id, set())
        if event_type == EventType.RESOURCE_DELETED:
            registered.difference_update(device.lights)
            return
        for light_id in pending_lights:
            if (light := lights.get(light_id)) is not None:
                async_handle_light(event_type, light)

    for light in lights:
        async_handle_light(EventType.RESOURCE_ADDED, light)
    config_entry.async_on_unload(
        lights.subscribe(
            async_handle_light,
            event_filter=(EventType.RESOURCE_ADDED, EventType.RESOURCE_DELETED),
        )
    )
    config_entry.async_on_unload(api.devices.subscribe(async_handle_device))


def normalize_hue_brightness(brightness: float | None) -> float | None:
    """Return calculated brightness values."""
    if brightness is not None:
        # Hue uses a range of [0, 100] to control brightness.
        brightness = float((brightness / 255) * 100)

    return brightness


def hue_brightness_to_ha(brightness: float) -> int:
    """Convert a Hue brightness (0-100) to a Home Assistant brightness (0-255)."""
    return round((brightness / 100) * 255)


def normalize_hue_transition(transition: float | None) -> float | None:
    """Return rounded transition values."""
    if transition is not None:
        # hue transition duration is in milliseconds and round them to 100ms
        transition = int(round(transition, 1) * 1000)

    return transition


def normalize_hue_colortemp(
    colortemp_k: int | None, min_mireds: int, max_mireds: int
) -> int | None:
    """Return color temperature within Hue's ranges."""
    if colortemp_k is None:
        return None
    colortemp_mireds = color_util.color_temperature_kelvin_to_mired(colortemp_k)
    # Hue only accepts a range between min_mireds..max_mireds
    return min(max(colortemp_mireds, min_mireds), max_mireds)


def get_motion_area_device_info(
    api: HueBridgeV2, motion_area: MotionAreaConfiguration
) -> DeviceInfo:
    """Return the device the entities of a MotionAware zone belong to.

    Rooms and zones each have a device of their own. A MotionAware zone that
    covers the whole home points at `bridge_home` instead, which has none, so
    its entities are attached to the bridge.
    """
    if (group := api.groups.get(motion_area.group.rid)) is None:
        return DeviceInfo(identifiers={(DOMAIN, api.config.bridge_id)})
    return DeviceInfo(identifiers={(DOMAIN, group.id)})
