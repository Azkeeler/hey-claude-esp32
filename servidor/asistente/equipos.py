"""Control de los ordenadores de Juan: el mini PC (local) y el PC grande (Windows por SSH)."""
from __future__ import annotations

import asyncio
import base64
import difflib
import json
import logging
import re
import socket
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .config import Config
from .config import Equipo as ConfigEquipo
from .ordenes import normalizar

log = logging.getLogger(__name__)

PLAZO_S = 20.0  # tiempo límite de cada orden
CACHE_APPS_S = 600.0
UMBRAL_APP = 0.72
UMBRAL_EQUIPO = 0.75
CARPETA_WIN = "C:\\asistente-voz"

_NUMEROS = {
    "uno": "1", "dos": "2", "tres": "3", "cuatro": "4", "cinco": "5",
    "seis": "6", "siete": "7", "ocho": "8", "nueve": "9", "diez": "10",
}
_BASURA = (
    "desinstalar", "uninstall", "documentation", "documentacion", "readme", "manual", "help", "website",
    "faq", "release notes", "on the web", "en la web", "samples",
)
_HABLADO = {"grande": "PC grande", "mini": "mini PC"}
_UA_NAVEGADOR = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_RE_VIDEO = re.compile(
    r'"videoRenderer":\{"videoId":"([\w-]{11})".{0,400}?"title":\{"runs":\[\{"text":"((?:[^"\\]|\\.)+)"', re.S
)


class ErrorEquipo(Exception):
    """Fallo al controlar un equipo (mensaje en español, pensado para decirse en voz alta)."""


# ---------- funciones puras ----------
def normalizar_nombre(texto: str) -> str:
    """Como `ordenes.normalizar` y además pasa a cifra los números escritos («aion dos» -> «aion 2»)."""
    return " ".join(_NUMEROS.get(p, p) for p in normalizar(texto).split())


def _puntuar(pedido: str, nombre: str) -> float:
    if pedido == nombre:
        return 1.0
    pp, pn = pedido.split(), nombre.split()
    if pp and all(p in pn for p in pp):
        return 0.9 - 0.03 * (len(pn) - len(pp))
    return difflib.SequenceMatcher(None, pedido, nombre).ratio()


def mejor_app(pedido: str, nombres: list[str]) -> tuple[str | None, list[str]]:
    """Elige la aplicación que más se parece a `pedido` y devuelve hasta 3 parecidos."""
    p = normalizar_nombre(pedido)
    if not p:
        return None, []
    puntuadas = sorted(((_puntuar(p, normalizar_nombre(n)), len(n), n) for n in nombres), key=lambda x: (-x[0], x[1]))
    parecidos = [n for s, _, n in puntuadas if s >= 0.4][:3]
    if puntuadas and puntuadas[0][0] >= UMBRAL_APP:
        return puntuadas[0][2], parecidos
    return None, parecidos


def es_basura(nombre: str) -> bool:
    """Accesos directos que no son aplicaciones (desinstaladores, manuales, enlaces web...)."""
    n = normalizar_nombre(nombre)
    return any(b in n for b in _BASURA)


def url_valida(url: str) -> bool:
    if not isinstance(url, str) or len(url) > 2000 or not url.startswith(("http://", "https://")):
        return False
    return not any(c.isspace() or c in "\"'`$<>" or ord(c) < 32 or ord(c) == 127 for c in url)


def paquete_magico(mac: str) -> bytes:
    """Paquete Wake on LAN: 6 bytes ff y la MAC 16 veces."""
    crudo = bytes.fromhex(re.sub(r"[^0-9a-fA-F]", "", mac))
    if len(crudo) != 6:
        raise ValueError(f"MAC inválida: {mac!r}")
    return b"\xff" * 6 + crudo * 16


def leer_desktop(texto: str) -> dict | None:
    """Saca el nombre de un `.desktop`; None si no es una aplicación visible."""
    campos: dict[str, str] = {}
    dentro = False
    for linea in texto.splitlines():
        linea = linea.strip()
        if linea.startswith("["):
            if dentro:
                break
            dentro = linea == "[Desktop Entry]"
            continue
        if dentro and "=" in linea and not linea.startswith("#"):
            k, _, v = linea.partition("=")
            campos.setdefault(k.strip(), v.strip())
    if campos.get("Type") != "Application":
        return None
    if campos.get("NoDisplay", "").lower() == "true" or campos.get("Hidden", "").lower() == "true":
        return None
    nombre = campos.get("Name[es]") or campos.get("Name")
    return {"nombre": nombre} if nombre else None


def extraer_video(html: str) -> tuple[str, str] | None:
    """Primer vídeo (id, título) de la página de resultados de YouTube."""
    m = _RE_VIDEO.search(html)
    if not m:
        return None
    try:
        titulo = json.loads('"' + m.group(2) + '"')
    except ValueError:
        titulo = m.group(2)
    return m.group(1), titulo


