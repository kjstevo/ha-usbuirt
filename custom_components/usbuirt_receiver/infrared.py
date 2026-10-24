import asyncio
import logging
import time
from homeassistant.components.infrared import InfraredReceiverEntity, InfraredReceivedSignal
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo

_LOGGER = logging.getLogger(__name__)

# Fired for each NEC repeat frame ("ditto") while a button is held down.
# Data: entity_id (this receiver), count (repeats so far in this hold),
# held (seconds since the button's full code was received).
EVENT_REPEAT = "usbuirt_receiver_repeat"
# Longest gap between frames of one hold (NEC repeats come every ~110 ms)
REPEAT_CHAIN_MAX_GAP = 0.35
# Ask the capture app to hold each request until a signal arrives
LONG_POLL_WAIT = 5


def _is_nec_repeat(timings):
    """NEC repeat frame: pulse ~9000 us, space ~2250 us, pulse ~560 us."""
    return (
        len(timings) == 3
        and 7000 <= timings[0] <= 11000
        and -3000 <= timings[1] <= -1500
        and 300 <= timings[2] <= 900
    )


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
        self._main_at = None      # when the last full frame arrived
        self._last_frame_at = None
        self._repeat_count = 0

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

    def _handle_timings(self, timings):
        now = time.monotonic()
        if _is_nec_repeat(timings):
            if (self._main_at is None or self._last_frame_at is None
                    or now - self._last_frame_at > REPEAT_CHAIN_MAX_GAP):
                self._main_at = None  # stray repeat, not part of a hold
                return
            self._last_frame_at = now
            self._repeat_count += 1
            self.hass.bus.async_fire(EVENT_REPEAT, {
                "entity_id": self.entity_id,
                "count": self._repeat_count,
                "held": round(now - self._main_at, 3),
            })
            return
        if len(timings) < 4:
            return
        self._main_at = self._last_frame_at = now
        self._repeat_count = 0
        self._handle_received_signal(InfraredReceivedSignal(timings=timings))

    async def _poll(self):
        config = self.entry.data
        url = f"http://{config['host']}:{config['port']}/signals"
        session = async_get_clientsession(self.hass)
        while True:
            started = time.monotonic()
            try:
                async with session.get(url, params={"wait": LONG_POLL_WAIT},
                                       headers={"Authorization": "Bearer " + config["token"]},
                                       timeout=LONG_POLL_WAIT + 5) as response:
                    response.raise_for_status()
                    data = await response.json()
                available = bool(data.get("running"))
                if available != self._attr_available:
                    self._attr_available = available
                    self.async_write_ha_state()
                if not available:
                    raise RuntimeError("capture is not running; check capture app logs")
                self._last_error = None
                signals = data.get("signals", [])
                for timings in signals:
                    if isinstance(timings, list) and len(timings) >= 3 and all(type(x) is int and x != 0 for x in timings):
                        self._handle_timings(timings)
                # A capture app without long-poll support answers at once:
                # fall back to polling every 0.25 s.
                if not signals and time.monotonic() - started < 0.2:
                    await asyncio.sleep(0.25)
                continue
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
            await asyncio.sleep(1)
