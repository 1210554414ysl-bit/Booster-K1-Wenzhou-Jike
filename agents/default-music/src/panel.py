"""Local capability URL; two existing panels share a single music controller."""

import json
import secrets
import subprocess
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

MAX_UPLOAD = 64 * 1024 * 1024


def serve_panel(local, online, upload):
    with zipfile.ZipFile('res/music_panel.zip') as bundle:
        assets = {name: bundle.read(name) for name in bundle.namelist()}
    prefix = '/default-music/' + secrets.token_urlsafe(24) + '/'
    host = '127.0.0.1'
    try:
        interfaces = json.loads(subprocess.check_output(
            ['ip', '-j', '-4', 'addr', 'show', 'up', 'scope', 'global'], timeout=3))
        addresses = [(interface['ifname'], entry['local'])
                     for interface in interfaces for entry in interface.get('addr_info', [])
                     if interface['ifname'].startswith(('wl', 'en', 'eth', 'usb'))]
        if addresses:
            host = next((ip for name, ip in addresses if name.startswith('wl')), addresses[0][1])
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    upload_lock = threading.Lock()
    command_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, payload, status=200, mime='application/json; charset=utf-8'):
            if not isinstance(payload, bytes):
                payload = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            path = urlsplit(self.path).path
            route = path[len(prefix):] if path.startswith(prefix) else None
            if route is None:
                self.send_error(404)
                return
            if route == '' or route.endswith('/'):
                route += 'index.html'
            name = 'dist/' + route
            if name not in assets:
                self.send_error(404)
                return
            mime = ('text/html; charset=utf-8' if name.endswith('.html') else
                    'text/css; charset=utf-8' if name.endswith('.css') else 'text/javascript; charset=utf-8')
            self.respond(assets[name], mime=mime)

        def do_POST(self):
            url = urlsplit(self.path)
            route = url.path[len(prefix):] if url.path.startswith(prefix) else ''
            if route not in ('local/api', 'online/api', 'local/upload'):
                self.send_error(404)
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                self.send_error(400)
                return
            if route == 'local/upload':
                if not 0 < length <= MAX_UPLOAD:
                    self.respond({'success': False, 'message': '单个文件最多 64 MiB。'}, 413)
                    return
                if self.headers.get_content_type() != 'application/octet-stream':
                    self.send_error(400)
                    return
                if not upload_lock.acquire(False):
                    self.respond({'success': False, 'message': '有歌曲正在上传，请稍后重试。'}, 409)
                    return
                try:
                    self.connection.settimeout(15)
                    result = upload(parse_qs(url.query).get('name', [''])[0], self.rfile, length)
                    self.respond(result, 201 if result.get('success') else 400)
                finally:
                    upload_lock.release()
                return
            if not 0 < length <= 128 * 1024 or self.headers.get_content_type() != 'application/json':
                self.send_error(400)
                return
            try:
                self.connection.settimeout(3)
                message = json.loads(self.rfile.read(length))
            except (OSError, ValueError):
                self.send_error(400)
                return
            if not isinstance(message, dict):
                self.send_error(400)
                return
            dispatch = local if route == 'local/api' else online
            # Provider I/O and read-only status must never block playback commands.
            if message.get('command') in ('status', 'search', 'login_start', 'login_poll',
                                           'playlists', 'playlist_tracks', 'logout'):
                result = dispatch(message)
            else:
                with command_lock:
                    result = dispatch(message)
            self.respond(result)

    server = ThreadingHTTPServer(('0.0.0.0', 0), Handler)
    threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.1}, daemon=True).start()
    return server, f'http://{host}:{server.server_port}{prefix}'
