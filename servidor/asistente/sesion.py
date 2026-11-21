"""Una conexión WebSocket con el aparato: máquina de estados, VAD, colas y abort."""
from __future__ import annotations

import asyncio
import contextlib
import csv
import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import webrtcvad
from aiohttp import WSMsgType, web

from . import ordenes, sesiones
from .audio import CodificadorOpus, DecodificadorOpus, muestras_por_frame, remuestrear
from .claude import FRASE_ESPERA, FRASE_TIMEOUT, LIMITE_RESPUESTA_S, Conversaciones
from .config import Config
from .frases import Troceador
from .stt import Transcriptor, es_texto_vacio
from .tts import Sintetizador

log = logging.getLogger(__name__)

VAD_MS = 30
VAD_TASA = 16000
FRASE_MAX_S = 30.0
SIN_VOZ_S = 8.0
COLCHON_S = 0.18  # ~3 frames de 60 ms por delante del reloj de reproducción
FRAME_S = 0.06
FRASE_CORTE = "Lo dejo aquí; si quieres más, pídemelo."

CABECERA_CSV = [
    "fecha", "dispositivo", "proyecto", "modelo", "fin_voz_stt_ms", "stt_primer_texto_ms",
    "primer_texto_primer_audio_ms", "total_ms", "chars_pregunta", "chars_respuesta",
]


@dataclass
class Servicios:
    cfg: Config
    stt: Transcriptor
    tts: Sintetizador
    conv: Conversaciones


def _registrar_csv(cfg: Config, fila: list) -> None:
    ruta = cfg.raiz / "logs" / "tiempos.csv"
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        nuevo = not ruta.exists()
        with ruta.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if nuevo:
                w.writerow(CABECERA_CSV)
            w.writerow(fila)
    except OSError as e:
        log.warning("No se pudo escribir tiempos.csv: %s", e)


class Habla:
    """Cola de frases -> TTS -> envío de frames Opus al ritmo real."""

    def __init__(self, sesion: "Sesion") -> None:
        self.s = sesion
        self.q_frases: asyncio.Queue[str | None] = asyncio.Queue()
        self.q_audio: asyncio.Queue[tuple[str, list[bytes]] | None] = asyncio.Queue(maxsize=3)
        self.t_primer_audio: float | None = None
        self.t_fin: float | None = None
        self.caracteres = 0
        self.hubo_audio = False
        self._cod = CodificadorOpus()
        self._t_sint = asyncio.create_task(self._sintetizar())
        self._t_env = asyncio.create_task(self._enviar())

    async def frase(self, texto: str) -> None:
        self.caracteres += len(texto)
        await self.q_frases.put(texto)

    async def _sintetizar(self) -> None:
        try:
            while (texto := await self.q_frases.get()) is not None:
                pcm = await self.s.sv.tts.sintetizar(texto)
                if len(pcm):
                    await self.q_audio.put((texto, self._cod.codificar(pcm)))
            # El fin de la cola se espera: con put_nowait se perdía si la cola estaba llena y el turno no acababa.
            await self.q_audio.put(None)
        except BaseException:
            with contextlib.suppress(asyncio.QueueFull):
                self.q_audio.put_nowait(None)
            raise

    async def _enviar(self) -> None:
        fin_reproduccion = 0.0
        while (item := await self.q_audio.get()) is not None:
            texto, frames = item
            if not self.hubo_audio:
                self.hubo_audio = True
                await self.s.enviar_json({"type": "tts", "state": "start"})
                self.s.tts_activo = True
            await self.s.enviar_json({"type": "tts", "state": "sentence_start", "text": texto})
            for f in frames:
                ahora = time.monotonic()
                espera = fin_reproduccion - ahora - COLCHON_S
                if espera > 0:
                    await asyncio.sleep(espera)
                    ahora = time.monotonic()
                await self.s.ws.send_bytes(f)
                if self.t_primer_audio is None:
                    self.t_primer_audio = ahora
                fin_reproduccion = max(fin_reproduccion, ahora) + FRAME_S
        # Esperar a que el aparato acabe de reproducir antes de cerrar con tts stop.
        resto = fin_reproduccion - time.monotonic()
        if resto > 0:
            await asyncio.sleep(resto)
        self.t_fin = time.monotonic()

    async def terminar(self) -> None:
        """Espera a que se hable todo lo encolado."""
        await self.q_frases.put(None)
        await self._t_sint
        await self._t_env

    async def cancelar(self) -> None:
        for t in (self._t_sint, self._t_env):
            t.cancel()
        for t in (self._t_sint, self._t_env):
            with contextlib.suppress(BaseException):
                await t


