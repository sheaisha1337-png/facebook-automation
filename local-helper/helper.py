import json
import re
import subprocess
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HOST, PORT = "127.0.0.1", 8765
ALLOWED_ORIGINS = {"https://facebook-kashif.onrender.com", "http://127.0.0.1:5000", "http://localhost:5000"}

def valid_youtube_url(value):
    try:
        parsed = urlparse(value)
        return parsed.scheme == "https" and parsed.hostname in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
    except Exception:
        return False

class Handler(BaseHTTPRequestHandler):
    server_version = "KashifLocalDownloader/1.0"
    def _origin(self): return self.headers.get("Origin", "")
    def _cors(self):
        origin = self._origin()
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin); self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Expose-Headers", "Content-Disposition")
    def _json(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status); self._cors(); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_OPTIONS(self):
        if self._origin() not in ALLOWED_ORIGINS: self.send_error(403); return
        self.send_response(204); self._cors(); self.end_headers()
    def do_GET(self):
        if self.path != "/health": self.send_error(404); return
        self._json(200, {"ok": True, "name": "Kashif Local Downloader"})
    def do_POST(self):
        if self.path != "/download": self.send_error(404); return
        if self._origin() not in ALLOWED_ORIGINS: self._json(403, {"error": "Website not allowed."}); return
        try:
            payload = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", "0")), 16384)))
            url = str(payload.get("url", "")).strip()
            if not valid_youtube_url(url): self._json(400, {"error": "Valid YouTube URL required."}); return
            with tempfile.TemporaryDirectory(prefix="kashif-youtube-") as temp:
                output = str(Path(temp) / "video.%(ext)s")
                cmd = [sys.executable, "-m", "yt_dlp", "--no-playlist", "--merge-output-format", "mp4", "-f", "bv*[height<=1080]+ba/b[height<=1080]/best", "-o", output, url]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
                files = sorted(Path(temp).glob("video.*"), key=lambda p: p.stat().st_size, reverse=True)
                if result.returncode != 0 or not files:
                    self._json(502, {"error": (result.stderr or result.stdout or "Download failed")[-700:]}); return
                video = files[0]; safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", video.name)
                self.send_response(200); self._cors(); self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Disposition", f'attachment; filename="{safe_name}"')
                self.send_header("Content-Length", str(video.stat().st_size)); self.end_headers()
                with video.open("rb") as source:
                    while chunk := source.read(1024 * 1024): self.wfile.write(chunk)
        except subprocess.TimeoutExpired: self._json(504, {"error": "Download timed out after 15 minutes."})
        except (BrokenPipeError, ConnectionResetError): pass
        except Exception as exc: self._json(500, {"error": str(exc)})
    def log_message(self, fmt, *args): print("[helper]", fmt % args)

if __name__ == "__main__":
    print(f"Kashif Local Downloader running at http://{HOST}:{PORT}")
    print("Keep this window open while using the website. Press Ctrl+C to stop.")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
