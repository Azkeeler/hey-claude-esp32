import json

from aiohttp.test_utils import TestClient, TestServer

from asistente.config import Config, Proyecto
from asistente.http import crear_app
from asistente.sesion import Servicios


class ConvFalsa:
    async def cerrar_todo(self):
        pass


def app():
    cfg = Config(
        host_publico="192.168.1.10", puerto=8765, token="secreto", dispositivos_permitidos=["aa:bb:cc:00:00:01"],
        proyectos=[Proyecto("a", "/")],
    )
    return crear_app(Servicios(cfg, None, None, ConvFalsa()))  # type: ignore[arg-type]


async def test_ota():
    async with TestClient(TestServer(app())) as c:
        r = await c.post(
            "/xiaozhi/ota/",
            headers={"Device-Id": "aa:bb:cc:00:00:01", "Client-Id": "x"},
            data=json.dumps({"application": {"version": "2.2.5"}}),
        )
        assert r.status == 200
        d = await r.json()
        assert d["websocket"] == {"url": "ws://192.168.1.10:8765/xiaozhi/v1/", "token": "secreto"}
        assert d["firmware"] == {"version": "2.2.5", "url": ""}
        assert "activation" not in d and "mqtt" not in d
        assert d["server_time"]["timestamp"] > 1_700_000_000_000
        assert d["server_time"]["timezone_offset"] in (60, 120)


async def test_ota_sin_cuerpo_y_activate():
    async with TestClient(TestServer(app())) as c:
        r = await c.post("/xiaozhi/ota/")
        assert r.status == 200 and (await r.json())["firmware"]["version"] == ""
        r = await c.post("/xiaozhi/ota/activate")
        assert r.status == 200 and await r.json() == {}
        assert (await c.get("/salud")).status == 200


async def test_websocket_sin_token_o_dispositivo_ajeno():
    async with TestClient(TestServer(app())) as c:
        assert (await c.get("/xiaozhi/v1/", headers={"Device-Id": "aa:bb:cc:00:00:01"})).status == 401
        r = await c.get("/xiaozhi/v1/", headers={"Authorization": "Bearer malo", "Device-Id": "aa:bb:cc:00:00:01"})
        assert r.status == 401
        r = await c.get("/xiaozhi/v1/", headers={"Authorization": "Bearer secreto", "Device-Id": "aa:bb:cc:dd:ee:ff"})
        assert r.status == 403
