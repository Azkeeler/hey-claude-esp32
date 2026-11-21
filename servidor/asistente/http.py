"""Servidor aiohttp: OTA, WebSocket y salud."""
from __future__ import annotations

import hmac
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from aiohttp import web

from .config import Config
from .sesion import Servicios, Sesion

log = logging.getLogger(__name__)

SERVICIOS = web.AppKey("servicios", Servicios)


def _offset_madrid_min() -> int:
    try:
        d = datetime.now(ZoneInfo("Europe/Madrid")).utcoffset()
        return int(d.total_seconds() // 60) if d else 0
    except Exception:  # noqa: BLE001  (sin tzdata)
        return 60


async def ota(request: web.Request) -> web.Response:
    cfg: Config = request.app[SERVICIOS].cfg
    try:
        cuerpo = await request.json()
    except Exception:  # noqa: BLE001
        cuerpo = {}
    version = ((cuerpo.get("application") or {}).get("version")) or ""
    log.info(
        "OTA: Device-Id=%s Client-Id=%s version=%s",
        request.headers.get("Device-Id", "?"), request.headers.get("Client-Id", "?"), version or "?",
    )
    return web.json_response(
        {
            "websocket": {"url": f"ws://{cfg.host_publico}:{cfg.puerto}/xiaozhi/v1/", "token": cfg.token},
            "server_time": {"timestamp": int(time.time() * 1000), "timezone_offset": _offset_madrid_min()},
            "firmware": {"version": version, "url": ""},
        }
    )


async def ota_activar(request: web.Request) -> web.Response:
    return web.json_response({})


async def salud(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def websocket(request: web.Request) -> web.StreamResponse:
    sv = request.app[SERVICIOS]
    cfg = sv.cfg
    cabecera = request.headers.get("Authorization", "")
    esperado = f"Bearer {cfg.token}"
    if not hmac.compare_digest(cabecera.encode(), esperado.encode()):
        log.warning("WebSocket rechazado (token) desde %s", request.remote)
        return web.Response(status=401, text="No autorizado")
    device_id = request.headers.get("Device-Id", "").lower()
    if cfg.dispositivos_permitidos and device_id not in cfg.dispositivos_permitidos:
        log.warning("WebSocket rechazado (dispositivo %r no permitido)", device_id)
        return web.Response(status=403, text="Dispositivo no permitido")
    ws = web.WebSocketResponse(max_msg_size=1024 * 1024)
    await ws.prepare(request)
    log.info("WebSocket abierto: Device-Id=%s Client-Id=%s", device_id, request.headers.get("Client-Id", "?"))
    sesion = Sesion(ws, sv, device_id or "desconocido", request.headers.get("Client-Id", ""))
    await sesion.atender()
    return ws


def crear_app(servicios: Servicios) -> web.Application:
    app = web.Application()
    app[SERVICIOS] = servicios
    app.router.add_post("/xiaozhi/ota/", ota)
    app.router.add_post("/xiaozhi/ota/activate", ota_activar)
    app.router.add_get("/xiaozhi/v1/", websocket)
    app.router.add_get("/salud", salud)

    async def cerrar(app: web.Application) -> None:
        await servicios.conv.cerrar_todo()

    app.on_cleanup.append(cerrar)
    return app
