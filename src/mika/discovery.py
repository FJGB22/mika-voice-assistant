"""Announces the server on the local network as mika.local (mDNS), so a satellite
can find it without a hard-coded IP address. See docs/protocol.md.

Run python -m mika.discovery to look for a running server the way a satellite would.
"""

import asyncio
import socket
from contextlib import asynccontextmanager

from zeroconf import NonUniqueNameException
from zeroconf.asyncio import AsyncServiceInfo, AsyncZeroconf

HOSTNAME = "mika.local."
SERVICE_TYPE = "_mika._tcp.local."
SERVICE_NAME = f"Mika.{SERVICE_TYPE}"

# Any outside address works: connect() on a UDP socket sends nothing, it only asks
# the OS which interface it would use, and that is the one facing the network.
ROUTE_PROBE = ("8.8.8.8", 80)

FIND_TIMEOUT_MS = 3000


def local_ip():
    # UDP, not TCP: a TCP connect() would really try to reach ROUTE_PROBE and hang
    # without internet. OSError means there is no route out at all.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(ROUTE_PROBE)
        except OSError:
            return None
        return s.getsockname()[0]


def make_service_info(ip, port):
    return AsyncServiceInfo(
        type_=SERVICE_TYPE,
        name=SERVICE_NAME,
        addresses=[socket.inet_aton(ip)],  # zeroconf wants packed bytes, not text
        port=port,
        server=HOSTNAME,
    )


@asynccontextmanager
async def announce(port):
    # Every path yields exactly once, so the server runs even when it cannot be
    # announced: satellites can still reach it by IP, but not if it crashes.
    ip = local_ip()
    if ip is None:
        print("mDNS: no network found, not announcing")
        yield
        return

    # Only the interface that faces the network: on all interfaces a satellite could
    # be handed a virtual adapter's address (WSL, VirtualBox) it cannot reach.
    zeroconf = AsyncZeroconf(interfaces=[ip])
    info = make_service_info(ip, port)
    try:
        # The first await probes the name (and raises if taken); the second waits
        # for the announcement packets to go out.
        await (await zeroconf.async_register_service(info))
    except NonUniqueNameException:
        name_taken = True
    else:
        name_taken = False

    # Handled outside the except block: the server runs inside the yield, and an
    # error there would otherwise be reported as caused by the name conflict.
    if name_taken:
        await zeroconf.async_close()  # nothing was announced, so nothing to keep open
        print(f"mDNS: another machine is already {HOSTNAME[:-1]}, not announcing")
        yield
        return

    print(f"mDNS: announced as {HOSTNAME[:-1]} ({ip})")
    try:
        yield
    finally:
        # Goodbye (TTL 0) before close: closing first drops the packets with the
        # socket, and satellites keep the old address for up to 2 minutes.
        await (await zeroconf.async_unregister_service(info))
        await zeroconf.async_close()


async def find():
    async with AsyncZeroconf() as zeroconf:
        info = AsyncServiceInfo(SERVICE_TYPE, SERVICE_NAME)
        if not await info.async_request(zeroconf.zeroconf, FIND_TIMEOUT_MS):
            return None
        return info.server, info.parsed_addresses(), info.port


def main():
    found = asyncio.run(find())
    if found is None:
        print(f"No Mika server answered within {FIND_TIMEOUT_MS / 1000:.0f} s.")
        return
    server, addresses, port = found
    print(f"Found {server} at {', '.join(addresses)}, port {port}")


if __name__ == "__main__":
    main()
