import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

class ConfigFlow(config_entries.ConfigFlow, domain="usbuirt_receiver"):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            host = user_input["host"].strip()
            if any(c in host for c in "/?#@"):
                errors["base"] = "cannot_connect"
            else:
                url = f"http://{host}:{user_input['port']}/signals"
                try:
                    session = async_get_clientsession(self.hass)
                    async with session.get(url, headers={"Authorization": "Bearer " + user_input["token"]}, timeout=8) as response:
                        response.raise_for_status()
                        data = await response.json()
                        if not data.get("running"):
                            raise ValueError("Capture process is not running")
                except Exception:
                    errors["base"] = "cannot_connect"
                else:
                    await self.async_set_unique_id(f"{host}:{user_input['port']}")
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(title="USB-UIRT Receiver", data={**user_input, "host": host})
        return self.async_show_form(step_id="user", data_schema=vol.Schema({
            vol.Required("host"): str,
            vol.Required("port", default=8766): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Required("token"): str,
        }), errors=errors)
