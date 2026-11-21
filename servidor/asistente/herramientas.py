"""Herramientas MCP (servidor «pc») para que Claude controle los ordenadores de Juan."""
from __future__ import annotations

import logging
import urllib.parse
from collections.abc import Awaitable, Callable

from claude_agent_sdk import McpSdkServerConfig, create_sdk_mcp_server, tool

from .equipos import Equipos, ErrorEquipo, buscar_youtube, normalizar_nombre, url_valida

log = logging.getLogger(__name__)

_EQUIPO = {"type": "string", "description": '"grande" o "mini"; si se omite, el de por defecto'}


def _esquema(propiedades: dict, obligatorias: list[str]) -> dict:
    return {"type": "object", "properties": propiedades, "required": obligatorias}


def _equipo_hablado(equipo: str | None, por_defecto: str = "grande") -> str:
    n = normalizar_nombre(equipo or por_defecto)
    if "mini" in n or "pequeno" in n or "servidor" in n:
        return "el mini PC"
    return "el PC grande"


def describir(nombre_completo: str, entrada: dict, por_defecto: str = "grande") -> str:
    """Frase hablada de lo que haría una herramienta con confirmación («cerrar Chrome en el PC grande»)."""
    if nombre_completo == "Bash":
        que = str(entrada.get("description") or entrada.get("command") or "").strip()
        return "ejecutar un comando: " + " ".join(que.split()[:20])
    corto = nombre_completo.rsplit("__", 1)[-1]
    donde = _equipo_hablado(entrada.get("equipo"), por_defecto)
    if corto == "cerrar_app":
        return f"cerrar {entrada.get('nombre', 'la aplicación')} en {donde}"
    if corto == "apagar_equipo":
        accion = "suspender" if entrada.get("modo") == "suspender" else "apagar"
        return f"{accion} {donde}"
    if corto == "abrir_app":
        return f"abrir {entrada.get('nombre', 'la aplicación')} en {donde}"
    return f"{corto.replace('_', ' ')} en {donde}"


def _respuesta(texto: str, error: bool = False) -> dict:
    r: dict = {"content": [{"type": "text", "text": texto}]}
    if error:
        r["is_error"] = True
    return r


