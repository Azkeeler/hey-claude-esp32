"""Troceo del texto de Claude en frases para el TTS y limpieza de Markdown."""
from __future__ import annotations

import re

MAX_FRASE = 200

# Abreviaturas tras las que un punto NO cierra frase (en minúsculas, sin el punto).
ABREVIATURAS = {
    "p", "ej", "etc", "sr", "sra", "srta", "dr", "dra", "ud", "uds", "vd", "vds", "pág", "págs",
    "núm", "aprox", "av", "cap", "art", "vs", "ee", "uu", "tel", "pj",
}

_ENLACE_MD = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(r"https?://\S+|www\.\S+")
_VINETA = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+")
_ENCABEZADO = re.compile(r"^\s*#{1,6}\s*")
_CITA = re.compile(r"^\s*>\s*")


def limpiar_markdown(texto: str) -> str:
    """Quita el formato Markdown para que se pueda leer en voz alta."""
    lineas = []
    for linea in texto.split("\n"):
        if linea.strip().startswith("```"):
            continue
        if re.fullmatch(r"\s*([-*_=]\s*){3,}", linea):
            continue
        if re.fullmatch(r"\s*\|?[\s:|-]+\|?\s*", linea) and "-" in linea:
            continue
        linea = _ENCABEZADO.sub("", linea)
        linea = _CITA.sub("", linea)
        linea = _VINETA.sub("", linea)
        lineas.append(linea)
    t = "\n".join(lineas)
    t = _ENLACE_MD.sub(lambda m: m.group(1), t)
    t = _URL.sub("un enlace", t)
    t = t.replace("|", " ")
    t = t.replace("`", "")
    t = re.sub(r"(\*\*|__)(.+?)\1", r"\2", t)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", t)
    t = t.replace("**", "").replace("__", "")
    t = re.sub(r"[ \t]+", " ", t)
    return t.strip()


def _cortar_larga(frase: str) -> list[str]:
    """Si la frase pasa de MAX_FRASE, la corta en la coma más cercana por debajo del límite."""
    partes = []
    resto = frase.strip()
    while len(resto) > MAX_FRASE:
        corte = resto.rfind(",", 0, MAX_FRASE)
        if corte < 20:
            corte = resto.rfind(" ", 0, MAX_FRASE)
        if corte < 20:
            corte = MAX_FRASE
        partes.append(resto[: corte + 1].strip() if resto[corte] == "," else resto[:corte].strip())
        resto = resto[corte + 1 :].strip()
    if resto:
        partes.append(resto)
    return partes


def _es_fin_de_frase(buf: str, i: int) -> bool:
    """¿El carácter buf[i] (. ! ? :) seguido de espacio cierra una frase?"""
    c = buf[i]
    if c != ".":
        return True
    m = re.search(r"([^\W\d_]+)$", buf[:i], re.UNICODE)
    if m and m.group(1).lower() in ABREVIATURAS:
        # «p. ej.», «etc.», «Sr.»: no cortar.
        return False
    return True


class Troceador:
    """Acumula texto en streaming y va devolviendo frases completas ya limpias."""

    def __init__(self) -> None:
        self._buf = ""

    def _extraer(self, final: bool) -> list[str]:
        frases: list[str] = []
        while True:
            corte = None
            for i, c in enumerate(self._buf):
                if c == "\n":
                    corte = (i, i + 1)
                    break
                if c in ".!?:" and i + 1 < len(self._buf) and self._buf[i + 1] in " \t\n":
                    if _es_fin_de_frase(self._buf, i):
                        corte = (i + 1, i + 1)
                        break
            if corte is None:
                break
            frase, self._buf = self._buf[: corte[0]], self._buf[corte[1] :]
            frases.extend(self._preparar(frase))
        if final and self._buf.strip():
            frases.extend(self._preparar(self._buf))
            self._buf = ""
        return frases

    @staticmethod
    def _preparar(frase: str) -> list[str]:
        limpia = limpiar_markdown(frase)
        if not re.search(r"\w", limpia):
            return []
        return _cortar_larga(limpia)

    def alimentar(self, texto: str) -> list[str]:
        self._buf += texto
        return self._extraer(final=False)

    def terminar(self) -> list[str]:
        return self._extraer(final=True)
