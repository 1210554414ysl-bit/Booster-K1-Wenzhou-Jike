#!/usr/bin/env python3
"""Generic client for booster_agent_manager (127.0.0.1:6868).

Usage:
    python3 agent_client.py get_agents
    python3 agent_client.py set_agent '{"id": "com.wenzhoujike.k1.music"}'
    python3 agent_client.py start_agent '{"id": "com.wenzhoujike.k1.music"}'
"""

import json
import socket
import struct
import sys

HOST = "127.0.0.1"
PORT = 6868
APP_API_LEVEL = 130


def recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("socket closed before full response")
        buf.extend(chunk)
    return bytes(buf)


def send(request: str, params: dict) -> dict:
    payload = {
        "request": request,
        "params": params,
        "app_api_level": APP_API_LEVEL,
        "app_platform": "",
    }
    raw = json.dumps(payload).encode("utf-8")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(30)
        sock.connect((HOST, PORT))
        sock.sendall(struct.pack("<I", len(raw)))
        sock.sendall(raw)
        size = struct.unpack("<I", recv_exact(sock, 4))[0]
        body = recv_exact(sock, size)
        return json.loads(body.decode("utf-8"))


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    request = sys.argv[1]
    params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    try:
        response = send(request, params)
    except Exception as exc:
        print(f"request failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(response, ensure_ascii=False, indent=2))
    return 0 if response.get("success") is True else 3


if __name__ == "__main__":
    sys.exit(main())
