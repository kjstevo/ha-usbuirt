import collections
import fcntl
import hmac
import json
import logging
import os
import re
import select
import struct
import subprocess
import termios
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logging.basicConfig(level=logging.INFO)
with open("/data/options.json") as f:
    cfg = json.load(f)
token = cfg["token"]
if len(token) < 24 or token.startswith("CHANGE_ME"):
    raise SystemExit("Set a unique access token at least 24 characters long before starting")
GAP_US = cfg["gap_us"]
queue = collections.deque(maxlen=256)
lock = threading.Lock()
# True while a capture backend is reading the receiver (reported as "running").
capturing = False
# True once direct capture has initialised the USB-UIRT at least once.
direct_started = False


class FrameBuilder:
    """Collects pulse/space durations into frames, the same way for every backend.

    Frames start with a pulse, adjacent same-kind durations are merged, and a
    space of at least gap_us ends the frame.
    """

    def __init__(self):
        self.frame = []
        self.last = time.monotonic()
        self.got_data = False

    def add(self, is_pulse, n):
        if not is_pulse and n >= GAP_US:
            self.flush()
            return
        if n <= 0 or (not self.frame and not is_pulse):
            return
        value = n if is_pulse else -n
        if self.frame and (self.frame[-1] > 0) == (value > 0):
            self.frame[-1] += value
        else:
            self.frame.append(value)
        self.last = time.monotonic()
        if len(self.frame) > 16000:
            logging.warning("Discarding oversized signal")
            self.frame.clear()

    def flush(self):
        if len(self.frame) >= 4:
            with lock:
                queue.append(list(self.frame))
            self.got_data = True
        self.frame.clear()


# ---------------------------------------------------------------------------
# Direct backend: talk to the USB-UIRT over its serial port (protocol as in
# LIRC's uirt2_common.c). Reads continuously, so there's no gap between presses.
# ---------------------------------------------------------------------------
UIRT2_UNIT = 50  # microseconds per raw byte
UIRT2_SETMODERAW = 0x21
UIRT2_GETVERSION = 0x23
UIRT2_CSERROR = 0x80


def _read(fd, n, timeout):
    """Read up to n bytes, waiting at most timeout seconds overall."""
    out = b""
    deadline = time.monotonic() + timeout
    while len(out) < n:
        left = deadline - time.monotonic()
        if left <= 0 or not select.select([fd], [], [], left)[0]:
            break
        chunk = os.read(fd, n - len(out))
        if not chunk:
            raise OSError("device closed")
        out += chunk
    return out


def _flush_input(fd, timeout):
    while select.select([fd], [], [], timeout)[0]:
        if not os.read(fd, 4096):
            raise OSError("device closed")


def _command(fd, payload, reply_len):
    _flush_input(fd, 0)
    os.write(fd, payload + bytes([(-sum(payload)) & 0xFF]))
    reply = _read(fd, reply_len, 1.0)
    if len(reply) < reply_len:
        return None
    if reply_len > 1 and sum(reply) & 0xFF:
        logging.warning("USB-UIRT reply checksum error: %s", reply.hex())
        return None
    return reply


def uirt_open(path):
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        # Raw tty (cfmakeraw), then wait for the UIRT to power up
        iflag, oflag, cflag, lflag, ispeed, ospeed, cc = termios.tcgetattr(fd)
        iflag &= ~(termios.IGNBRK | termios.BRKINT | termios.PARMRK | termios.ISTRIP
                   | termios.INLCR | termios.IGNCR | termios.ICRNL | termios.IXON)
        oflag &= ~termios.OPOST
        lflag &= ~(termios.ECHO | termios.ECHONL | termios.ICANON | termios.ISIG | termios.IEXTEN)
        cflag &= ~(termios.CSIZE | termios.PARENB)
        cflag |= termios.CS8
        cc[termios.VMIN] = 0
        cc[termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSAFLUSH, [iflag, oflag, cflag, lflag, ispeed, ospeed, cc])
        time.sleep(0.1)
        # 115200 baud, 8 bits, RTS/CTS flow control
        cflag |= termios.CREAD | termios.CLOCAL | termios.CRTSCTS
        termios.tcsetattr(fd, termios.TCSAFLUSH,
                          [iflag, oflag, cflag, lflag, termios.B115200, termios.B115200, cc])

        _flush_input(fd, 0.2)
        reply = _command(fd, bytes([UIRT2_GETVERSION]), 3)
        if reply is None:
            # Newer firmware sends extended version information
            _flush_input(fd, 0.2)
            reply = _command(fd, bytes([UIRT2_GETVERSION]), 8)
        if reply is None:
            raise OSError("no USB-UIRT found (no reply to version request)")
        version = (reply[0] << 8) | reply[1]

        reply = _command(fd, bytes([UIRT2_SETMODERAW]), 1)
        if reply is None or reply[0] >= UIRT2_CSERROR:
            raise OSError("could not set raw mode")

        if version >= 0x0905:
            try:
                fcntl.ioctl(fd, termios.TIOCMBIC, struct.pack("I", termios.TIOCM_DTR))
            except OSError as err:
                logging.warning("Could not clear DTR: %s", err)
        logging.info("USB-UIRT version %04x ready (direct raw capture)", version)
        return fd
    except BaseException:
        os.close(fd)
        raise


