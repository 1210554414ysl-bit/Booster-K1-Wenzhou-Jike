"""Serve the bundled panel and dispatch independent API requests concurrently."""

import json
import secrets
import socket
import subprocess
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


def serve_panel(dispatch):
    with zipfile.ZipFile("resources/music_panel.zip") as bundle:
        assets = {
            "/": (bundle.read("dist/index.html"), "text/html; charset=utf-8"),
            "/player.js": (
                bundle.read("dist/player.js"),
                "text/javascript; charset=utf-8",
            ),
            "/player.css": (bundle.read("dist/player.css"), "text/css; charset=utf-8"),
        }
    host = "127.0.0.1"
    if Path("/usr/sbin/ip").exists() or Path("/usr/bin/ip").exists():
        interfaces = json.loads(
            subprocess.check_output(
                ["ip", "-j", "-4", "addr", "show", "up", "scope", "global"], timeout=3
            )
        )
        addresses = [
            (interface["ifname"], address["local"])
            for interface in interfaces
            for address in interface.get("addr_info", [])
            if interface["ifname"].startswith(("wl", "en", "eth", "usb"))
        ]
        if addresses:
            host = next(
                (address for name, address in addresses if name.startswith("wl")),
                addresses[0][1],
            )
    prefix = "/online-music/" + secrets.token_urlsafe(24)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            route = path[len(prefix) :] if path.startswith(prefix + "/") else ""
            if route not in assets:
                self.send_error(404)
                return
            content, content_type = assets[route]
            self.respond(content, content_type)

        def do_POST(self):
            if urlsplit(self.path).path != prefix + "/api":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                if (
                    not 0 < length <= 128 * 1024
                    or self.headers.get_content_type() != "application/json"
                ):
                    self.send_error(400)
                    return
                self.connection.settimeout(5)
                message = json.loads(self.rfile.read(length))
            except (ValueError, OSError):
                self.send_error(400)
                return
            try:
                result = dispatch(message)
            except Exception:
                result = {"success": False, "message": "请求未完成，请重试。"}
            self.respond(
                json.dumps(result, ensure_ascii=False).encode(),
                "application/json; charset=utf-8",
            )

        def respond(self, content, content_type):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            try:
                self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError, socket.timeout):
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
    server.daemon_threads = True
    threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True
    ).start()
    return server, f"http://{host}:{server.server_port}{prefix}/"
