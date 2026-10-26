# USB-UIRT Raw Capture

Experimental; not hardware tested. Stop host lircd, mode2, and irrecord before starting. Only this app should open the receiver.

Set token to a unique random secret of at least 24 characters. Default device is /dev/serial/by-id/usb-FTDI_USB-UIRT-if00-port0; try /dev/ttyUSB0 if the alias is unavailable inside the app. backend selects how the receiver is read. direct (default) talks to the USB-UIRT over its serial port and reads continuously, so presses register back to back. If direct mode cannot initialise the device after three tries it falls back to mode2 automatically. mode2 uses LIRC's mode2 with the configured driver; with usb_uirt_raw, mode2 exits after every signal and is restarted, which costs about a second per press. Default driver usb_uirt_raw depends on Debian LIRC driver availability.

Start and inspect logs. Install the separate custom integration following the repository README. Use the HA machine LAN IPv4 address, port 8766, and the same token.

This app listens on all interfaces using unencrypted HTTP with bearer-token authentication. Restrict TCP 8766 to Home Assistant on a trusted LAN; never expose it to the internet. It does not automatically install the integration or HAIR.

Raw signal framing uses gap_us (default 20000 microseconds) and a 120 ms idle flush; in direct mode a signal is sent about gap_us + 30 ms after the USB-UIRT reports its end. Adjust gap_us for protocols with long internal spaces. Carrier frequency is unspecified. Long presses can produce repeat signals. One polling integration only; endpoint reads drain a 256-signal queue.


clean_signals (default on) repairs signals damaged by ambient IR noise or a weak remote: gaps under 350 us inside a pulse and blips under 200 us inside a space are merged away, a leading pulse broken into pieces is rejoined, and noise in front of a code's leader is dropped. These timings do not occur in common IR protocols (NEC, Samsung, Sony, RC5, RC6, JVC, Panasonic, Sharp), so clean codes are unchanged. Turn it off if a remote with unusually short timings stops matching.