def crear_servidor_pc(equipos: Equipos) -> tuple[McpSdkServerConfig, dict[str, Callable[[dict], Awaitable[str]]]]:
    """Crea el servidor MCP «pc» y el diccionario `mcp__pc__x -> función(entrada)` (para ejecutar sin el modelo)."""
    funciones: dict[str, Callable[[dict], Awaitable[str]]] = {}
    herramientas = []

    def registrar(nombre: str, descripcion: str, esquema: dict):
        def deco(fn: Callable[[dict], Awaitable[str]]):
            funciones[f"mcp__pc__{nombre}"] = fn

            @tool(nombre, descripcion, esquema)
            async def envoltura(args: dict) -> dict:
                try:
                    return _respuesta(await fn(args))
                except ErrorEquipo as e:
                    return _respuesta(str(e), error=True)
                except Exception as e:  # noqa: BLE001
                    log.exception("Fallo en la herramienta %s", nombre)
                    return _respuesta(f"Ha fallado: {e!r}", error=True)

            herramientas.append(envoltura)
            return fn

        return deco

    @registrar("estado_equipos", "Dice qué equipos hay y cuáles están encendidos.", _esquema({}, []))
    async def estado_equipos(entrada: dict) -> str:
        partes = []
        for e in equipos.todos():
            partes.append(f"{e.hablado}: {'encendido' if await e.encendido() else 'apagado'}")
        por_defecto = equipos.resolver(None).hablado
        return "; ".join(partes) + f". Por defecto: {por_defecto}."

    @registrar("encender_equipo", "Enciende un equipo apagado (Wake on LAN).", _esquema({"equipo": _EQUIPO}, []))
    async def encender_equipo(entrada: dict) -> str:
        return await equipos.resolver(entrada.get("equipo")).encender()

    @registrar(
        "listar_apps",
        "Lista hasta 30 aplicaciones o juegos instalados, opcionalmente solo los que contienen un texto.",
        _esquema({"equipo": _EQUIPO, "filtro": {"type": "string", "description": "texto que debe contener"}}, []),
    )
    async def listar_apps(entrada: dict) -> str:
        eq = equipos.resolver(entrada.get("equipo"))
        filtro = normalizar_nombre(entrada.get("filtro") or "")
        nombres = [n for n in sorted(await eq.apps()) if filtro in normalizar_nombre(n)]
        if not nombres:
            return f"No hay aplicaciones que coincidan en el {eq.hablado}."
        return f"En el {eq.hablado}: " + ", ".join(nombres[:30]) + "."

    @registrar(
        "abrir_app",
        "Abre una aplicación o un juego por su nombre (aunque esté escrito aproximado). El navegador es "
        "«Google Chrome» en el PC grande y «Firefox» en el mini PC.",
        _esquema({"nombre": {"type": "string"}, "equipo": _EQUIPO}, ["nombre"]),
    )
    async def abrir_app(entrada: dict) -> str:
        eq = equipos.resolver(entrada.get("equipo"))
        real = await eq.abrir_app(str(entrada.get("nombre", "")))
        return f"Abierta {real} en el {eq.hablado}."

    @registrar(
        "abrir_web",
        "Abre una página web (URL http o https) en el navegador.",
        _esquema({"url": {"type": "string"}, "equipo": _EQUIPO}, ["url"]),
    )
    async def abrir_web(entrada: dict) -> str:
        url = str(entrada.get("url", "")).strip()
        if not url_valida(url):
            raise ErrorEquipo("Esa dirección web no es válida.")
        eq = equipos.resolver(entrada.get("equipo"))
        await eq.abrir_url(url)
        return f"Abierta la página en el {eq.hablado}."

    @registrar(
        "youtube",
        "Busca un vídeo en YouTube y abre el primer resultado.",
        _esquema({"busqueda": {"type": "string"}, "equipo": _EQUIPO}, ["busqueda"]),
    )
    async def youtube(entrada: dict) -> str:
        busqueda = str(entrada.get("busqueda", "")).strip()
        if not busqueda:
            raise ErrorEquipo("Falta qué buscar en YouTube.")
        eq = equipos.resolver(entrada.get("equipo"))
        encontrado = await buscar_youtube(busqueda)
        if encontrado:
            video_id, titulo = encontrado
            await eq.abrir_url(f"https://www.youtube.com/watch?v={video_id}")
            return f"Abierto en el {eq.hablado} el vídeo: {titulo}."
        url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(busqueda)
        await eq.abrir_url(url)
        return f"No he podido elegir un vídeo; he abierto la búsqueda en YouTube en el {eq.hablado}."

    @registrar(
        "cerrar_app",
        "Cierra las ventanas de una aplicación (con confirmación de Juan).",
        _esquema({"nombre": {"type": "string"}, "equipo": _EQUIPO}, ["nombre"]),
    )
    async def cerrar_app(entrada: dict) -> str:
        eq = equipos.resolver(entrada.get("equipo"))
        nombre = str(entrada.get("nombre", "")).strip()
        if not nombre:
            raise ErrorEquipo("Falta el nombre de la aplicación.")
        n = await eq.cerrar_app(nombre)
        if n == 0:
            return f"No he encontrado ventanas de {nombre} abiertas en el {eq.hablado}."
        return f"Cerradas {n} ventanas de {nombre} en el {eq.hablado}."

    @registrar(
        "apagar_equipo",
        "Apaga o suspende un equipo (con confirmación de Juan).",
        _esquema({"equipo": _EQUIPO, "modo": {"type": "string", "enum": ["apagar", "suspender"]}}, ["modo"]),
    )
    async def apagar_equipo(entrada: dict) -> str:
        eq = equipos.resolver(entrada.get("equipo"))
        modo = entrada.get("modo", "apagar")
        await eq.apagar(modo)
        return f"{'Suspendiendo' if modo == 'suspender' else 'Apagando'} el {eq.hablado}."

    servidor = create_sdk_mcp_server(name="pc", tools=herramientas)
    return servidor, funciones