def ip_por_mac(salida_ip_neigh: str, mac: str) -> str | None:
    """Busca la IP de una MAC en la salida de `ip neigh`."""
    mac = mac.lower()
    for linea in salida_ip_neigh.splitlines():
        partes = linea.split()
        if mac in [p.lower() for p in partes] and partes:
            return partes[0]
    return None


# ---------- ayudas ----------
async def _correr(args: list[str], entrada: bytes | None = None) -> tuple[int, str, str]:
    """Ejecuta un proceso (sin shell) con tiempo límite. Devuelve (código, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE if entrada is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        salida, error = await asyncio.wait_for(proc.communicate(entrada), PLAZO_S)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise ErrorEquipo("La orden ha tardado demasiado.") from None
    return proc.returncode or 0, salida.decode("utf-8", "replace"), error.decode("utf-8", "replace")


async def _suelto(args: list[str]) -> None:
    """Lanza un proceso sin esperar a que termine."""
    try:
        await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as e:
        raise ErrorEquipo("No he podido lanzarlo en el mini PC.") from e


def _b64(texto: str) -> str:
    return base64.b64encode(texto.encode("utf-8")).decode("ascii")


def _dec(texto: str) -> str:
    """Expresión de PowerShell que recupera `texto` (puede llevar acentos) sin riesgo de inyección."""
    # Entre paréntesis: como argumento de una orden (Start-Process …) PowerShell no evaluaría la expresión.
    return f"([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{_b64(texto)}')))"


# ---------- equipos ----------
class Equipo:
    """Base: lo que se puede hacer con un ordenador."""

    def __init__(self, cfg: ConfigEquipo) -> None:
        self.cfg = cfg
        self.nombre = cfg.nombre
        self.hablado = _HABLADO.get(cfg.nombre, cfg.nombre)
        self._lock = asyncio.Lock()
        self._cache: tuple[float, dict[str, str]] | None = None

    async def encendido(self) -> bool:
        raise NotImplementedError

    async def encender(self) -> str:
        raise NotImplementedError

    async def _leer_apps(self) -> dict[str, str]:
        raise NotImplementedError

    async def _lanzar(self, identificador: str) -> None:
        raise NotImplementedError

    async def abrir_url(self, url: str) -> None:
        raise NotImplementedError

    async def cerrar_app(self, nombre: str) -> int:
        raise NotImplementedError

    async def apagar(self, modo: str) -> None:
        raise NotImplementedError

    async def apps(self) -> dict[str, str]:
        """Nombre visible -> identificador para lanzarla (en caché 10 min)."""
        if self._cache and time.monotonic() - self._cache[0] < CACHE_APPS_S:
            return self._cache[1]
        apps = await self._leer_apps()
        self._cache = (time.monotonic(), apps)
        return apps

    async def abrir_app(self, nombre: str) -> str:
        """Abre la aplicación que más se parece a `nombre` y devuelve su nombre real."""
        apps = await self.apps()
        elegida, parecidos = mejor_app(nombre, list(apps))
        if elegida is None:
            extra = f" ¿Querías: {', '.join(parecidos)}?" if parecidos else ""
            raise ErrorEquipo(f"No encuentro esa aplicación en el {self.hablado}.{extra}")
        await self._lanzar(apps[elegida])
        return elegida


class EquipoLocal(Equipo):
    """El mini PC (GNOME en Wayland)."""

    _CARPETAS = (
        "/usr/share/applications",
        "~/.local/share/applications",
        "/var/lib/snapd/desktop/applications",
        "/var/lib/flatpak/exports/share/applications",
    )

    async def encendido(self) -> bool:
        return True

    async def encender(self) -> str:
        return "El mini PC ya está encendido."

    @classmethod
    def _escanear(cls) -> dict[str, str]:
        apps: dict[str, str] = {}
        for carpeta in cls._CARPETAS:
            for f in sorted(Path(carpeta).expanduser().glob("*.desktop")):
                try:
                    datos = leer_desktop(f.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    continue
                if datos and not es_basura(datos["nombre"]):
                    apps.setdefault(datos["nombre"], f.stem)
        for f in sorted(Path("~/.steam/steam/steamapps").expanduser().glob("appmanifest_*.acf")):
            try:
                texto = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            appid = re.search(r'"appid"\s+"(\d+)"', texto)
            nombre = re.search(r'"name"\s+"([^"]+)"', texto)
            if appid and nombre:
                apps.setdefault(nombre.group(1), f"steam:{appid.group(1)}")
        return apps

    async def _leer_apps(self) -> dict[str, str]:
        return await asyncio.to_thread(self._escanear)

    async def _lanzar(self, identificador: str) -> None:
        if identificador.startswith("steam:"):
            await _suelto(["steam", f"steam://rungameid/{identificador[6:]}"])
        else:
            await _suelto(["gtk-launch", identificador])

    async def abrir_url(self, url: str) -> None:
        await _suelto(["xdg-open", url])

    async def cerrar_app(self, nombre: str) -> int:
        raise ErrorEquipo("En el mini PC no se puede hacer eso por voz.")

    async def apagar(self, modo: str) -> None:
        raise ErrorEquipo("En el mini PC no se puede hacer eso por voz.")


class EquipoWindows(Equipo):
    """El PC grande: Windows con OpenSSH y PowerShell."""

    def __init__(self, cfg: ConfigEquipo) -> None:
        super().__init__(cfg)
        self._ultima_ip = cfg.ip

    async def _ip(self) -> str:
        try:
            _, salida, _ = await _correr(["ip", "neigh"])
            ip = ip_por_mac(salida, self.cfg.mac)
        except (ErrorEquipo, OSError):
            ip = None
        if ip:
            self._ultima_ip = ip
        return self._ultima_ip

    async def encendido(self) -> bool:
        ip = await self._ip()
        if not ip:
            return False
        try:
            _, w = await asyncio.wait_for(asyncio.open_connection(ip, 22), 1.0)
        except (OSError, asyncio.TimeoutError):
            return False
        w.close()
        return True

    async def _ssh(self, guion: str) -> str:
        """Ejecuta un guion de PowerShell (ASCII) por SSH, pasado por stdin, y devuelve su salida."""
        if not await self.encendido():
            raise ErrorEquipo(f"El {self.hablado} está apagado. Si quieres, lo enciendo.")
        c = self.cfg
        args = [
            "ssh", "-i", c.clave, "-o", f"UserKnownHostsFile={c.known_hosts}", "-o", f"HostKeyAlias={c.alias_host}",
            "-o", "StrictHostKeyChecking=yes", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6",
            f"{c.usuario}@{await self._ip()}", "powershell", "-NoProfile", "-NonInteractive", "-Command", "-",
        ]
        cabecera = "$ProgressPreference='SilentlyContinue'; [Console]::OutputEncoding=[Text.Encoding]::UTF8\n"
        entrada = (cabecera + guion + "\n").encode("ascii")
        async with self._lock:
            codigo, salida, error = await _correr(args, entrada)
        if codigo == 255:
            log.warning("SSH al %s falló: %s", self.nombre, error.strip()[:300])
            raise ErrorEquipo(f"No consigo conectar con el {self.hablado}.")
        lineas = [li for li in salida.splitlines() if not li.startswith(("#< CLIXML", "<Objs"))]
        return "\n".join(lineas).strip()

    async def _interactivo(self, interno: str, esperar_resultado: bool = False) -> str:
        """Ejecuta `interno` en el escritorio de Juan (sesión 1) mediante una tarea programada /IT."""
        ruta = f"{CARPETA_WIN}\\lanzar.ps1"
        tarea = (
            "powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File " + ruta
        )
        partes = [
            f"$d='{CARPETA_WIN}'; if(-not (Test-Path $d)){{New-Item -ItemType Directory -Path $d | Out-Null}}",
            f"$s={_dec(interno)}",
            f"[IO.File]::WriteAllText('{ruta}',$s,(New-Object Text.UTF8Encoding($true)))",
            f"Remove-Item '{CARPETA_WIN}\\resultado.txt' -ErrorAction SilentlyContinue",
            "schtasks /Query /TN AsistenteVoz *> $null",
            f"if($LASTEXITCODE -ne 0){{ schtasks /Create /TN AsistenteVoz /TR '{tarea}' "
            "/SC ONCE /ST 00:00 /SD 01/01/2030 /IT /F | Out-Null }",
            "schtasks /Run /TN AsistenteVoz | Out-Null",
        ]
        if esperar_resultado:
            partes += [
                "Start-Sleep -Seconds 2",
                f"if(Test-Path '{CARPETA_WIN}\\resultado.txt'){{ Get-Content '{CARPETA_WIN}\\resultado.txt' }}",
            ]
        return await self._ssh("\n".join(partes))

    async def _leer_apps(self) -> dict[str, str]:
        guion = (
            "$dirs=@((Join-Path $env:ProgramData 'Microsoft\\Windows\\Start Menu\\Programs'),"
            "(Join-Path $env:APPDATA 'Microsoft\\Windows\\Start Menu\\Programs'),"
            "(Join-Path $env:USERPROFILE 'Desktop'),(Join-Path $env:PUBLIC 'Desktop'))\n"
            "foreach($d in $dirs){ Get-ChildItem -LiteralPath $d -Recurse -File -ErrorAction SilentlyContinue |"
            " Where-Object { $_.Extension -in '.lnk','.url' } | ForEach-Object { $_.FullName } }"
        )
        salida = await self._ssh(guion)
        apps: dict[str, str] = {}
        for ruta in salida.splitlines():
            ruta = ruta.strip()
            if not ruta:
                continue
            nombre = ruta.rsplit("\\", 1)[-1].rsplit(".", 1)[0]
            if nombre and not es_basura(nombre):
                apps.setdefault(nombre, ruta)
        return apps

    async def _lanzar(self, identificador: str) -> None:
        await self._interactivo(f"Start-Process -FilePath {_dec(identificador)}")

    async def abrir_url(self, url: str) -> None:
        await self._interactivo(f"Start-Process {_dec(url)}")

    async def cerrar_app(self, nombre: str) -> int:
        interno = (
            f"$t=[WildcardPattern]::Escape({_dec(nombre)}); $n=0\n"
            "Get-Process | Where-Object { $_.MainWindowHandle -ne 0 -and "
            "($_.MainWindowTitle -like \"*$t*\" -or $_.ProcessName -like \"*$t*\") } | "
            "ForEach-Object { if($_.CloseMainWindow()){ $n++ } }\n"
            f"Set-Content -Path '{CARPETA_WIN}\\resultado.txt' -Value $n"
        )
        salida = await self._interactivo(interno, esperar_resultado=True)
        try:
            return int(salida.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return 0

    async def apagar(self, modo: str) -> None:
        if modo == "suspender":
            await self._ssh("rundll32.exe powrprof.dll,SetSuspendState 0,1,0")
        elif modo == "apagar":
            await self._ssh("shutdown /s /t 20")
        else:
            raise ErrorEquipo("Solo sé apagar o suspender.")

    def _enviar_wol(self) -> None:
        paquete = paquete_magico(self.cfg.mac)
        destinos = [f"{self.cfg.red}255", "255.255.255.255"] if self.cfg.red else ["255.255.255.255"]
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            for destino in destinos:
                for puerto in (9, 7):
                    s.sendto(paquete, (destino, puerto))

    async def encender(self) -> str:
        if await self.encendido():
            return f"El {self.hablado} ya está encendido."
        try:
            await asyncio.to_thread(self._enviar_wol)
        except (OSError, ValueError) as e:
            log.warning("Wake on LAN falló: %r", e)
            raise ErrorEquipo(f"No he podido mandar la orden de encendido al {self.hablado}.") from e
        return "He mandado la orden de encendido; tarda un minuto más o menos."


class Equipos:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._equipos: dict[str, Equipo] = {}
        for e in cfg.equipos:
            self._equipos[e.nombre] = EquipoWindows(e) if e.tipo == "windows-ssh" else EquipoLocal(e)

    def todos(self) -> list[Equipo]:
        return list(self._equipos.values())

    def resolver(self, nombre: str | None) -> Equipo:
        """El equipo que se nombra (por nombre o alias); sin nombre, el de por defecto."""
        if not nombre or not nombre.strip():
            return self._equipos[self.cfg.pc_por_defecto]
        buscado = normalizar_nombre(nombre)
        mejor, mejor_p = None, 0.0
        for eq, e in zip(self._equipos.values(), self.cfg.equipos):
            for cand in [e.nombre, *e.alias]:
                c = normalizar_nombre(cand)
                p = 1.0 if c == buscado else difflib.SequenceMatcher(None, buscado, c).ratio()
                if p > mejor_p:
                    mejor, mejor_p = eq, p
        if mejor is None or mejor_p < UMBRAL_EQUIPO:
            tengo = ", ".join(e.hablado for e in self._equipos.values())
            raise ErrorEquipo(f"No conozco ese equipo. Tengo: {tengo}.")
        return mejor


def _pedir_youtube(busqueda: str) -> str:
    url = "https://www.youtube.com/results?" + urllib.parse.urlencode({"search_query": busqueda, "hl": "es"})
    req = urllib.request.Request(
        url, headers={"User-Agent": _UA_NAVEGADOR, "Accept-Language": "es-ES,es", "Cookie": "SOCS=CAI"}
    )
    with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 (URL fija de youtube.com)
        return r.read().decode("utf-8", "replace")


async def buscar_youtube(busqueda: str) -> tuple[str, str] | None:
    """Primer vídeo (id, título) para `busqueda`, o None si falla."""
    try:
        html = await asyncio.to_thread(_pedir_youtube, busqueda)
    except Exception as e:  # noqa: BLE001
        log.warning("Búsqueda en YouTube falló: %r", e)
        return None
    return extraer_video(html)
