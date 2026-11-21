"""Carga de config.toml y .env, con validación al arrancar."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

PROMPT_POR_DEFECTO = (
    "Hablas con Juan por un altavoz. Responde en español de España, hablado y muy breve (una o dos frases "
    "salvo que pida más). Sin Markdown, sin listas, sin tablas, sin código ni rutas largas leídas "
    "en voz alta: resume. Si necesitas leer archivos, hazlo, pero di solo la conclusión. "
    "Puedes actuar en los ordenadores de Juan con las herramientas pc: abrir aplicaciones, páginas web y vídeos "
    "de YouTube, en el PC grande (Windows, por defecto) o en el mini PC. Úsalas directamente cuando lo pida y "
    "confirma en una frase lo que has hecho. Cerrar aplicaciones y apagar o suspender necesitan que Juan "
    "confirme: llama a la herramienta y, si te responde que falta la confirmación, pregúntale solo "
    '"¿Lo hago? Di sí o no" diciendo qué vas a hacer; no la repitas. No hay herramientas para comprar, pagar, '
    "escribir contraseñas ni enviar mensajes: si lo pide, di que por voz no se puede."
)

HERRAMIENTAS_PROHIBIDAS = ["Bash", "Edit", "Write", "NotebookEdit"]


class ErrorConfig(Exception):
    """Configuración inválida (mensaje pensado para mostrarse tal cual)."""


@dataclass
class Proyecto:
    nombre: str
    carpeta: str
    alias: list[str] = field(default_factory=list)
    permisos: list[str] = field(default_factory=lambda: ["Read", "Grep", "Glob", "WebSearch", "WebFetch"])
    confirmar: list[str] = field(default_factory=list)  # herramientas que piden confirmación por voz


@dataclass
class Equipo:
    nombre: str
    tipo: str  # "windows-ssh" o "local"
    alias: list[str] = field(default_factory=list)
    usuario: str = ""
    mac: str = ""
    ip: str = ""
    red: str = ""
    clave: str = ""
    known_hosts: str = ""
    alias_host: str = ""


@dataclass
class Config:
    escucha: str = "192.168.1.10"
    puerto: int = 8765
    host_publico: str = "192.168.1.10"
    dispositivos_permitidos: list[str] = field(default_factory=list)
    log: str = "INFO"
    stt_modelo: str = "small"
    stt_hilos: int = 8
    stt_vocabulario: list[str] = field(default_factory=list)
    vad_agresividad: int = 2
    silencio_fin_ms: int = 700
    voz_minima_ms: int = 300
    max_caracteres_hablados: int = 400
    tts_voz: str = "es_ES-davefx-medium"
    tts_velocidad: float = 1.0
    claude_modelo: str = "sonnet"
    conversacion_caduca_min: int = 30
    proyecto_por_defecto: str = "minibestia"
    prompt_extra: str = PROMPT_POR_DEFECTO
    proyectos: list[Proyecto] = field(default_factory=list)
    equipos: list[Equipo] = field(default_factory=list)
    pc_por_defecto: str = ""
    token: str = ""
    raiz: Path = RAIZ

    def proyecto(self, nombre: str) -> Proyecto | None:
        for p in self.proyectos:
            if p.nombre == nombre:
                return p
        return None

    def equipo(self, nombre: str) -> Equipo | None:
        for e in self.equipos:
            if e.nombre == nombre:
                return e
        return None


def leer_env(ruta: Path) -> dict[str, str]:
    """Lee un .env sencillo (CLAVE=valor, # comentarios, comillas opcionales)."""
    datos: dict[str, str] = {}
    if not ruta.is_file():
        return datos
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        datos[clave.strip()] = valor.strip().strip('"').strip("'")
    return datos


def cargar(ruta: str | Path | None = None, validar: bool = True) -> Config:
    ruta = Path(ruta) if ruta else RAIZ / "config.toml"
    if not ruta.is_file():
        raise ErrorConfig(f"No existe {ruta}. Copia config.ejemplo.toml a config.toml.")
    with ruta.open("rb") as f:
        t = tomllib.load(f)

    s, stt, vad, tts, cl = (t.get(k, {}) for k in ("servidor", "stt", "vad", "tts", "claude"))
    cfg = Config(
        escucha=s.get("escucha", "192.168.1.10"),
        puerto=int(s.get("puerto", 8765)),
        host_publico=s.get("host_publico", "192.168.1.10"),
        dispositivos_permitidos=[d.lower() for d in s.get("dispositivos_permitidos", [])],
        log=s.get("log", "INFO"),
        stt_modelo=stt.get("modelo", "small"),
        stt_hilos=int(stt.get("hilos", 8)),
        stt_vocabulario=list(stt.get("vocabulario", [])),
        vad_agresividad=int(vad.get("agresividad", 2)),
        silencio_fin_ms=int(vad.get("silencio_fin_ms", 700)),
        voz_minima_ms=int(vad.get("voz_minima_ms", 300)),
        max_caracteres_hablados=int(tts.get("max_caracteres", 400)),
        tts_voz=tts.get("voz", "es_ES-davefx-medium"),
        tts_velocidad=float(tts.get("velocidad", 1.0)),
        claude_modelo=cl.get("modelo", "sonnet"),
        conversacion_caduca_min=int(cl.get("conversacion_caduca_min", 30)),
        proyecto_por_defecto=cl.get("proyecto_por_defecto", ""),
        prompt_extra=(cl.get("prompt_extra") or PROMPT_POR_DEFECTO).strip(),
        proyectos=[
            Proyecto(
                nombre=p["nombre"],
                carpeta=p["carpeta"],
                alias=list(p.get("alias", [])),
                permisos=list(p.get("permisos", ["Read", "Grep", "Glob", "WebSearch", "WebFetch"])),
                confirmar=list(p.get("confirmar", [])),
            )
            for p in t.get("proyectos", [])
        ],
        equipos=[
            Equipo(
                nombre=e["nombre"],
                tipo=e.get("tipo", ""),
                alias=list(e.get("alias", [])),
                usuario=e.get("usuario", ""),
                mac=e.get("mac", "").lower(),
                ip=e.get("ip", ""),
                red=e.get("red", ""),
                clave=e.get("clave", ""),
                known_hosts=e.get("known_hosts", ""),
                alias_host=e.get("alias_host", "") or e.get("ip", ""),
            )
            for e in t.get("equipos", [])
        ],
        pc_por_defecto=t.get("pc", {}).get("por_defecto", ""),
    )

    env = leer_env(RAIZ / ".env")
    cfg.token = os.environ.get("ASISTENTE_TOKEN") or env.get("ASISTENTE_TOKEN", "")
    if validar:
        validar_config(cfg)
    return cfg


def validar_config(cfg: Config) -> None:
    errores: list[str] = []
    if not cfg.token:
        errores.append("falta ASISTENTE_TOKEN (en .env o en el entorno)")
    if cfg.escucha in ("0.0.0.0", "::", ""):
        errores.append("servidor.escucha no puede ser 0.0.0.0: usa la IP de la LAN o 127.0.0.1")
    if not cfg.proyectos:
        errores.append("no hay ningún [[proyectos]]")
    for p in cfg.proyectos:
        if not Path(p.carpeta).is_dir():
            errores.append(f"la carpeta del proyecto «{p.nombre}» no existe: {p.carpeta}")
    if cfg.proyecto_por_defecto and cfg.proyecto(cfg.proyecto_por_defecto) is None:
        errores.append(f"proyecto_por_defecto «{cfg.proyecto_por_defecto}» no está entre los [[proyectos]]")
    if not cfg.proyecto_por_defecto:
        errores.append("falta claude.proyecto_por_defecto")
    for e in cfg.equipos:
        if e.tipo not in ("windows-ssh", "local"):
            errores.append(f"el equipo «{e.nombre}» tiene un tipo desconocido: {e.tipo!r}")
        if e.tipo == "windows-ssh":
            for campo in ("usuario", "mac", "clave", "known_hosts"):
                if not getattr(e, campo):
                    errores.append(f"el equipo «{e.nombre}» (windows-ssh) necesita «{campo}»")
    if cfg.equipos and cfg.equipo(cfg.pc_por_defecto) is None:
        errores.append(f"pc.por_defecto «{cfg.pc_por_defecto}» no está entre los [[equipos]]")
    if not 0 <= cfg.vad_agresividad <= 3:
        errores.append("vad.agresividad debe estar entre 0 y 3")
    if errores:
        raise ErrorConfig("Configuración inválida:\n  - " + "\n  - ".join(errores))
