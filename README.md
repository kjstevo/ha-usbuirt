# USB-UIRT receiver for Home Assistant and HAIR

Experimental 0.1.0. A capture app plus a native infrared receiver custom integration. No transmitter support. No LIRC remote definitions or irw button names required. HAIR is installed separately. This is a custom repository, not an official Home Assistant store listing.

## Install the app from Home Assistant

1. Open Settings > Apps (called Add-ons on older versions) > App store > menu > Repositories.
2. Add https://github.com/kjstevo/ha-usbuirt and refresh the store.
3. Install USB-UIRT Raw Capture. First install builds a container image and requires internet access. amd64 and aarch64 only.
4. Stop Linux-host lircd and any mode2/irrecord process using this USB-UIRT. Leave USB passthrough enabled. Only this capture app should open it.
5. In app Configuration set a unique random token at least 24 characters long. The default device is /dev/serial/by-id/usb-FTDI_USB-UIRT-if00-port0. If unavailable inside the app, try /dev/ttyUSB0. Default driver is usb_uirt_raw; use the driver from your known-working mode2 command if different.
6. Start the app and inspect logs. Serial access is requested using uart:true; full-access mode should not be necessary.

## Install the receiver integration separately

The app store installs the capture app, not custom_components. Choose one route:

- HACS custom repository: in HACS add https://github.com/kjstevo/ha-usbuirt with category Integration, then download USB-UIRT Receiver and restart Home Assistant. HACS acceptance/install has not been tested; if it rejects this repository use manual installation.
- Manual: copy custom_components/usbuirt_receiver into /config/custom_components/usbuirt_receiver and restart Home Assistant.

Then Settings > Devices & services > Add integration > USB-UIRT Receiver. Enter the HA machine LAN IPv4 address (without http://), port 8766, and the same access token. Do not use localhost when capture and Core are in different network namespaces. Ensure the address is reachable from Core.

Open HAIR, select the receiver, open Sniffer, and press a remote button briefly. Reload HAIR if it does not refresh its receiver list. The integration requires a Core version exposing InfraredReceiverEntity and InfraredReceivedSignal; version metadata is not a guarantee of API compatibility.

## Architecture

USB-UIRT -> mode2 in capture app -> token-authenticated local HTTP -> native InfraredReceiverEntity -> HAIR.

Both pieces run on your existing Home Assistant machine; no external computer or MQTT required. The capture app provides mode2 without modifying the Core container. Hardware visibility alone does not establish serial access inside every container.

## Security

The app uses host networking and listens on all interfaces on TCP 8766. HTTP is unencrypted and uses a bearer token. Keep it on a trusted LAN, firewall access to Home Assistant, and never forward this port to the internet. One polling integration per endpoint: reads drain the queue. Queue is limited to 256 captures; events can be lost during outages.

## Troubleshooting and limitations

- Check app logs for device busy, missing path, or unavailable driver. The packaged Debian LIRC build has not been verified against USB-UIRT hardware.
- Stop host lircd, associated socket activation if present, and competing capture tools. Do not stop unrelated Home Assistant services.
- mode2 output must contain pulse N, space N, or timeout N lines. Raw reception is enough; irw may remain silent.
- gap_us defaults to 20000 microseconds; raise for protocols with long internal spaces or lower if frames merge. A 120 ms idle flush is also used. Long holds can create repeat events.
- Carrier frequency is not inferred. Availability means mode2 is alive, not proof of successful IR reception.
- No Docker build, actual Home Assistant runtime, HACS installation, HAIR matching, or hardware test has been performed. Treat this as an initial experimental implementation and inspect logs before relying on automations.
- Remove the integration to stop polling; stop the capture app separately to release USB hardware.

## Technical references

https://contributors.home-assistant.io/core/entities/infrared/
https://developers.home-assistant.io/docs/add-ons/configuration/
https://www.lirc.org/html/mode2.html
https://github.com/DAB-LABS/HAIR
