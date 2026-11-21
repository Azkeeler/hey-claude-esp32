"""Órdenes locales que se resuelven antes de llamar a Claude. Función pura y testeable."""
from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass

UMBRAL = 0.75


@dataclass
class Orden:
    # "proyecto", "nueva", "donde", "parar", "proyecto_desconocido", "sesiones", "sesion", "confirmar", "negar"
    tipo: str
    proyecto: str | None = None  # nombre del proyecto (tipo "proyecto")
    pedido: str | None = None  # lo que dijo el usuario (tipos "proyecto_desconocido" y "sesion")


def normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFD", texto.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


_PARAR = {
    "para", "parar", "callate", "cállate", "silencio", "calla", "basta", "alto", "stop", "para ya", "calla ya",
    "callate ya", "basta ya", "ya vale", "vale ya", "ya esta", "vale para", "para para", "deja de hablar", "cancela",
    "cancelar", "no sigas", "suficiente", "ya basta", "para de hablar", "callate por favor",
}
_NUEVA = (
    "nueva conversacion",
    "nueva conversacion por favor",
    "empieza de cero",
    "empezar de cero",
    "empecemos de cero",
    "olvida lo anterior",
    "olvidalo todo",
    "borra la conversacion",
)
_DONDE = (
    "en que proyecto estas",
    "que proyecto tienes activo",
    "en que proyecto estamos",
    "en que proyecto trabajas",
)
_VERBO_CAMBIO = r"(?:cambi\w*|pas\w*|vuelv\w*|volv\w*|regres\w*|ve|vete|vamos|vamonos|ir|llevame|abre|abreme|pon|ponme|ponte|quiero)"
_RE_VERBO = re.compile(rf"\b{_VERBO_CAMBIO}\b")
# «cambia a movicio», «pasa al de consolas», «vamos a movicio»: verbo + a/al + nombre, sin la palabra «proyecto».
_RE_DIRECTO = re.compile(rf"\b{_VERBO_CAMBIO}\s+(?:a|al|para)\s+(?:el\s+|la\s+|de\s+)?(?P<x>\w+(?:\s\w+)?)$")
_PREGUNTA = re.compile(r"^(?:que|como|cuando|cuanto|cuantos|cual|cuales|donde|quien|por que|para que|hay|tiene|es|esta)\b")
_MAX_PALABRAS = 9


def _parecido(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if min(len(a), len(b)) < 5:  # alias cortos («web», «movi»): solo valen exactos
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _buscar_proyecto(texto: str, proyectos) -> str | None:
    """Busca en `texto` (ya normalizado) el nombre o alias de proyecto que más se parece a alguna de sus palabras.

    Whisper escribe «movición», «mobicio» o «mini bestias»: se comparan ventanas de palabras, no la frase entera.
    """
    palabras = normalizar(texto).split()
    mejor, mejor_puntuacion = None, 0.0
    for p in proyectos:
        for candidato in [p.nombre, *p.alias]:
            c = normalizar(candidato)
            n = len(c.split())
            for tam in {n, 1, 2}:
                for i in range(len(palabras) - tam + 1):
                    v = " ".join(palabras[i : i + tam])
                    puntuacion = max(_parecido(v, c), _parecido(v.replace(" ", ""), c.replace(" ", "")))
                    if puntuacion > mejor_puntuacion:
                        mejor, mejor_puntuacion = p.nombre, puntuacion
    return mejor if mejor_puntuacion >= UMBRAL else None


def _orden_proyecto(t: str, proyectos) -> Orden | None:
    """«cambia al proyecto X» y las formas en que Whisper lo destroza («quiero que cambia esa movición»)."""
    palabras = t.split()
    if len(palabras) > _MAX_PALABRAS or _PREGUNTA.match(t):
        return None  # una pregunta sobre un proyecto: que la conteste Claude
    con_palabra = "proyecto" in palabras or "proyectos" in palabras
    if con_palabra and (_RE_VERBO.search(t) or palabras[0] == "proyecto"):
        encontrado = _buscar_proyecto(t, proyectos)
        if encontrado:
            return Orden("proyecto", proyecto=encontrado)
        resto = t.split("proyecto", 1)[1].strip()
        return Orden("proyecto_desconocido", pedido=resto) if resto else None
    m = _RE_DIRECTO.search(t)
    if m:
        encontrado = _buscar_proyecto(m.group("x"), proyectos)
        if encontrado:
            return Orden("proyecto", proyecto=encontrado)
    return None


_SI = {
    "si", "si hazlo", "hazlo", "adelante", "si adelante", "confirmo", "confirmado", "vale", "de acuerdo", "dale",
    "ok", "okay", "si claro", "claro",
}
_NO = {"no", "cancela", "cancelalo", "mejor no", "dejalo", "no lo hagas", "para"}
_VERBO_SESION = rf"(?:{_VERBO_CAMBIO}|entra\w*|retoma\w*|continua\w*|sigue\w*)"
_RE_IR_SESION = re.compile(rf"\b{_VERBO_SESION}\b.*\bsesion\b(?P<resto>.*)$")
_RE_LISTAR = re.compile(r"\b(?:lista\w*|dime|enumera\w*|muestra\w*|cuales|que|lee\w*)\b")


def _confirmacion(t: str) -> Orden | None:
    """«sí», «hazlo», «no», «cancela»: solo se miran cuando hay algo pendiente de confirmar."""
    palabras = t.split()
    if t in _SI or (t.startswith("si ") and len(palabras) <= 4 and "no" not in palabras):
        return Orden("confirmar")
    if t in _NO or (t.startswith("no ") and len(palabras) <= 5):
        return Orden("negar")
    return None


def _orden_sesion(t: str) -> Orden | None:
    """«lista las sesiones», «ve a la sesión dos», «cambia a la sesión del asistente de voz»."""
    palabras = t.split()
    if _PREGUNTA.match(t) and not re.match(r"^(?:que|cuales)\s+sesion", t):
        return None  # «¿qué pasa con la sesión de ayer?»: que conteste Claude
    if len(palabras) <= 12 and (m := _RE_IR_SESION.search(t)):
        resto = m.group("resto").strip()
        return Orden("sesion", pedido=resto) if resto else Orden("sesiones")
    if len(palabras) <= 8 and ("sesiones" in palabras or "sesion" in palabras) and _RE_LISTAR.search(t):
        return Orden("sesiones")
    return None


def interpretar(texto: str, proyectos, pendiente: bool = False) -> Orden | None:
    """Devuelve la orden local que contiene `texto`, o None si hay que preguntar a Claude."""
    t = normalizar(texto)
    if not t:
        return None
    t_sin_cortesia = re.sub(r"\s+por favor$", "", t)
    if pendiente and (o := _confirmacion(t_sin_cortesia)):
        return o
    if t_sin_cortesia in {normalizar(x) for x in _PARAR}:
        return Orden("parar")
    if t_sin_cortesia in _NUEVA:
        return Orden("nueva")
    if t_sin_cortesia in _DONDE:
        return Orden("donde")
    if o := _orden_sesion(t_sin_cortesia):
        return o
    return _orden_proyecto(t_sin_cortesia, proyectos)
