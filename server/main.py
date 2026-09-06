"""FastAPI adapter for simulation control and live visualization."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

from simulation.models import (
    AdaptiveRuntimeState,
    DronePublicState,
    InterferenceConfig,
    MissionCommand,
    MissionTask,
    OperatorFrame,
    OperatorState,
    ScenarioEvent,
    SimulationEvent,
    SimulationSnapshot,
)
from simulation.operators import report_from_payload
from simulation.scenarios import ScenarioPreset
from simulation.simulation import SimulationEngine
from simulation.world import WorldDefinition

from .mission_intel import install as install_mission_intel
from .phone_listener import DEFAULT_PORT, start_phone_listener
from .phone_demo import PhoneFeed
from .websocket import WebSocketHub

# How often, in simulated seconds, a registered mission plan is re-evaluated.
PLAN_EVALUATION_SECONDS = 1.0

# The phone feed carries unauthenticated datagrams, so it stays on loopback
# until an operator deliberately opens it to the LAN the phones are on.
PHONE_FEED_HOST = os.environ.get("PHONE_FEED_HOST", "127.0.0.1")


def _default_engine() -> SimulationEngine:
    """Build the demo runtime with enough nodes to include a relay specialist."""
    raw_count = os.environ.get("SIMULATION_DRONE_COUNT", "4")
    try:
        drone_count = int(raw_count)
    except ValueError as exc:
        raise RuntimeError("SIMULATION_DRONE_COUNT must be an integer") from exc
    if not 1 <= drone_count <= 32:
        raise RuntimeError("SIMULATION_DRONE_COUNT must be between 1 and 32")
    return SimulationEngine(drone_count=drone_count)


def create_app(
    engine: SimulationEngine | None = None,
    start_runner: bool = True,
    mission_backend: str = "local-llm",
    phone_feed_port: int | None = None,
) -> FastAPI:
    runtime = engine or _default_engine()
    phone_demo = os.environ.get("PHONE_DEMO", "0") == "1"
    phone_samples = PhoneFeed()
    hub = WebSocketHub()
    if phone_feed_port is None:
        phone_feed_port = int(os.environ.get("PHONE_FEED_PORT", DEFAULT_PORT))

    async def simulation_loop() -> None:
        interval = runtime.config.tick_seconds
        next_plan_evaluation = 0.0
        while True:
            started = asyncio.get_running_loop().time()
            if runtime.running and not phone_demo:
                runtime.tick(interval)
                # Mission plans advance on their own cadence: trigger checks and
                # constraint enforcement do not need to run at the tick rate.
                if runtime.time >= next_plan_evaluation and app.state.mission_session.plans:
                    next_plan_evaluation = runtime.time + PLAN_EVALUATION_SECONDS
                    snapshot = runtime.snapshot()
                    app.state.mission_session.evaluate(
                        snapshot, snapshot.events, runtime.observed_entities()
                    )
            elapsed = asyncio.get_running_loop().time() - started
            await asyncio.sleep(max(0.0, interval - elapsed))

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(simulation_loop()) if start_runner else None
        phones = None
        if start_runner and phone_feed_port > 0:
            try:
                phones = await start_phone_listener(
                    runtime.operators, PHONE_FEED_HOST, phone_feed_port, phone_samples
                )
            except OSError as exc:
                # A busy port must not take the whole runtime down: HTTP ingest
                # via POST /api/operators still works without the UDP feed.
                logging.getLogger(__name__).warning(
                    "phone feed disabled, could not bind %s:%s (%s)",
                    PHONE_FEED_HOST, phone_feed_port, exc,
                )
        app.state.phone_feed = phones
        try:
            yield
        finally:
            if phones is not None:
                phones.close()
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    app = FastAPI(
        title="Distributed Mission Runtime",
        version="0.1.0",
        description="Simulation only; not validated for operational deployment.",
        lifespan=lifespan,
    )
    app.state.engine = runtime
    app.state.phone_samples = phone_samples
    app.state.websocket_hub = hub
    app.state.mission_session = install_mission_intel(app, runtime, mission_backend)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        page = Path(__file__).with_name("static").joinpath("index.html")
        return HTMLResponse(page.read_text(encoding="utf-8"))

    @app.get("/api/state", response_model=SimulationSnapshot)
    async def state() -> SimulationSnapshot:
        return runtime.snapshot()

    @app.get("/api/world", response_model=WorldDefinition)
    async def world() -> WorldDefinition:
        """World bounds, obstacles, regions, and current moving-entity seeds."""
        return runtime.world.definition

    @app.get("/api/drones", response_model=list[DronePublicState])
    async def drones() -> list[DronePublicState]:
        return runtime.snapshot(event_limit=0).drones

    @app.get("/api/missions", response_model=list[MissionTask])
    async def missions() -> list[MissionTask]:
        return list(runtime.missions.tasks.values())

    @app.get("/api/events", response_model=list[SimulationEvent])
    async def events(
        limit: int = Query(default=100, ge=1, le=500),
        after_sequence: int = Query(default=0, ge=0),
    ) -> list[SimulationEvent]:
        return runtime.events.recent(limit, after_sequence)

    @app.post("/api/missions", status_code=201)
    async def create_mission(command: MissionCommand) -> MissionTask:
        try:
            return runtime.submit_mission(command)
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.get("/api/phones")
    async def phones() -> dict:
        return {"phones": phone_samples.snapshot(), "packets": phone_samples.packets,
                "listening": getattr(app.state, "phone_feed", None) is not None,
                "host": PHONE_FEED_HOST, "port": phone_feed_port, "phone_demo": phone_demo}

    @app.get("/api/operators", response_model=list[OperatorState])
    async def operators() -> list[OperatorState]:
        """The people currently on the ground, placed in scene metres."""
        return runtime.operators.states()

    @app.post("/api/operators", status_code=202)
    async def publish_operator(sample: dict) -> dict[str, object]:
        """Accept one phone sample over HTTP.

        The body is the same JSON object `RoomBridge` puts in its datagrams, so
        a phone, a bridge script, or curl can all publish through one shape.
        """
        report = report_from_payload(sample)
        if report is None:
            raise HTTPException(422, "expected at least a string id and a finite pos [x, y]")
        runtime.operators.ingest(report)
        return {"operator_id": report.operator_id, "connected": runtime.operators.connected}

    @app.get("/api/operators/frame", response_model=OperatorFrame)
    async def operator_frame() -> OperatorFrame:
        return runtime.operators.frame

    @app.post("/api/operators/frame", response_model=OperatorFrame)
    async def set_operator_frame(frame: OperatorFrame) -> OperatorFrame:
        """Pin the phone group onto the ground.

        UWB ranging fixes the group's shape but never its absolute position or
        north, so which phone stands where is a declared convention. This is
        where that convention is set.
        """
        return runtime.operators.set_frame(frame)

    @app.post("/api/simulation/pause")
    async def pause() -> dict[str, bool]:
        runtime.pause()
        return {"running": runtime.running}

    @app.post("/api/simulation/resume")
    async def resume() -> dict[str, bool]:
        runtime.resume()
        return {"running": runtime.running}

    @app.post("/api/simulation/reset")
    async def reset() -> SimulationSnapshot:
        runtime.reset()
        return runtime.snapshot()

    @app.post("/api/simulation/scenario/{preset}")
    async def scenario(preset: ScenarioPreset) -> SimulationSnapshot:
        runtime.set_scenario(preset)
        return runtime.snapshot()

    @app.post("/api/interference")
    async def interference(config: InterferenceConfig) -> InterferenceConfig:
        runtime.set_interference(config)
        return runtime.interference.config

    @app.post("/api/events/inject", status_code=202)
    async def inject(event: ScenarioEvent) -> dict[str, str]:
        runtime.inject_event(event)
        return {"event_id": event.id, "status": "applied"}

    @app.post("/api/drones/{node_id}/fail")
    async def fail(node_id: str) -> dict[str, str]:
        try:
            runtime.fail_drone(node_id)
        except KeyError as exc:
            raise HTTPException(404, f"unknown drone: {node_id}") from exc
        return {"node_id": node_id, "status": "offline"}

    @app.post("/api/drones/fail-random")
    async def fail_random() -> dict[str, str]:
        try:
            node_id = runtime.fail_random_drone()
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"node_id": node_id, "status": "offline"}

    @app.post("/api/drones/{node_id}/recover")
    async def recover(node_id: str) -> dict[str, str]:
        try:
            runtime.recover_drone(node_id)
        except KeyError as exc:
            raise HTTPException(404, f"unknown drone: {node_id}") from exc
        return {"node_id": node_id, "status": "online"}

    @app.post("/api/workers/{node_id}/connect")
    async def connect_worker(node_id: str, uri: str = Query(..., pattern=r"^wss?://")) -> dict[str, str]:
        try:
            runtime.attach_external_worker(node_id, uri)
        except KeyError as exc:
            raise HTTPException(404, f"unknown drone: {node_id}") from exc
        except (OSError, RuntimeError) as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"node_id": node_id, "worker": uri, "status": "connected"}

    @app.get("/api/adaptive", response_model=AdaptiveRuntimeState)
    async def adaptive() -> AdaptiveRuntimeState:
        return runtime.snapshot(event_limit=0).adaptive

    @app.get("/api/adaptive/explanations")
    async def explanations(limit: int = Query(default=20, ge=1, le=60)) -> list[dict]:
        return [item.model_dump(mode="json") for item in runtime.explanations.recent(limit)]

    @app.post("/api/edge/connect")
    async def connect_edge(uri: str = Query(..., pattern=r"^wss?://"), node_id: str | None = None) -> dict[str, object]:
        try:
            profile = runtime.connect_edge_worker(uri, node_id)
        except (OSError, RuntimeError) as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"status": "connected", "profile": profile.model_dump(mode="json")}

    @app.post("/api/edge/{node_id}/disconnect")
    async def disconnect_edge(node_id: str) -> dict[str, object]:
        return {"node_id": node_id, "orphaned": runtime.detach_edge_node(node_id)}

    @app.post("/api/control/fail")
    async def fail_control() -> dict[str, str]:
        runtime.fail_control()
        return {"control": "offline", "mission_execution": "peer-to-peer"}

    @app.post("/api/control/recover")
    async def recover_control() -> dict[str, str]:
        runtime.recover_control()
        return {"control": "online"}

    @app.websocket("/ws")
    async def websocket_state(websocket: WebSocket) -> None:
        await hub.connect(websocket)
        interval = 1.0 / runtime.config.snapshot_rate_hz
        try:
            while True:
                await websocket.send_text(runtime.snapshot().model_dump_json())
                await asyncio.sleep(interval)
        except (WebSocketDisconnect, RuntimeError):
            hub.disconnect(websocket)

    return app


app = create_app()
