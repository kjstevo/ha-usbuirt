import collections
import hmac
import json
import logging
import re
import select
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logging.basicConfig(level=logging.INFO)
with open("/data/options.json") as f:
    cfg = json.load(f)
token = cfg["token"]
if len(token) < 24 or token.startswith("CHANGE_ME"):
    raise SystemExit("Set a unique access token at least 24 characters long before starting")
queue = collections.deque(maxlen=256)
lock = threading.Lock()
process = None
# True while mode2 is being restarted right after a normal exit, so /signals
# keeps reporting "running" and the receiver doesn't flip to unavailable.
restarting = False

def capture():
    global process, restarting
    quick_failures = 0
    while True:
        frame = []
        last = time.monotonic()
        got_data = False
        def flush():
            nonlocal got_data
            if len(frame) >= 4:
                with lock:
                    queue.append(list(frame))
                got_data = True
            frame.clear()
        try:
            cmd = ["mode2", "--driver=" + cfg["driver"], "--device=" + cfg["device"]]
            if not restarting:
                logging.info("Starting raw capture: %s", cmd)
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=None, bufsize=0)
            restarting = False
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
                        n = int(raw)
                        if kind == b"timeout" or (kind == b"space" and n >= cfg["gap_us"]):
                            flush()
                            continue
                        if n <= 0 or (not frame and kind != b"pulse"):
                            continue
                        value = n if kind == b"pulse" else -n
                        if frame and (frame[-1] > 0) == (value > 0):
                            frame[-1] += value
                        else:
                            frame.append(value)
                        last = time.monotonic()
                        if len(frame) > 16000:
                            logging.warning("Discarding oversized signal")
                            frame.clear()
                elif frame and time.monotonic() - last > 0.12:
                    flush()
            flush()
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)
        except Exception:
            logging.exception("Capture failed")
            if process is not None and process.poll() is None:
                process.kill()
            got_data = False
        # mode2 with the usb_uirt_raw driver exits ("readdata() failed") after
        # every received signal. Restart it at once so the next press isn't
        # lost; only back off if it keeps exiting without receiving anything.
        quick_failures = 0 if got_data else quick_failures + 1
        if quick_failures <= 3:
            restarting = True
            if not got_data:
                logging.warning("mode2 exited: %s; restarting", process and process.returncode)
            time.sleep(0.05)
        else:
            restarting = False
            logging.warning("mode2 exited: %s; retrying in 5 seconds", process and process.returncode)
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
        body = json.dumps({"running": restarting or (process is not None and process.poll() is None), "signals": signals}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass

threading.Thread(target=capture, daemon=True).start()
ThreadingHTTPServer(("0.0.0.0", cfg["port"]), Handler).serve_forever()
