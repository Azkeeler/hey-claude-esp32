"""Puente con claude-agent-sdk: una conversación por aparato y proyecto."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from pathlib import Path
import warnings
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from claude_agent_sdk import (
    AssistantMessage,
    CanUseToolShadowedWarning,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    StreamEvent,
    TextBlock,
)

from .config import HERRAMIENTAS_PROHIBIDAS, Config, Proyecto
from .equipos import Equipos, ErrorEquipo
from .herramientas import crear_servidor_pc, describir
from .sesiones import SesionInfo

log = logging.getLogger(__name__)

# allowed_tools auto-aprueba lo permitido; can_use_tool solo ve lo demás (y lo deniega). Es lo que se quiere.
warnings.filterwarnings("ignore", category=CanUseToolShadowedWarning)

ESPERA_PRIMER_TEXTO_S = 4.0
LIMITE_RESPUESTA_S = 120.0
FRASE_ESPERA = "Un momento, lo miro."
FRASE_TIMEOUT = "He tardado demasiado; pregúntamelo de otra forma."
PENDIENTE_CADUCA_S = 60.0
MENSAJE_YA_PENDIENTE = (
    "Ya hay una acción esperando el sí de Juan. No llames a más herramientas: pregúntale «¿Lo hago?» y termina."
)
MENSAJE_FALTA_CONFIRMACION = (
    'Falta la confirmación de Juan. Pregúntale "¿Lo hago? Di sí o no" diciendo en pocas palabras qué vas a hacer, '
    "y termina. No vuelvas a llamar a la herramienta: si dice que sí, se ejecuta sola."
)


@dataclass
class Resultado:
    caracteres: int = 0
    t_primer_texto: float | None = None  # time.monotonic()
    error: str | None = None


@dataclass
class Pendiente:
    """Herramienta que espera el «sí» de Juan."""

    herramienta: str
    entrada: dict
    descripcion: str
    t: float


@dataclass
class _Dispositivo:
    proyecto: str
    cliente: ClaudeSDKClient | None = None
    session_id: str | None = None  # para retomar si hubo que reiniciar el cliente tras un abort
    ultima: float = field(default_factory=time.monotonic)
    cliente_clave: tuple[str, str] | None = None  # ("proyecto", nombre) o ("sesion", id) del cliente abierto
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    sesion: SesionInfo | None = None  # sesión de Claude Code retomada
    bifurcar: bool = False  # la primera vez se retoma en una copia
    pendiente: Pendiente | None = None
    nota: str = ""  # se antepone a la siguiente pregunta
    ultima_lista: list[SesionInfo] = field(default_factory=list)
    aprobado: tuple[str, str] | None = None  # (herramienta, clave) que Juan aprobó por voz: vale una sola vez


def _clave_entrada(entrada: dict) -> str:
    """Lo que tiene que coincidir para que una aprobación valga: el comando exacto (Bash) o toda la entrada."""
    if "command" in entrada:
        return str(entrada["command"])
    return json.dumps(entrada, sort_keys=True, ensure_ascii=False)


class Conversaciones:
    def __init__(self, cfg: Config, equipos: Equipos | None = None) -> None:
        self.cfg = cfg
        self._disp: dict[str, _Dispositivo] = {}
        self._servidor_pc = None
        self._funciones: dict = {}
        if equipos is not None:
            self._servidor_pc, self._funciones = crear_servidor_pc(equipos)
        # Usar la suscripción de Claude Code, nunca una clave de API.
        os.environ.pop("ANTHROPIC_API_KEY", None)

    # ---- estado ----
    def _dispositivo(self, device_id: str) -> _Dispositivo:
        d = self._disp.get(device_id)
        if d is None:
            d = _Dispositivo(proyecto=self.cfg.proyecto_por_defecto)
            self._disp[device_id] = d
        return d

    def proyecto_activo(self, device_id: str) -> Proyecto:
        return self.cfg.proyecto(self._dispositivo(device_id).proyecto)  # type: ignore[return-value]

    def sesion_activa(self, device_id: str) -> SesionInfo | None:
        return self._dispositivo(device_id).sesion

    async def cambiar_proyecto(self, device_id: str, nombre: str) -> None:
        d = self._dispositivo(device_id)
        if d.proyecto != nombre or d.sesion is not None:
            d.proyecto = nombre
            await self.nueva(device_id)

    async def nueva(self, device_id: str) -> None:
        """Olvida la conversación (cierra el cliente, sale de la sesión retomada y no retoma nada)."""
        d = self._dispositivo(device_id)
        async with d.lock:
            await self._cerrar(d)
            d.session_id = None
            d.sesion = None
            d.bifurcar = False
            d.pendiente = None

    async def ir_a_sesion(self, device_id: str, info: SesionInfo) -> None:
        """Retoma una sesión de Claude Code (en una copia: la original no se toca)."""
        d = self._dispositivo(device_id)
        async with d.lock:
            await self._cerrar(d)
            d.sesion = info
            d.session_id = None
            d.bifurcar = True
            d.pendiente = None

    def guardar_lista(self, device_id: str, lista: list[SesionInfo]) -> None:
        self._dispositivo(device_id).ultima_lista = lista

    def ultima_lista(self, device_id: str) -> list[SesionInfo]:
        return self._dispositivo(device_id).ultima_lista

    # ---- confirmación por voz ----
    def hay_pendiente(self, device_id: str) -> bool:
        d = self._dispositivo(device_id)
        if d.pendiente is not None and time.monotonic() - d.pendiente.t > PENDIENTE_CADUCA_S:
            d.pendiente = None
        return d.pendiente is not None

    def descartar_pendiente(self, device_id: str) -> None:
        self._dispositivo(device_id).pendiente = None

    def confirmacion_por_modelo(self, device_id: str) -> str | None:
        """Si lo pendiente lo ejecuta el propio Claude (Bash), lo aprueba una vez y devuelve el mensaje para Claude."""
        d = self._dispositivo(device_id)
        p = d.pendiente
        if p is None or p.herramienta in self._funciones:
            return None
        d.pendiente = None
        d.aprobado = (p.herramienta, _clave_entrada(p.entrada))
        log.info("Confirmado por voz (lo ejecuta Claude): %s", p.descripcion)
        orden = p.entrada.get("command")
        exacta = f" Es esta, tal cual: {orden}" if orden else ""
        return (
            f"Juan ha dicho que sí a: {p.descripcion}. Ya está aprobado: ejecuta ahora esa misma orden con la "
            f"misma herramienta, sin cambiar nada.{exacta} Después dile el resultado en una o dos frases."
        )

    async def confirmar(self, device_id: str) -> str:
        """Ejecuta la herramienta pendiente sin pasar por el modelo y deja una nota para su siguiente turno."""
        d = self._dispositivo(device_id)
        p, d.pendiente = d.pendiente, None
        if p is None:
            return "No hay nada pendiente de confirmar."
        try:
            texto = await self._funciones[p.herramienta](p.entrada)
        except ErrorEquipo as e:
            texto = str(e)
        except Exception as e:  # noqa: BLE001
            log.exception("Fallo al ejecutar %s tras la confirmación", p.herramienta)
            texto = f"Ha fallado: {e!r}"
        log.info("Confirmado y ejecutado: %s -> %s", p.descripcion, texto)
        d.nota = (
            f"[Nota del sistema de voz: Juan confirmó y se hizo: {p.descripcion}. Resultado: {texto}]"
        )
        return texto

    async def cerrar_todo(self) -> None:
        for d in self._disp.values():
            await self._cerrar(d)

    @staticmethod
    async def _cerrar(d: _Dispositivo) -> None:
        c, d.cliente = d.cliente, None
        if c is not None:
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(asyncio.shield(c.disconnect()), 10)

    # ---- opciones ----
    def _proyecto_de(self, cwd: str) -> Proyecto:
        """Proyecto cuya carpeta es `cwd` o lo contiene (los worktrees cuelgan de ella); si no, el de por defecto."""
        for p in self.cfg.proyectos:
            carpeta = Path(p.carpeta)
            ruta = Path(cwd)
            if ruta == carpeta or carpeta in ruta.parents:
                return p
        return self.cfg.proyecto(self.cfg.proyecto_por_defecto)  # type: ignore[return-value]

    @staticmethod
    def _clave(d: _Dispositivo) -> tuple[str, str]:
        return ("sesion", d.sesion.id) if d.sesion else ("proyecto", d.proyecto)

    def _opciones(self, d: _Dispositivo) -> ClaudeAgentOptions:
        if d.sesion:
            p = self._proyecto_de(d.sesion.cwd)
            cwd, retomar, bifurcar = d.sesion.cwd, d.session_id or d.sesion.id, d.bifurcar
        else:
            p = self.cfg.proyecto(d.proyecto)
            assert p is not None
            cwd, retomar, bifurcar = p.carpeta, d.session_id, False
        permitidas = list(p.permisos)
        confirmables = list(p.confirmar)
        prohibidas = [t for t in HERRAMIENTAS_PROHIBIDAS if t not in permitidas and t not in confirmables]
        por_defecto = self.cfg.pc_por_defecto or "grande"

        async def puede_usar(nombre: str, entrada: dict, contexto) -> PermissionResultAllow | PermissionResultDeny:
            if nombre in permitidas:
                return PermissionResultAllow()
            if nombre in confirmables and d.aprobado == (nombre, _clave_entrada(entrada)):
                d.aprobado = None
                log.info("Herramienta aprobada por voz: %s %s", nombre, str(entrada)[:300])
                return PermissionResultAllow()
            if nombre in confirmables and d.pendiente is not None:
                # Ya hay una acción esperando el «sí»: no se cambia por otra en el mismo turno.
                return PermissionResultDeny(message=MENSAJE_YA_PENDIENTE)
            if nombre in confirmables:
                d.aprobado = None
                descripcion = describir(nombre, entrada, por_defecto)
                d.pendiente = Pendiente(nombre, dict(entrada), descripcion, time.monotonic())
                log.info("Pendiente de confirmar: %s | %s", descripcion, str(entrada)[:300])
                return PermissionResultDeny(message=MENSAJE_FALTA_CONFIRMACION)
            log.warning("Herramienta denegada: %s", nombre)
            return PermissionResultDeny(message="Por voz no se puede aprobar esta herramienta.")

        return ClaudeAgentOptions(
            cwd=cwd,
            model=self.cfg.claude_modelo,
            allowed_tools=permitidas,
            disallowed_tools=prohibidas,
            permission_mode="default",
            can_use_tool=puede_usar,
            system_prompt={"type": "preset", "preset": "claude_code", "append": self.cfg.prompt_extra},
            setting_sources=["project"],
            include_partial_messages=True,
            resume=retomar,
            fork_session=bifurcar,
            mcp_servers={"pc": self._servidor_pc} if self._servidor_pc is not None else {},
        )

    async def _cliente(self, d: _Dispositivo) -> ClaudeSDKClient:
        clave = self._clave(d)
        if d.cliente is not None and d.cliente_clave == clave:
            return d.cliente
        await self._cerrar(d)
        c = ClaudeSDKClient(self._opciones(d))
        await c.connect()
        d.cliente, d.cliente_clave = c, clave
        return c

    # ---- consulta ----
    async def responder(
        self,
        device_id: str,
        texto: str,
        al_texto: Callable[[str], Awaitable[None]],
        al_espera: Callable[[], Awaitable[None]],
    ) -> Resultado:
        """Envía `texto` a Claude y va pasando a `al_texto` los trozos de texto hablable.

        Si la tarea se cancela (abort) o se pasa del tiempo, la conversación sigue siendo válida.
        """
        d = self._dispositivo(device_id)
        async with d.lock:
            ahora = time.monotonic()
            if d.cliente is not None and ahora - d.ultima > self.cfg.conversacion_caduca_min * 60:
                log.info("Conversación de %s caducada: se empieza otra", device_id)
                await self._cerrar(d)
                d.session_id = None
                d.sesion = None
                d.bifurcar = False
            d.ultima = ahora
            if d.nota:
                texto, d.nota = d.nota + "\n\n" + texto, ""
            res = Resultado()
            visto_delta = False
            primero = asyncio.Event()

            async def espera() -> None:
                try:
                    await asyncio.wait_for(primero.wait(), ESPERA_PRIMER_TEXTO_S)
                except asyncio.TimeoutError:
                    await al_espera()

            tarea_espera = asyncio.create_task(espera())
            cliente: ClaudeSDKClient | None = None
            try:
                cliente = await self._cliente(d)
                await cliente.query(texto)
                async for m in cliente.receive_response():
                    sid = getattr(m, "session_id", None)
                    if sid:
                        d.session_id = sid
                    trozo = ""
                    if isinstance(m, StreamEvent):
                        if m.parent_tool_use_id:
                            continue
                        ev = m.event
                        if ev.get("type") == "content_block_start" and ev.get("content_block", {}).get("type") == "text":
                            trozo = "\n"
                        elif ev.get("type") == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
                            trozo = ev["delta"].get("text", "")
                            visto_delta = True
                    elif isinstance(m, AssistantMessage) and not visto_delta and not m.parent_tool_use_id:
                        trozo = "\n" + "".join(b.text for b in m.content if isinstance(b, TextBlock)) + "\n"
                    elif isinstance(m, ResultMessage):
                        if d.bifurcar and sid:
                            d.bifurcar = False
                            log.info("Sesión bifurcada: la copia es %s", sid)
                        if m.is_error:
                            res.error = "; ".join(m.errors or []) or m.subtype
                            log.error("Claude devolvió error: %s", res.error)
                    if trozo:
                        if trozo.strip():
                            if res.t_primer_texto is None:
                                res.t_primer_texto = time.monotonic()
                                primero.set()
                            res.caracteres += len(trozo.strip())
                        await al_texto(trozo)
            except BaseException:
                # Cancelación (abort), tiempo agotado o fallo: dejar el cliente en un estado limpio.
                await self._recuperar(d, cliente)
                raise
            finally:
                primero.set()
                tarea_espera.cancel()
                with contextlib.suppress(BaseException):
                    await tarea_espera
                d.ultima = time.monotonic()
            return res

    async def _recuperar(self, d: _Dispositivo, cliente: ClaudeSDKClient | None) -> None:
        """Tras un abort: interrumpe y vacía el turno; si no se puede, reinicia el cliente retomando la sesión."""
        if cliente is None:
            return

        async def limpiar() -> None:
            await cliente.interrupt()
            async for m in cliente.receive_response():
                if isinstance(m, ResultMessage):
                    break

        try:
            await asyncio.wait_for(asyncio.shield(limpiar()), 8)
        except BaseException as e:  # noqa: BLE001
            log.warning("No se pudo vaciar el turno (%r): se reinicia el cliente y se retoma la sesión", e)
            await self._cerrar(d)
