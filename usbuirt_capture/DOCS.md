# USB-UIRT Raw Capture

Experimental; not hardware tested. Stop host lircd, mode2, and irrecord before starting. Only this app should open the receiver.

Set token to a unique random secret of at least 24 characters. Default device is /dev/serial/by-id/usb-FTDI_USB-UIRT-if00-port0; try /dev/ttyUSB0 if the alias is unavailable inside the app. Use the driver from your known-working mode2 command. Default usb_uirt_raw depends on Debian LIRC driver availability.

Start and inspect logs. Install the separate custom integration following the repository README. Use the HA machine LAN IPv4 address, port 8766, and the same token.

This app listens on all interfaces using unencrypted HTTP with bearer-token authentication. Restrict TCP 8766 to Home Assistant on a trusted LAN; never expose it to the internet. It does not automatically install the integration or HAIR.

Raw signal framing uses gap_us (default 20000 microseconds) and a 120 ms idle flush. Adjust gap_us for protocols with long internal spaces. Carrier frequency is unspecified. Long presses can produce repeat signals. One polling integration only; endpoint reads drain a 256-signal queue.
