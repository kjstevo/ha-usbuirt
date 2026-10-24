import asyncio
import logging
from homeassistant.components.infrared import InfraredReceiverEntity, InfraredReceivedSignal
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([USBUIRTReceiver(entry)])

class USBUIRTReceiver(InfraredReceiverEntity):
    _attr_has_entity_name = True
    _attr_name = "Receiver"
    _attr_available = False

    def __init__(self, entry):
        self.entry = entry
        self._attr_unique_id = entry.entry_id + "_receiver"
        self._attr_device_info = DeviceInfo(identifiers={("usbuirt_receiver", entry.entry_id)}, name="USB-UIRT", manufacturer="USB-UIRT", model="Raw infrared receiver")
        self._task = None
        self._last_error = None

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self._task = self.hass.async_create_background_task(self._poll(), "USB-UIRT capture polling")

    async def async_will_remove_from_hass(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await super().async_will_remove_from_hass()

    async def _poll(self):
        config = self.entry.data
        url = f"http://{config['host']}:{config['port']}/signals"
        session = async_get_clientsession(self.hass)
        while True:
            try:
                async with session.get(url, headers={"Authorization": "Bearer " + config["token"]}, timeout=8) as response:
                    response.raise_for_status()
                    data = await response.json()
                available = bool(data.get("running"))
                if available != self._attr_available:
                    self._attr_available = available
                    self.async_write_ha_state()
                if not available:
                    raise RuntimeError("mode2 is not running; check capture app logs")
                self._last_error = None
                for timings in data.get("signals", []):
                    if isinstance(timings, list) and len(timings) >= 4 and all(type(x) is int and x != 0 for x in timings):
                        self._handle_received_signal(InfraredReceivedSignal(timings=timings))
            except asyncio.CancelledError:
                raise
            except Exception as err:
                if self._attr_available:
                    self._attr_available = False
                    self.async_write_ha_state()
                message = str(err)
                if message != self._last_error:
                    _LOGGER.warning("USB-UIRT capture unavailable: %s", message)
                    self._last_error = message
            await asyncio.sleep(0.25 if self._attr_available else 1)
