# dnhacks26

Start by cloning the repository:

```sh
git clone https://github.com/yav-fi/dnhacks26.git
```

Yavin added `AGENTS.md` and `CLAUDE.md` to keep coding-agent instructions consistent across tools.

## Distributed mission runtime

This repository includes a deterministic, multi-node autonomous aerial mission simulator. It separates simulator-owned ground truth from each drone's local estimate and routes all peer knowledge through a lossy, delayed network model. It is a software simulation and has not been validated for real-world or safety-critical deployment.

### Setup and run

Python 3.11 or newer is required.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
pytest
uvicorn server.main:app --reload
```

Open <http://127.0.0.1:8000> for the lightweight control console. API documentation is at <http://127.0.0.1:8000/docs>.

Run the accelerated end-to-end scenario with:

```sh
python -m simulation.demo
```

Add `--realtime` to play it at wall-clock speed. The scenario allocates WATCH and SEARCH tasks across four drones, fails Drone 2 at 10 seconds, waits for heartbeat timeout and reassignment, removes GPS from Drone 3, and raises network packet loss.

### Integration contracts

Mission-producing systems (`llms/`, gesture, and UI) submit the typed `MissionCommand` JSON schema to `POST /api/missions`. Environment code can construct `WorldDefinition` with bounds, regions, obstacles, and moving entities. Mobile code can emit the provided `OperatorPositionUpdate` schema. The frontend consumes `SimulationSnapshot`, `SimulationEvent`, `DronePublicState`, `MissionTask`, and `LinkState` from `GET /api/state` or `/ws`.

Important endpoints:

- `GET /api/state`, `/api/drones`, `/api/missions`, `/api/events`
- `POST /api/missions`
- `POST /api/simulation/pause`, `/resume`, `/reset`
- `POST /api/simulation/scenario/{NORMAL|DEGRADED|CONTESTED|CHAOS}`
- `POST /api/interference`, `/api/events/inject`
- `POST /api/drones/{id}/fail`, `/api/drones/{id}/recover`
- WebSocket `/ws` sends JSON state snapshots at the configured publish rate.

The in-process `DroneNode` intentionally has no reference to `World` or `SimulationEngine`. Its inputs are measurements, delivered messages, and typed task assignments; its outputs are messages and `MotionIntent`. Those ports are defined as protocols in `simulation/interfaces.py` so a later process or laptop transport can implement the same boundary.
