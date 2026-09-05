"""Run: python -m integrations.flight_bridge (interactive API at /docs)."""

import argparse
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from src.flight_language import MissionError, instruction_lines
from .ardupilot_link import ArduPilotLink
from .flight_path import build_preview, mission_planner_file
from .speech_input import LocalTranscriber, normalize_instruction
from .natural_planner import NaturalPlanner, interpret


class Origin(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    latitude_deg: float = Field(ge=-85, le=85)
    longitude_deg: float = Field(ge=-180, le=180)
    altitude_msl_m: float


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    text: str = Field(min_length=1, max_length=8000)
    origin: Origin | None = None
    sample_spacing_m: float = Field(default=2, ge=0.5, le=10)


class InterpretRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=1, max_length=8000)
    lines: list[str] = Field(max_length=100)
    position: int = Field(ge=0, le=100)


def create_app(endpoint=None, baud=115200, allowed_origins=(), link_factory=ArduPilotLink, transcriber=None, interpreter=None):
    preview = None
    link = None
    link_error = None
    speech = transcriber or LocalTranscriber()
    natural = interpreter or NaturalPlanner()

    async def read_telemetry():
        nonlocal link_error
        while True:
            try:
                link.poll()
            except Exception as error:
                link_error = str(error)
                link.error = link_error
                return
            await asyncio.sleep(0.05)

    @asynccontextmanager
    async def lifespan(app):
        nonlocal link, link_error
        task = None
        if endpoint:
            try:
                link = await asyncio.to_thread(link_factory, endpoint, baud=baud)
                task = asyncio.create_task(read_telemetry())
            except Exception as error:
                link_error = str(error)
        try:
            yield
        finally:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            if link:
                link.close()

    app = FastAPI(title='Flight preview bridge', version='0.1.0', lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=list(allowed_origins),
                       allow_origin_regex=r'https?://(localhost|127\.0\.0\.1)(:\d+)?',
                       allow_methods=['GET', 'POST'], allow_headers=['Content-Type'])

    def telemetry():
        if link:
            return link.snapshot()
        return dict(type='ardupilot_telemetry', connected=False, endpoint=endpoint,
                    error=link_error, position=None, position_stale=True)

    @app.post('/api/preview')
    async def generate(request: PreviewRequest):
        nonlocal preview
        try:
            result = build_preview(request.text,
                                   request.origin.model_dump() if request.origin else None,
                                   request.sample_spacing_m)
        except MissionError as error:
            raise HTTPException(422, str(error)) from error
        preview = result
        return result

    @app.get('/api/preview')
    async def latest():
        if preview is None:
            raise HTTPException(404, 'No preview generated yet.')
        return preview

    @app.get('/api/preview/{mission_id}/mission.waypoints', response_class=PlainTextResponse)
    async def export(mission_id: str):
        if preview is None or preview['mission_id'] != mission_id:
            raise HTTPException(404, 'Preview is no longer current; generate or fetch it again.')
        try:
            content = mission_planner_file(preview)
        except MissionError as error:
            raise HTTPException(422, str(error)) from error
        return PlainTextResponse(content, headers={'Content-Disposition': 'attachment; filename="mission.waypoints"'})

    @app.get('/api/telemetry')
    async def get_telemetry():
        return telemetry()

    @app.get('/api/planner/ai/status')
    async def ai_status():
        return natural.status()

    @app.post('/api/planner/interpret')
    async def interpret_input(request: InterpretRequest):
        try:
            return await asyncio.to_thread(interpret, request.text, request.lines, request.position, natural)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        except RuntimeError as error:
            raise HTTPException(503, str(error)) from error

    @app.post('/api/planner/parse')
    async def parse_input(request: PreviewRequest):
        normalized = normalize_instruction(request.text)
        try:
            return dict(normalized_text=normalized, lines=instruction_lines(normalized))
        except MissionError as error:
            raise HTTPException(422, str(error)) from error

    @app.post('/api/planner/export', response_class=PlainTextResponse)
    async def export_draft(request: PreviewRequest):
        # Validate the exact submitted draft, independent of other browser tabs.
        try:
            result = build_preview(request.text, request.origin.model_dump() if request.origin else None,
                                   request.sample_spacing_m)
            return PlainTextResponse(mission_planner_file(result),
                                     headers={'Content-Disposition': 'attachment; filename="mission.waypoints"'})
        except MissionError as error:
            raise HTTPException(422, str(error)) from error

    @app.get('/api/speech/status')
    async def speech_status():
        return speech.status()

    @app.post('/api/speech/transcribe')
    async def transcribe(request: Request):
        audio = bytearray()
        async for chunk in request.stream():
            audio.extend(chunk)
            if len(audio) > 1100000:
                raise HTTPException(413, 'Recording exceeds the 30-second WAV size limit.')
        try:
            return await asyncio.to_thread(speech.transcribe, bytes(audio))
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        except Exception as error:
            raise HTTPException(503, f'Local transcription unavailable: {error}') from error

    @app.websocket('/ws/flight')
    async def stream(socket: WebSocket):
        await socket.accept()
        try:
            last_mission = None
            while True:
                # Preview is replayed to new clients and sent again only on change.
                if preview and preview['mission_id'] != last_mission:
                    await socket.send_json(preview)
                    last_mission = preview['mission_id']
                await socket.send_json(telemetry())
                await asyncio.sleep(0.2)
        except (WebSocketDisconnect, RuntimeError, OSError):
            pass

    app.mount('/planner', StaticFiles(directory=Path(__file__).with_name('waypoint_ui'), html=True), name='planner')
    return app


def main():
    import uvicorn
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--connect', help='Optional MAVLink endpoint, e.g. udpin:127.0.0.1:14551')
    parser.add_argument('--baud', type=int, default=115200)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--allow-origin', action='append', default=[], help='Additional visualizer web origin')
    args = parser.parse_args()
    uvicorn.run(create_app(args.connect, args.baud, args.allow_origin), host=args.host, port=args.port)


if __name__ == '__main__':
    main()
