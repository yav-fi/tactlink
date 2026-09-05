"""Learned, node-local model of communication terrain.

Nothing in this module may read simulator RF truth.  A node only ever sees the
packets that actually arrived, so connectivity is *inferred* from three locally
observable facts:

* which of a peer's sequence numbers showed up (an empirical delivery ratio),
* how long each packet took (``now - timestamp_sent``), and
* where this node believed it was while that happened.

Those samples are folded into a coarse spatial grid keyed by the observer's own
estimated position, which answers "what communication quality have we
historically observed around here?".  Cells are shareable, so a node can learn
about terrain it has never flown - through the same lossy transport as
everything else, which means the map itself can be stale or partitioned.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import floor
from typing import Any, Iterable

from .models import Vector3

# Latency the estimator treats as "free"; anything beyond it erodes quality.
_NOMINAL_LATENCY = 0.05
_LATENCY_SCALE = 0.8
# Remote hearsay counts, but never as much as something we measured ourselves.
_REMOTE_WEIGHT = 0.6


def cell_key(position: Vector3, cell_size: float) -> str:
    return f"{int(floor(position.x / cell_size))}:{int(floor(position.y / cell_size))}"


def cell_center(key: str, cell_size: float) -> Vector3:
    ix, iy = (int(part) for part in key.split(":"))
    return Vector3(x=(ix + 0.5) * cell_size, y=(iy + 0.5) * cell_size, z=0.0)


def quality_from(success_rate: float, latency: float) -> float:
    """Deterministic scalar link-quality estimate from observable evidence."""

    penalty = 1.0 / (1.0 + max(0.0, latency - _NOMINAL_LATENCY) / _LATENCY_SCALE)
    return max(0.0, min(1.0, success_rate * penalty))


@dataclass
class PeerWindow:
    """Rolling arrival evidence for one peer, used to estimate its link."""

    peer_id: str
    first_sequence: int = 0
    last_sequence: int = 0
    received: int = 0
    latency_total: float = 0.0
    latency_samples: int = 0
    last_arrival: float = 0.0

    def record(self, sequence: int, sent_at: float, now: float) -> None:
        if self.received == 0:
            self.first_sequence = sequence
            self.last_sequence = sequence
        elif sequence < self.first_sequence or sequence < self.last_sequence - 200:
            # The peer restarted its counter; start a fresh window.
            self.first_sequence = sequence
            self.last_sequence = sequence
            self.received = 0
            self.latency_total = 0.0
            self.latency_samples = 0
        self.last_sequence = max(self.last_sequence, sequence)
        self.received += 1
        latency = max(0.0, now - sent_at)
        self.latency_total += latency
        self.latency_samples += 1
        self.last_arrival = now

    @property
    def expected(self) -> int:
        return max(self.received, self.last_sequence - self.first_sequence + 1)

    @property
    def success_rate(self) -> float:
        return min(1.0, self.received / self.expected) if self.expected else 0.0

    @property
    def mean_latency(self) -> float:
        return self.latency_total / self.latency_samples if self.latency_samples else 0.0

    def reset(self) -> None:
        self.first_sequence = self.last_sequence
        self.received = 0
        self.latency_total = 0.0
        self.latency_samples = 0


@dataclass
class CommunicationCell:
    """Decayed aggregate of everything observed while inside one grid cell."""

    key: str
    quality: float = 0.0
    latency: float = 0.0
    success_rate: float = 0.0
    weight: float = 0.0
    samples: int = 0
    updated_at: float = 0.0
    sources: list[str] = field(default_factory=list)

    def blend(
        self,
        quality: float,
        latency: float,
        success_rate: float,
        weight: float,
        now: float,
        half_life: float,
        source: str,
    ) -> None:
        decayed = self.weight * 0.5 ** (max(0.0, now - self.updated_at) / half_life) if self.weight else 0.0
        total = decayed + weight
        if total <= 0.0:
            return
        self.quality = (self.quality * decayed + quality * weight) / total
        self.latency = (self.latency * decayed + latency * weight) / total
        self.success_rate = (self.success_rate * decayed + success_rate * weight) / total
        self.weight = total
        self.samples += 1
        self.updated_at = now
        if source not in self.sources:
            self.sources.append(source)
            self.sources.sort()

    def to_record(self) -> dict[str, Any]:
        return {
            "cell": self.key,
            "quality": round(self.quality, 5),
            "latency": round(self.latency, 5),
            "success_rate": round(self.success_rate, 5),
            "weight": round(self.weight, 5),
            "samples": self.samples,
            "updated_at": round(self.updated_at, 4),
            "sources": list(self.sources),
        }


class CommunicationBelief:
    """One node's empirical connectivity map. No simulator state, ever."""

    def __init__(
        self,
        owner_id: str,
        cell_size: float = 40.0,
        half_life: float = 45.0,
        minimum_samples: int = 2,
    ) -> None:
        self.owner_id = owner_id
        self.cell_size = cell_size
        self.half_life = half_life
        self.minimum_samples = minimum_samples
        self.cells: dict[str, CommunicationCell] = {}
        self.peers: dict[str, PeerWindow] = {}
        self.peer_quality: dict[str, float] = {}
        self.last_sampled_at: float = 0.0
        self.observed_samples = 0
        self.merged_samples = 0

    # -- learning ------------------------------------------------------------

    def observe_reception(self, peer_id: str, sequence: int, sent_at: float, now: float) -> None:
        """Fold one *actually delivered* packet into the peer's arrival window."""

        if peer_id == self.owner_id or sequence <= 0:
            return
        window = self.peers.get(peer_id)
        if window is None:
            window = PeerWindow(peer_id=peer_id)
            self.peers[peer_id] = window
        window.record(sequence, sent_at, now)

    def observe_silence(self, peer_id: str, now: float) -> None:
        """A peer we used to hear has gone quiet; that is evidence too."""

        window = self.peers.get(peer_id)
        if window is None:
            return
        self.peer_quality[peer_id] = 0.0
        window.last_arrival = now

    def sample(self, position: Vector3, now: float) -> list[dict[str, Any]]:
        """Close the current observation window into cell + per-peer estimates."""

        key = cell_key(position, self.cell_size)
        updated: list[dict[str, Any]] = []
        for peer_id in sorted(self.peers):
            window = self.peers[peer_id]
            if window.latency_samples < self.minimum_samples:
                continue
            success = window.success_rate
            latency = window.mean_latency
            quality = quality_from(success, latency)
            self.peer_quality[peer_id] = round(quality, 5)
            cell = self.cells.get(key)
            if cell is None:
                cell = CommunicationCell(key=key)
                self.cells[key] = cell
            cell.blend(quality, latency, success, 1.0, now, self.half_life, self.owner_id)
            self.observed_samples += 1
            updated.append(cell.to_record())
            window.reset()
        self.last_sampled_at = now
        return updated

    def merge(self, records: Iterable[dict[str, Any]], now: float) -> int:
        """Incorporate connectivity cells learned by another node."""

        merged = 0
        for record in records:
            key = str(record.get("cell", ""))
            if not key or ":" not in key:
                continue
            source = str(record.get("source", record.get("sources", [""])[0] if record.get("sources") else ""))
            if source == self.owner_id:
                continue
            cell = self.cells.get(key)
            if cell is None:
                cell = CommunicationCell(key=key)
                self.cells[key] = cell
            observed_at = float(record.get("updated_at", now))
            if observed_at <= cell.updated_at and source in cell.sources:
                continue
            cell.blend(
                float(record.get("quality", 0.0)),
                float(record.get("latency", 0.0)),
                float(record.get("success_rate", 0.0)),
                _REMOTE_WEIGHT * max(0.25, min(2.0, float(record.get("weight", 1.0)))),
                now,
                self.half_life,
                source or "peer",
            )
            merged += 1
            self.merged_samples += 1
        return merged

    # -- querying ------------------------------------------------------------

    def predicted_quality(self, point: Vector3, now: float | None = None) -> float | None:
        """Inverse-distance estimate of link quality around ``point``.

        Returns ``None`` when the node has simply never learned anything nearby;
        callers must then fall back to distance-agnostic behaviour rather than
        inventing a number.
        """

        if not self.cells:
            return None
        radius = self.cell_size * 2.5
        numerator = 0.0
        denominator = 0.0
        for cell in self.cells.values():
            center = cell_center(cell.key, self.cell_size)
            distance = ((center.x - point.x) ** 2 + (center.y - point.y) ** 2) ** 0.5
            if distance > radius:
                continue
            recency = (
                0.5 ** (max(0.0, now - cell.updated_at) / self.half_life) if now is not None else 1.0
            )
            weight = cell.weight * recency / (1.0 + distance / self.cell_size)
            if weight <= 1e-9:
                continue
            numerator += cell.quality * weight
            denominator += weight
        if denominator <= 1e-9:
            return None
        return max(0.0, min(1.0, numerator / denominator))

    def confidence(self, point: Vector3) -> float:
        """How much evidence backs :meth:`predicted_quality` at ``point``."""

        radius = self.cell_size * 2.5
        weight = sum(
            cell.weight
            for cell in self.cells.values()
            if (
                (cell_center(cell.key, self.cell_size).x - point.x) ** 2
                + (cell_center(cell.key, self.cell_size).y - point.y) ** 2
            )
            ** 0.5
            <= radius
        )
        return max(0.0, min(1.0, weight / 6.0))

    def route_quality(self, points: list[Vector3], now: float | None = None) -> tuple[float, float]:
        """Worst-case and mean predicted quality along a polyline.

        The bottleneck matters more than the average for connectivity: a route
        is only as connected as its weakest leg.
        """

        estimates = [value for value in (self.predicted_quality(point, now) for point in points) if value is not None]
        if not estimates:
            return (0.0, 0.0)
        return (min(estimates), sum(estimates) / len(estimates))

    def mean_peer_quality(self) -> float:
        values = [value for value in self.peer_quality.values()]
        return sum(values) / len(values) if values else 1.0

    def weakest_peer(self) -> tuple[str, float] | None:
        if not self.peer_quality:
            return None
        peer_id = min(sorted(self.peer_quality), key=lambda key: self.peer_quality[key])
        return (peer_id, self.peer_quality[peer_id])

    # -- sharing -------------------------------------------------------------

    def export(self, limit: int = 24) -> list[dict[str, Any]]:
        """Most-informative cells, newest first, for gossip through the network."""

        ranked = sorted(
            self.cells.values(),
            key=lambda cell: (-cell.updated_at, cell.key),
        )[:limit]
        records = []
        for cell in ranked:
            record = cell.to_record()
            record["source"] = self.owner_id
            records.append(record)
        return records

    def snapshot(self) -> list[dict[str, Any]]:
        return [self.cells[key].to_record() for key in sorted(self.cells)]