def direct_capture():
    """Read raw IR from the USB-UIRT until the device fails."""
    global capturing, direct_started
    fd = uirt_open(cfg["device"])
    builder = FrameBuilder()
    new_signal = True   # next bytes are the 2-byte gap since the last signal
    gap_hi = None
    pulse = True
    end_seen_at = None  # time the 0xFF end-of-signal byte arrived
    end_wait = GAP_US / 1e6 + 0.03
    capturing = True
    direct_started = True
    try:
        while True:
            if select.select([fd], [], [], 0.01)[0]:
                data = os.read(fd, 4096)
                if not data:
                    raise OSError("device closed")
                for b in data:
                    if b == 0xFF:
                        new_signal = True
                        gap_hi = None
                        end_seen_at = time.monotonic()
                        continue
                    end_seen_at = None
                    if new_signal:
                        if gap_hi is None:
                            gap_hi = b
                            continue
                        builder.add(False, UIRT2_UNIT * (256 * gap_hi + b))
                        gap_hi = None
                        new_signal = False
                        pulse = True
                        continue
                    builder.add(pulse, UIRT2_UNIT * b or 1)
                    pulse = not pulse
            if builder.frame:
                now = time.monotonic()
                # Send the frame as soon as the signal has clearly ended
                if (end_seen_at is not None and now - end_seen_at > end_wait) or now - builder.last > 0.12:
                    builder.flush()
    finally:
        capturing = False
        os.close(fd)


# ---------------------------------------------------------------------------
# mode2 backend (fallback): LIRC's mode2 exits after every signal with the
# usb_uirt_raw driver, so it is restarted right away (about 1 s per restart).
# ---------------------------------------------------------------------------
def mode2_capture():
    global capturing
    builder = FrameBuilder()
    cmd = ["mode2", "--driver=" + cfg["driver"], "--device=" + cfg["device"]]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=None, bufsize=0)
    capturing = True
    try:
        buffer = b""
        while process.poll() is None:
            ready, _, _ = select.select([process.stdout], [], [], 0.05)
            if ready:
                chunk = process.stdout.read(4096)
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    match = re.fullmatch(rb"\s*(pulse|space|timeout)\s+(\d+)\s*", line)
                    if not match:
                        continue
                    kind, raw = match.groups()
                    if kind == b"timeout":
                        builder.flush()
                    else:
                        builder.add(kind == b"pulse", int(raw))
            elif builder.frame and time.monotonic() - builder.last > 0.12:
                builder.flush()
        builder.flush()
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=5)
    return builder.got_data, process.returncode


def capture():
    global capturing
    backend = cfg.get("backend", "direct")
    direct_ok = False      # direct mode has worked at least once
    direct_failures = 0
    quick_failures = 0
    logging.info("Capture backend: %s", backend)
    while True:
        if backend == "direct":
            try:
                direct_capture()
            except Exception as err:
                direct_ok = direct_ok or direct_started
                direct_failures = 0 if direct_ok else direct_failures + 1
                if direct_failures >= 3:
                    # Never managed to initialise the device: use mode2 instead
                    logging.error("Direct USB-UIRT capture failed (%s); falling back to mode2", err)
                    backend = "mode2"
                    continue
                logging.warning("Direct USB-UIRT capture stopped: %s; retrying in 2 seconds", err)
                time.sleep(2)
            continue

        got_data, code = False, None
        try:
            got_data, code = mode2_capture()
        except Exception:
            logging.exception("Capture failed")
        # Restart at once after a normal post-signal exit; keep reporting
        # "running" meanwhile. Back off only if it keeps exiting with no data.
        quick_failures = 0 if got_data else quick_failures + 1
        if quick_failures <= 3:
            capturing = True
            if not got_data:
                logging.warning("mode2 exited: %s; restarting", code)
            time.sleep(0.05)
        else:
            capturing = False
            logging.warning("mode2 exited: %s; retrying in 5 seconds", code)
            time.sleep(5)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
            self.send_error(401)
            return
        if self.path != "/signals":
            self.send_error(404)
            return
        with lock:
            signals = list(queue)
            queue.clear()
        body = json.dumps({"running": capturing, "signals": signals}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


threading.Thread(target=capture, daemon=True).start()
ThreadingHTTPServer(("0.0.0.0", cfg["port"]), Handler).serve_forever()
