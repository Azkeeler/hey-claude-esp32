"""Sesiones de Claude Code de Juan (las de las terminales) para retomarlas por voz."""
from __future__ import annotations

import difflib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from claude_agent_sdk import list_sessions

from .ordenes import normalizar

log = logging.getLogger(__name__)

TROZO = 1024 * 1024
LECTURA_MAX = 20 * TROZO
_COLA = 4096  # bytes que se repiten entre trozos para no partir una coincidencia
_RE_ENTRYPOINT = re.compile(rb'"entrypoint"\s*:\s*"([^"]*)"')
_RE_CWD = re.compile(rb'"cwd"\s*:\s*"((?:[^"\\]|\\.)*)"')
_ENTRADAS_AJENAS = ("sdk-py", "sdk-cli")
_PALABRAS_SOBRAN = {"la", "el", "de", "del", "numero", "sesion"}
_NUMEROS = {
    "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
    "primera": 1, "segunda": 2, "tercera": 3, "cuarta": 4, "quinta": 5,
    "primero": 1, "segundo": 2, "tercero": 3, "cuarto": 4, "quinto": 5,
    "ultima": 1, "ultimo": 1,
}


@dataclass
class SesionInfo:
    id: str
    titulo: str
    cwd: str
    modificado: float


def _primera_aparicion(ruta: Path, patron: re.Pattern[bytes]) -> bytes | None:
    """Primer grupo que casa con `patron` leyendo el fichero a trozos (sin cargarlo entero)."""
    leido = 0
    cola = b""
    try:
        with ruta.open("rb") as f:
            while leido < LECTURA_MAX:
                trozo = f.read(TROZO)
                if not trozo:
                    break
                leido += len(trozo)
                m = patron.search(cola + trozo)
                if m:
                    return m.group(1)
                cola = (cola + trozo)[-_COLA:]
    except OSError:
        return None
    return None


def _buscar_jsonl(id_sesion: str) -> Path | None:
    for ruta in (Path.home() / ".claude" / "projects").glob(f"*/{id_sesion}.jsonl"):
        return ruta
    return None


def _titulo_crudo(s) -> str:
    return (s.custom_title or s.summary or s.first_prompt or "").strip()


def listar(limite: int = 40) -> list[SesionInfo]:
    """Sesiones de Claude Code abiertas por Juan (no las del asistente ni las de scripts), de la más reciente."""
    resultado: list[SesionInfo] = []
    for s in list_sessions(limit=limite * 2):
        titulo = _titulo_crudo(s)
        if not titulo:
            continue
        ruta = _buscar_jsonl(s.session_id)
        entrada = _primera_aparicion(ruta, _RE_ENTRYPOINT) if ruta else None
        if entrada and entrada.decode("utf-8", "replace") in _ENTRADAS_AJENAS:
            continue
        cwd = s.cwd
        if not cwd and ruta:
            crudo = _primera_aparicion(ruta, _RE_CWD)
            if crudo:
                try:
                    cwd = json.loads(b'"' + crudo + b'"')
                except ValueError:
                    cwd = None
        if not cwd or not Path(cwd).is_dir() or cwd == "/tmp" or cwd.startswith("/tmp/"):  # /tmp: pruebas
            continue
        resultado.append(SesionInfo(s.session_id, titulo, cwd, float(s.last_modified)))
        if len(resultado) >= limite:
            break
    return resultado


def titulo_hablado(info: SesionInfo) -> str:
    """Título en una línea, sin Markdown ni comillas invertidas, recortado a 9 palabras."""
    t = re.sub(r"[`*_#>:;.!?¡¿]+", " ", info.titulo)  # sin puntuación: el troceador del TTS la partiría en frases
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    return " ".join(t.split()[:9])


def _numero(pedido: str) -> int | None:
    palabras = [p for p in normalizar(pedido).split() if p not in _PALABRAS_SOBRAN]
    if len(palabras) != 1:
        return None
    p = palabras[0]
    if p.isdigit():
        return int(p)
    return _NUMEROS.get(p)


def elegir(sesiones: list[SesionInfo], pedido: str, ultima_lista: list[SesionInfo]) -> SesionInfo | None:
    """La sesión que Juan pide: por número en la última lista o por palabras del título."""
    n = _numero(pedido)
    if n is not None:
        base = ultima_lista or sesiones[:5]
        return base[n - 1] if 1 <= n <= len(base) else None
    palabras = [p for p in normalizar(pedido).split() if len(p) >= 4 and p not in _PALABRAS_SOBRAN]
    if not palabras:
        return None
    mejor, mejor_p = None, 0
    for s in sesiones:
        del_titulo = normalizar(s.titulo).split()
        puntos = sum(
            1 for p in palabras
            if any(p == w or difflib.SequenceMatcher(None, p, w).ratio() >= 0.8 for w in del_titulo)
        )
        mas_reciente = mejor is not None and puntos == mejor_p and s.modificado > mejor.modificado
        if puntos > mejor_p or (mas_reciente and puntos >= 1):
            mejor, mejor_p = s, puntos
    return mejor if mejor_p >= 1 else None
