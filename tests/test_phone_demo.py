import asyncio
import json
import socket
import time

from fastapi.testclient import TestClient

from server.main import create_app
from server.phone_demo import PhoneFeed
from server.phone_listener import start_phone_listener
from simulation.operators import OperatorRegistry


def packet(identity="one", **extra):
    return json.dumps(dict(id=identity, name=identity, room="test", pos=[1, 2], z=3,
                           gesture="Thumb_Up", gestureConfidence=0.95, flat=False, **extra)).encode()


def test_unpositioned_phone_is_connected_without_invented_geometry():
    feed = PhoneFeed()
    feed.ingest(b'{"id":"one","room":"test","members":3,"flat":true}')
    [phone] = feed.snapshot()
    assert phone["pos"] is None
    assert phone["members"] == 3
    assert phone["flat"]
    assert phone["gesture"] == "None"


def test_feed_ages_both_packets_and_geometry_and_expires():
    clock = [0]
    feed = PhoneFeed(clock=lambda: clock[0])
    feed.ingest(packet(geometryAge=2))
    clock[0] = 1.6
    assert feed.snapshot()[0]["age"] == 1.6
    assert feed.snapshot()[0]["geometryAge"] == 3.6
    clock[0] = 11
    assert feed.snapshot() == []


def test_invalid_udp_packets_do_not_break_receiver():
    feed = PhoneFeed()
    for payload in [b'[]', b'null', b'bad', b'\xff', b'{"id":"one"}', b'x' * 9000]:
        feed.ingest(payload)
    assert feed.snapshot() == []


def test_five_real_udp_streams_reach_browser_contract_then_three_phone_mode():
    async def run():
        feed = PhoneFeed()
        transport = await start_phone_listener(OperatorRegistry(), "127.0.0.1", 0, feed)
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            endpoint = transport.get_extra_info("sockname")
            for index in range(5):
                sender.sendto(packet(str(index)), endpoint)
            for _ in range(100):
                if len(feed.snapshot()) == 5:
                    break
                await asyncio.sleep(0.01)
            assert len(feed.snapshot()) == 5
            assert all(phone["pos"] == [1, 2] and phone["z"] == 3 for phone in feed.snapshot())
            for index in range(3):
                sender.sendto(json.dumps(dict(id=str(index), room="flat-room", pos=[index, 0], z=0,
                                              flat=True, members=3)).encode(), endpoint)
            for _ in range(100):
                flat = [phone for phone in feed.snapshot() if phone["flat"]]
                if len(flat) == 3:
                    break
                await asyncio.sleep(0.01)
            assert len(flat) == 3
            assert all(phone["room"] == "flat-room" and phone["z"] == 0 for phone in flat)
        finally:
            sender.close()
            transport.close()
    asyncio.run(run())


def test_phone_demo_endpoint_does_not_tick_a_second_drone(monkeypatch):
    monkeypatch.setenv("PHONE_DEMO", "1")
    app = create_app(start_runner=True, phone_feed_port=0)
    app.state.phone_samples.ingest(packet())
    with TestClient(app) as client:
        time.sleep(0.3)
        response = client.get("/api/phones")
        assert response.status_code == 200
        data = response.json()
        assert data["phone_demo"] is True
        assert data["listening"] is False
        assert len(data["phones"]) == 1
        assert app.state.engine.time == 0
