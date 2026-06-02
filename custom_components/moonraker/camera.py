"""Support for Moonraker camera."""

from __future__ import annotations

import logging

from homeassistant.components.camera import Camera
from homeassistant.components.mjpeg.camera import MjpegCamera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_TLS,
    CONF_URL,
    CONF_OPTION_CAMERA_STREAM,
    CONF_OPTION_CAMERA_SNAPSHOT,
    CONF_OPTION_CAMERA_PORT,
    CONF_OPTION_THUMBNAIL_PORT,
    DOMAIN,
    METHODS,
    PRINTSTATES,
)

_LOGGER = logging.getLogger(__name__)
DEFAULT_PORT = 80

hardcoded_camera = {
    "name": "webcam",
    "location": "printer",
    "service": "mjpegstreamer-adaptive",
    "target_fps": "15",
    "stream_url": "/webcam/?action=stream",
    "snapshot_url": "/webcam/?action=snapshot",
    "flip_horizontal": False,
    "flip_vertical": False,
    "rotation": 0,
    "source": "database",
}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up available Moonraker cameras."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    camera_cnt = 0
    try:
        if (stream := config_entry.options.get(CONF_OPTION_CAMERA_STREAM)) and stream != "":
            hardcoded_camera["stream_url"] = stream
            hardcoded_camera["snapshot_url"] = config_entry.options.get(CONF_OPTION_CAMERA_SNAPSHOT)
            async_add_entities([MoonrakerCamera(config_entry, coordinator, hardcoded_camera, 100)])
            camera_cnt += 1
        else:
            cameras = await coordinator.async_fetch_data(METHODS.SERVER_WEBCAMS_LIST)
            for camera_id, camera in enumerate(cameras.get("webcams", [])):
                async_add_entities([MoonrakerCamera(config_entry, coordinator, camera, camera_id)])
                camera_cnt += 1
    except Exception as exc:
        _LOGGER.debug("Could not add any cameras from the API list: %s", exc)

    if camera_cnt == 0:
        _LOGGER.info("No Camera in the list, trying hardcoded")
        async_add_entities([MoonrakerCamera(config_entry, coordinator, hardcoded_camera, 0)])

    async_add_entities(
        [PreviewCamera(config_entry, coordinator, async_get_clientsession(hass, verify_ssl=False))]
    )


class MoonrakerCamera(MjpegCamera):
    """Representation of a Moonraker Camera Stream."""

    def __init__(self, config_entry, coordinator, camera, camera_id) -> None:
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, config_entry.entry_id)})
        self.port = config_entry.options.get(CONF_OPTION_CAMERA_PORT) or DEFAULT_PORT
        scheme = "https" if config_entry.data.get(CONF_TLS) else "http"

        if camera["stream_url"].startswith(("http://", "https://")):
            base_url = ""
        else:
            base_url = f"{scheme}://{config_entry.data.get(CONF_URL)}:{self.port}"

        _LOGGER.info("Connecting to camera: %s%s", base_url, camera["stream_url"])

        super().__init__(
            device_info=self._attr_device_info,
            mjpeg_url=f"{base_url}{camera['stream_url']}",
            name=f"{coordinator.api_device_name} {camera['name']}",
            still_image_url=f"{base_url}{camera['snapshot_url']}",
            unique_id=f"{config_entry.entry_id}_{camera['name']}_{camera_id}",
        )


class PreviewCamera(Camera):
    """Representation of the gcode thumbnail."""

    _attr_is_streaming = False

    def __init__(self, config_entry, coordinator, session) -> None:
        super().__init__()
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, config_entry.entry_id)})
        self.url = config_entry.data.get(CONF_URL)
        self.scheme = "https" if config_entry.data.get(CONF_TLS) else "http"
        self.coordinator = coordinator
        self._attr_name = f"{coordinator.api_device_name} Thumbnail"
        self._attr_unique_id = f"{config_entry.entry_id}_thumbnail"
        self._session = session
        self._current_pic = None
        self._current_path = ""
        self.port = config_entry.options.get(CONF_OPTION_THUMBNAIL_PORT) or DEFAULT_PORT

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return current camera image."""
        del width, height

        status = self.coordinator.data.get("status")
        if not status:
            return None
        print_stats = status.get("print_stats", {})
        if print_stats.get("state") != PRINTSTATES.PRINTING.value:
            return None

        new_path = self.coordinator.data.get("thumbnails_path")
        if not new_path or new_path == self._current_path:
            return self._current_pic

        try:
            async with self._session.get(
                f"{self.scheme}://{self.url}:{self.port}/{new_path}"
            ) as resp:
                if resp.status == 200:
                    self._current_pic = await resp.read()
                    self._current_path = new_path
                    return self._current_pic
                _LOGGER.debug("Thumbnail fetch returned HTTP %s", resp.status)
        except Exception as exc:
            _LOGGER.debug("Thumbnail fetch failed: %s", exc)
        return None