class Sesion:
    def __init__(self, ws: web.WebSocketResponse, sv: Servicios, device_id: str, client_id: str) -> None:
        self.ws = ws
        self.sv = sv
        self.cfg = sv.cfg
        self.device_id = device_id
        self.client_id = client_id
        self.session_id = str(uuid.uuid4())
        self.tasa_subida = 16000
        self.frame_ms = 60
        self.dec: DecodificadorOpus | None = None
        self._lock_envio = asyncio.Lock()
        # Escucha
        self.escuchando = False
        self.modo = "auto"
        self._reiniciar_vad()
        self._t_sin_voz: asyncio.Task | None = None
        self._plazo_reposo = 0.0  # time.monotonic() en que, si no ha habido una frase válida, se va a reposo
        # Respuesta
        self.tarea: asyncio.Task | None = None
        self.tts_activo = False
        self.cerrar_pedido = False

    # ---------- envío ----------
    async def enviar_json(self, datos: dict) -> None:
        datos = {"session_id": self.session_id, **datos}
        async with self._lock_envio:
            if not self.ws.closed:
                await self.ws.send_str(json.dumps(datos, ensure_ascii=False))

    # ---------- VAD ----------
    def _reiniciar_vad(self) -> None:
        self.vad = webrtcvad.Vad(self.cfg.vad_agresividad)
        self.pendiente = np.zeros(0, dtype=np.int16)  # PCM a 16 kHz aún sin pasar por el VAD
        self.pcm: list[np.ndarray] = []
        self.voz_ms = 0
        self.silencio_ms = 0
        self.total_ms = 0
        self.hay_voz = False  # ya se ha oído voz (aunque sea poca)
        self.t_inicio_voz: float | None = None

    def _armar_escucha(self, modo: str | None = None, nuevo_plazo: bool = True) -> None:
        """Empieza a capturar una frase. `nuevo_plazo=False` conserva el plazo de reposo que ya corría."""
        if modo:
            self.modo = modo
        self._reiniciar_vad()
        self.escuchando = True
        if nuevo_plazo:
            self._plazo_reposo = time.monotonic() + SIN_VOZ_S
        self._cancelar_sin_voz()
        self._t_sin_voz = asyncio.create_task(self._vigilar_sin_voz())

    def _cancelar_sin_voz(self) -> None:
        if self._t_sin_voz and self._t_sin_voz is not asyncio.current_task():
            self._t_sin_voz.cancel()
        self._t_sin_voz = None

    async def _vigilar_sin_voz(self) -> None:
        await asyncio.sleep(max(0.0, self._plazo_reposo - time.monotonic()))
        # Si hay una frase a medias con voz suficiente se deja acabar: al cerrarse decide _seguir_o_reposo().
        if self.escuchando and self.voz_ms < self.cfg.voz_minima_ms and self.tarea is None:
            await self._reposo("sin voz en %.0f s" % SIN_VOZ_S)

    async def _seguir_o_reposo(self) -> None:
        """Tras una frase que no valía (ruido, STT vacío): sigue escuchando sin alargar el plazo, o a reposo."""
        if time.monotonic() >= self._plazo_reposo:
            await self._reposo("sin una frase válida en %.0f s" % SIN_VOZ_S)
        else:
            self._armar_escucha(nuevo_plazo=False)

    async def _reposo(self, motivo: str) -> None:
        """Manda el aparato a reposo.

        En application.cc, `tts stop` solo saca al aparato de «Speaking»; en modo auto lo devuelve a «Listening»
        (se quedaría escuchando para siempre). Lo que lo lleva a reposo (Idle) es cerrar el canal de audio, así
        que se manda `tts stop` y se cierra el WebSocket; con la palabra de activación vuelve a abrirse.
        """
        log.info("[%s] reposo: %s", self.device_id, motivo)
        self.escuchando = False
        with contextlib.suppress(Exception):
            await self.enviar_json({"type": "tts", "state": "stop"})
        self.cerrar_pedido = True
        with contextlib.suppress(Exception):
            await self.ws.close()

    def _alimentar_vad(self, pcm: np.ndarray) -> bool:
        """Procesa audio de 16 kHz. Devuelve True si hay que cerrar la frase."""
        self.pcm.append(pcm)
        self.pendiente = np.concatenate([self.pendiente, pcm])
        paso = muestras_por_frame(VAD_TASA, VAD_MS)
        cerrar = False
        while len(self.pendiente) >= paso and not cerrar:
            trozo, self.pendiente = self.pendiente[:paso], self.pendiente[paso:]
            self.total_ms += VAD_MS
            if self.modo == "manual":
                if self.total_ms >= FRASE_MAX_S * 1000:
                    cerrar = True
                continue
            if self.vad.is_speech(trozo.tobytes(), VAD_TASA):
                if not self.hay_voz:
                    self.t_inicio_voz = time.monotonic()
                self.hay_voz = True
                self.voz_ms += VAD_MS
                self.silencio_ms = 0
            elif self.hay_voz:
                self.silencio_ms += VAD_MS
            if self.hay_voz and self.silencio_ms >= self.cfg.silencio_fin_ms:
                cerrar = True
            elif self.total_ms >= FRASE_MAX_S * 1000 and self.hay_voz:
                cerrar = True
        return cerrar

    async def _frase_cerrada(self) -> None:
        """Termina la captura: lanza el turno, o descarta si hubo muy poca voz y sigue escuchando."""
        pcm = np.concatenate(self.pcm) if self.pcm else np.zeros(0, dtype=np.int16)
        voz_ms = self.voz_ms
        manual = self.modo == "manual"
        self.escuchando = False
        self._cancelar_sin_voz()
        if not manual and voz_ms < self.cfg.voz_minima_ms:
            log.debug("[%s] frase descartada (%d ms de voz)", self.device_id, voz_ms)
            await self._seguir_o_reposo()
            return
        if len(pcm) < muestras_por_frame(VAD_TASA, 200):
            await self._seguir_o_reposo()
            return
        self.tarea = asyncio.create_task(self._turno(pcm, time.monotonic()))
        self.tarea.add_done_callback(self._turno_acabado)

    def _turno_acabado(self, t: asyncio.Task) -> None:
        if self.tarea is t:
            self.tarea = None
        if not t.cancelled() and t.exception():
            log.error("[%s] fallo en el turno: %r", self.device_id, t.exception(), exc_info=t.exception())

    # ---------- turno de pregunta/respuesta ----------
    async def _turno(self, pcm: np.ndarray, t_fin_voz: float) -> None:
        conv = self.sv.conv
        proyecto = conv.proyecto_activo(self.device_id).nombre
        habla: Habla | None = None
        troceador = Troceador()
        texto = ""
        chars_resp = 0
        t_stt = t_primer_texto = t_inicio_habla = None
        modelo = self.cfg.claude_modelo
        try:
            pico = int(np.abs(pcm.astype(np.int32)).max()) if len(pcm) else 0
            rms = float(np.sqrt(np.mean(pcm.astype(np.float64) ** 2))) if len(pcm) else 0.0
            log.info(
                "[%s] nivel de la frase: pico %.1f dBFS, RMS %.1f dBFS", self.device_id,
                20 * np.log10(max(pico, 1) / 32768), 20 * np.log10(max(rms, 1.0) / 32768),
            )
            texto = await self.sv.stt.transcribir(pcm)
            t_stt = time.monotonic()
            log.info("[%s] STT (%d ms): %r", self.device_id, (t_stt - t_fin_voz) * 1000, texto)
            if es_texto_vacio(texto):
                await self.enviar_json({"type": "tts", "state": "stop"})
                await self._seguir_o_reposo()
                return
            await self.enviar_json({"type": "stt", "text": texto})

            hay_pendiente = conv.hay_pendiente(self.device_id)
            orden = ordenes.interpretar(texto, self.cfg.proyectos, pendiente=hay_pendiente)
            if hay_pendiente and (orden is None or orden.tipo not in ("confirmar", "negar")):
                conv.descartar_pendiente(self.device_id)
            if orden is not None and orden.tipo == "confirmar":
                mensaje = conv.confirmacion_por_modelo(self.device_id)
                if mensaje:  # lo pendiente es un comando: lo ejecuta Claude, ya aprobado
                    orden, texto = None, mensaje
            if orden is not None and orden.tipo == "parar":
                await self.enviar_json({"type": "tts", "state": "stop"})
                self._armar_escucha()
                return

            habla = Habla(self)
            if orden is not None:
                modelo = "local"
                t_primer_texto = time.monotonic()
                respuesta = await self._ejecutar_orden(orden)
                for fr in Troceador().alimentar(respuesta + "\n"):
                    await habla.frase(fr)
            else:
                cortado = False

                async def decir(fr: str) -> None:
                    # Tope de lo que se lee en voz alta: Claude acaba su turno, pero el resto no se dice.
                    nonlocal cortado
                    if cortado:
                        return
                    if habla.caracteres and habla.caracteres + len(fr) > self.cfg.max_caracteres_hablados:
                        cortado = True
                        await habla.frase(FRASE_CORTE)
                        return
                    await habla.frase(fr)

                async def al_texto(trozo: str) -> None:
                    for fr in troceador.alimentar(trozo):
                        await decir(fr)

                async def al_espera() -> None:
                    await habla.frase(FRASE_ESPERA)

                try:
                    res = await asyncio.wait_for(
                        conv.responder(self.device_id, texto, al_texto, al_espera), LIMITE_RESPUESTA_S
                    )
                    t_primer_texto = res.t_primer_texto
                    chars_resp = res.caracteres
                    for fr in troceador.terminar():
                        await decir(fr)
                    if res.error and not res.caracteres:
                        await habla.frase("Ha habido un problema al consultar a Claude.")
                except asyncio.TimeoutError:
                    log.warning("[%s] respuesta de Claude demasiado larga", self.device_id)
                    await habla.frase(FRASE_TIMEOUT)

            await habla.terminar()
            if habla.hubo_audio:
                await self.enviar_json({"type": "tts", "state": "stop"})
                self.tts_activo = False
            else:
                await self.enviar_json({"type": "tts", "state": "stop"})
                self._armar_escucha()
            t_inicio_habla = habla.t_primer_audio
            self._registrar(t_fin_voz, t_stt, t_primer_texto, habla, proyecto, modelo, texto, chars_resp)
        except asyncio.CancelledError:
            if habla:
                await habla.cancelar()
            raise
        except Exception:
            if habla:
                await habla.cancelar()
            if self.tts_activo:
                with contextlib.suppress(Exception):
                    await self.enviar_json({"type": "tts", "state": "stop"})
                self.tts_activo = False
            elif not self.ws.closed:
                self._armar_escucha()
            raise

    def _registrar(self, t_fin_voz, t_stt, t_primer_texto, habla: Habla, proyecto, modelo, pregunta, chars_resp) -> None:
        def ms(a, b):
            return round((b - a) * 1000) if a is not None and b is not None else ""

        t_audio, t_fin = habla.t_primer_audio, habla.t_fin
        fase = (ms(t_fin_voz, t_stt), ms(t_stt, t_primer_texto), ms(t_primer_texto, t_audio), ms(t_fin_voz, t_fin))
        log.info(
            "[%s] tiempos ms: fin_voz→stt=%s stt→primer_texto=%s primer_texto→primer_audio=%s total=%s",
            self.device_id, *fase,
        )
        fila = [
            datetime.now(timezone.utc).isoformat(timespec="seconds"), self.device_id, proyecto, modelo,
            *fase, len(pregunta), chars_resp or habla.caracteres,
        ]
        _registrar_csv(self.cfg, fila)

    async def _ejecutar_orden(self, orden: ordenes.Orden) -> str:
        conv = self.sv.conv
        nombres = ", ".join(p.nombre for p in self.cfg.proyectos)
        if orden.tipo == "proyecto":
            await conv.cambiar_proyecto(self.device_id, orden.proyecto)
            return f"Vale, ahora estoy en {orden.proyecto}."
        if orden.tipo == "proyecto_desconocido":
            return f"No conozco ese proyecto. Tengo estos: {nombres}."
        if orden.tipo == "nueva":
            await conv.nueva(self.device_id)
            return "Vale, empezamos de cero."
        if orden.tipo == "donde":
            info = conv.sesion_activa(self.device_id)
            if info is not None:
                return f"Estoy en la sesión {sesiones.titulo_hablado(info)}."
            return f"Estoy en el proyecto {conv.proyecto_activo(self.device_id).nombre}."
        if orden.tipo == "confirmar":
            return await conv.confirmar(self.device_id)
        if orden.tipo == "negar":
            return "Vale, no lo hago."
        if orden.tipo == "sesiones":
            lista = await asyncio.to_thread(sesiones.listar)
            if not lista:
                return "No encuentro sesiones de Claude Code."
            conv.guardar_lista(self.device_id, lista[:5])
            numeros = ("Uno", "Dos", "Tres", "Cuatro", "Cinco")
            partes = [f"{numeros[i]}: {sesiones.titulo_hablado(s)}." for i, s in enumerate(lista[:5])]
            return " ".join(partes) + " Di: ve a la sesión, y el número."
        if orden.tipo == "sesion":
            lista = await asyncio.to_thread(sesiones.listar)
            info = sesiones.elegir(lista, orden.pedido or "", conv.ultima_lista(self.device_id))
            if info is None:
                return "No encuentro esa sesión. Di: lista las sesiones."
            await conv.ir_a_sesion(self.device_id, info)
            return f"Vale, estoy en la sesión {sesiones.titulo_hablado(info)}."
        return ""

    # ---------- abort ----------
    async def abortar(self, motivo: str) -> None:
        log.info("[%s] abort (%s)", self.device_id, motivo)
        t, self.tarea = self.tarea, None
        habia_turno = t is not None and not t.done()
        if habia_turno:
            t.cancel()
            with contextlib.suppress(BaseException):
                await t
        if habia_turno or self.tts_activo:
            with contextlib.suppress(Exception):
                await self.enviar_json({"type": "tts", "state": "stop"})
        self.tts_activo = False

    # ---------- mensajes ----------
    async def _hello(self, msg: dict) -> None:
        ap = msg.get("audio_params") or {}
        self.tasa_subida = int(ap.get("sample_rate", 16000))
        self.frame_ms = int(ap.get("frame_duration", 60))
        self.dec = DecodificadorOpus(self.tasa_subida, self.frame_ms)
        log.info("[%s] hello: Opus %d Hz, %d ms, version=%s", self.device_id, self.tasa_subida, self.frame_ms, msg.get("version"))
        await self.enviar_json(
            {
                "type": "hello",
                "transport": "websocket",
                "audio_params": {"format": "opus", "sample_rate": 24000, "channels": 1, "frame_duration": 60},
            }
        )

    async def _listen(self, msg: dict) -> None:
        estado = msg.get("state")
        if estado == "detect":
            log.info("[%s] palabra de activación: %r", self.device_id, msg.get("text"))
            if self.tarea is not None or self.tts_activo:
                await self.abortar("palabra de activación durante la respuesta")
        elif estado == "start":
            modo = msg.get("mode", "auto")
            modo = "manual" if modo == "manual" else "auto"  # «realtime» se trata como «auto»
            if self.tarea is not None:
                await self.abortar("listen start durante la respuesta")
            log.info("[%s] escuchando (modo %s)", self.device_id, modo)
            self._armar_escucha(modo)
        elif estado == "stop":
            if self.escuchando and self.modo == "manual":
                await self._frase_cerrada()
            else:
                self.escuchando = False
                self._cancelar_sin_voz()

    async def _binario(self, datos: bytes) -> None:
        if not self.escuchando or self.dec is None or self.tarea is not None:
            return
        try:
            pcm = self.dec.decodificar(datos)
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] frame Opus inválido: %s", self.device_id, e)
            return
        if self.tasa_subida != VAD_TASA:
            pcm = remuestrear(pcm, self.tasa_subida, VAD_TASA)
        if self._alimentar_vad(pcm):
            await self._frase_cerrada()

    async def atender(self) -> None:
        try:
            async for m in self.ws:
                if m.type == WSMsgType.BINARY:
                    await self._binario(m.data)
                elif m.type == WSMsgType.TEXT:
                    try:
                        msg = json.loads(m.data)
                    except ValueError:
                        log.warning("[%s] JSON inválido: %r", self.device_id, m.data[:100])
                        continue
                    tipo = msg.get("type")
                    if tipo == "hello":
                        await self._hello(msg)
                    elif tipo == "listen":
                        await self._listen(msg)
                    elif tipo == "abort":
                        await self.abortar(str(msg.get("reason")))
                    else:
                        log.info("[%s] mensaje ignorado (%s): %s", self.device_id, tipo, str(m.data)[:200])
                elif m.type == WSMsgType.ERROR:
                    log.warning("[%s] error de WebSocket: %s", self.device_id, self.ws.exception())
        finally:
            self.escuchando = False
            self._cancelar_sin_voz()
            t, self.tarea = self.tarea, None
            if t is not None and not t.done():
                t.cancel()
                with contextlib.suppress(BaseException):
                    await t
            log.info("[%s] conexión cerrada", self.device_id)
