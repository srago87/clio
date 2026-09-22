# Clio — Copyright (C) 2026 Sean Rago
# Licensed under the GNU Affero General Public License v3.0 or later.
# See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

"""Local-network onboarding helpers, used by start.sh in TUNNEL_MODE=local:
detecting this machine's LAN IP and printing a terminal QR code encoding
the phone-facing URL, so a phone on the same Wi-Fi can scan and connect
without Tailscale or any second-device install.
"""

import ipaddress
import socket
import sys


def get_local_ip() -> str | None:
    """Best-effort LAN IP for this machine, or None if none could be found.

    There's no direct "what's my LAN IP" syscall, so this uses the standard
    workaround: open a UDP socket toward a public address and read back the
    local address the OS routed it through. UDP connect() only resolves a
    route — no packet is actually sent, so this works even without internet
    access. Falls back to hostname resolution if that fails (e.g. no route
    to any external network).

    The fallback is rejected if it resolves to a loopback address, which
    happens on Debian/Ubuntu systems with the common `/etc/hosts` line
    `127.0.1.1 <hostname>` — that's never reachable from another device, so
    treating it as a valid LAN IP would print an unreachable QR code/URL.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        pass
    finally:
        sock.close()

    fallback = socket.gethostbyname(socket.gethostname())
    if ipaddress.ip_address(fallback).is_loopback:
        return None
    return fallback


def print_qr(url: str) -> None:
    import segno

    segno.make(url).terminal(out=sys.stdout, compact=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "qr":
        print_qr(sys.argv[2])
    else:
        print(get_local_ip() or "")
