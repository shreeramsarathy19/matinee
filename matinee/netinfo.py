"""Figure out the URLs another device (the iPhone) can use to reach this Mac."""
from __future__ import annotations

import re
import socket
import subprocess
from typing import List


def local_ipv4s() -> List[str]:
    ips: List[str] = []
    try:
        out = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=3).stdout
        for m in re.finditer(r"^\s*inet (\d+\.\d+\.\d+\.\d+)", out, re.M):
            ip = m.group(1)
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    if not ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ips.append(s.getsockname()[0])
            s.close()
        except Exception:
            pass
    return ips


def bonjour_name() -> str:
    try:
        name = subprocess.run(
            ["scutil", "--get", "LocalHostName"], capture_output=True, text=True, timeout=3
        ).stdout.strip()
        if name:
            return name + ".local"
    except Exception:
        pass
    host = socket.gethostname()
    return host if host.endswith(".local") else host + ".local"


def urls(port: int) -> List[str]:
    out = [f"http://{bonjour_name().lower()}:{port}"]
    out += [f"http://{ip}:{port}" for ip in local_ipv4s()]
    return out
