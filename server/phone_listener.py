"""UDP intake for SignalMap phones, run inside the FastAPI event loop.

``ios/SignalMap/RoomBridge.swift`` already streams each phone's own position,
compass facing and locally recognized gesture as small JSON datagrams, about ten
times a second, to a "Visualizer host" set in the app's lobby. It predates this
server and was pointed at the standalone webcam simulator (``src/phone_feed.py``).

Listening for exactly that datagram here means a phone already in the field
needs no rebuild and no new setting beyond the host it is already asked for: the
same stream now lands in the mission runtime and shows up on the 3D map. The
typed ``POST /api/operators`` route exists alongside it for clients that would
rather speak HTTP, and both funnel into the one :class:`OperatorRegistry`.

The socket is one-way and receive-only. Datagrams are unauthenticated, so this
is bound to the loopback interface unless a host is chosen deliberately, and it
must not be exposed to an untrusted network.
"""

from __future__ import annotations

import asyncio
import logging

from simulation.operators import OperatorRegistry, parse_phone_datagram

LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 9870


class PhoneDatagramProtocol(asyncio.DatagramProtocol):
    """Parses each datagram into the registry, dropping anything malformed."""

    def __init__(self, registry: OperatorRegistry) -> None:
        self.registry = registry
        self.dropped = 0

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        report = parse_phone_datagram(data)
        if report is None:
            self.dropped += 1
            return
        self.registry.ingest(report)

    def error_received(self, exc: Exception) -> None:
        LOGGER.warning("phone feed socket error: %s", exc)


async def start_phone_listener(
    registry: OperatorRegistry, host: str, port: int
) -> asyncio.DatagramTransport:
    """Bind the phone feed. Raises OSError if the port is unavailable."""
    loop = asyncio.get_running_loop()
    transport, _protocol = await loop.create_datagram_endpoint(
        lambda: PhoneDatagramProtocol(registry), local_addr=(host, port)
    )
    LOGGER.info("phone feed listening on %s:%s", host, port)
    return transport
