#!/usr/bin/env python3
"""Booster K1 agent control over the local booster_agent_manager socket.

Run this ON the robot (127.0.0.1:6868).

    python3 k1_agent_ctl.py list
    python3 k1_agent_ctl.py status <agent-id>
    python3 k1_agent_ctl.py install <path/to/x.agent>
    python3 k1_agent_ctl.py restart <agent-id>      # stop -> start -> set (activates)
    python3 k1_agent_ctl.py activate <agent-id>     # just set_agent
    python3 k1_agent_ctl.py stop <agent-id>

Why `restart` calls set_agent: `start_agent` only launches the process.
`on_agent_activated` fires when the agent becomes the *current* agent, i.e. on
`set_agent`. Starting without setting leaves the agent "Inactive" with an empty
playlist/UI — this bit us once.
"""

import argparse
import json
import socket
import struct
import subprocess
import sys
import time

HOST = "127.0.0.1"
PORT = 6868
APP_API_LEVEL = 130
INSTALL_CLIENT = "/opt/booster/BoosterAgent/install_agent_client"


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    buf = bytearray()
    while len(buf) < size:
        chunk = sock.recv(size - len(buf))
        if not chunk:
            raise ConnectionError("socket closed before full response")
        buf.extend(chunk)
    return bytes(buf)


def request(name: str, params: dict, timeout: float = 60.0) -> dict:
    payload = {
        "request": name,
        "params": params,
        "app_api_level": APP_API_LEVEL,
        "app_platform": "",
    }
    raw = json.dumps(payload).encode("utf-8")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect((HOST, PORT))
        sock.sendall(struct.pack("<I", len(raw)))
        sock.sendall(raw)
        size = struct.unpack("<I", _recv_exact(sock, 4))[0]
        return json.loads(_recv_exact(sock, size).decode("utf-8"))


def list_agents() -> int:
    response = request("get_agents", {})
    for agent in response.get("result", {}).get("agents", []):
        mark = "*" if agent.get("agent_state") == "Active" else " "
        print(
            f"{mark} {agent.get('id'):<34} v{agent.get('version'):<8} "
            f"state={agent.get('agent_state'):<14} running={agent.get('running')}"
        )
    return 0


def status(agent_id: str) -> int:
    response = request("get_agents", {})
    for agent in response.get("result", {}).get("agents", []):
        if agent.get("id") == agent_id:
            print(json.dumps(agent, ensure_ascii=False, indent=2))
            return 0
    print(f"agent not found: {agent_id}", file=sys.stderr)
    return 1


def install(path: str) -> int:
    return subprocess.call(["python3", INSTALL_CLIENT, path])


def stop(agent_id: str) -> int:
    response = request("stop_agent", {"id": agent_id})
    print("stop:", response.get("success"), response.get("error") or "")
    return 0 if response.get("success") else 1


def activate(agent_id: str) -> int:
    response = request("set_agent", {"id": agent_id})
    print("set_agent:", response.get("success"), response.get("error") or "")
    return 0 if response.get("success") else 1


def restart(agent_id: str, settle: float = 6.0) -> int:
    stop(agent_id)
    time.sleep(settle)
    response = request("start_agent", {"id": agent_id})
    print("start_agent:", response.get("success"), response.get("error") or "")
    time.sleep(2.0)
    activate(agent_id)
    time.sleep(3.0)
    print("\ncurrent state:")
    return status(agent_id)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list").set_defaults(func=lambda a: list_agents())

    p_status = sub.add_parser("status")
    p_status.add_argument("agent_id")
    p_status.set_defaults(func=lambda a: status(a.agent_id))

    p_install = sub.add_parser("install")
    p_install.add_argument("path")
    p_install.set_defaults(func=lambda a: install(a.path))

    p_stop = sub.add_parser("stop")
    p_stop.add_argument("agent_id")
    p_stop.set_defaults(func=lambda a: stop(a.agent_id))

    p_act = sub.add_parser("activate")
    p_act.add_argument("agent_id")
    p_act.set_defaults(func=lambda a: activate(a.agent_id))

    p_restart = sub.add_parser("restart")
    p_restart.add_argument("agent_id")
    p_restart.set_defaults(func=lambda a: restart(a.agent_id))

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
