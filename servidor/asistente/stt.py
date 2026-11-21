"""Transcripción con faster-whisper (CPU, int8)."""
from __future__ import annotations

import asyncio
import logging
import re

import numpy as np
from faster_whisper import WhisperModel

from .audio import a_float
from .config import Config

log = logging.getLogger(__name__)


class Transcriptor:
    def __init__(self, cfg: Config) -> None:
        ruta = cfg.raiz / "modelos" / "whisper" / cfg.stt_modelo
        origen = str(ruta) if ruta.is_dir() else cfg.stt_modelo
        log.info("Cargando whisper «%s» (%s)", cfg.stt_modelo, origen)
        self._modelo = WhisperModel(origen, device="cpu", compute_type="int8", cpu_threads=cfg.stt_hilos)
        # Nombres propios que Whisper no conoce («Movicio» salía como «movición»): se le dan como pista.
        nombres = [p.nombre.capitalize() for p in cfg.proyectos] + list(cfg.stt_vocabulario)
        self._pistas = ", ".join(dict.fromkeys(nombres)) or None

    def _transcribir(self, pcm16k: np.ndarray) -> str:
        segmentos, _ = self._modelo.transcribe(
            a_float(pcm16k), language="es", beam_size=1, vad_filter=False, condition_on_previous_text=False,
            hotwords=self._pistas,
        )
        bruto = " ".join(s.text.strip() for s in segmentos).strip()
        texto = limpiar(bruto)
        if texto != bruto:
            log.info("STT bruto: %r", bruto)
        return texto

    async def transcribir(self, pcm16k: np.ndarray) -> str:
        return await asyncio.to_thread(self._transcribir, pcm16k)


# Frases que Whisper se inventa con ruido o silencio (vienen de subtítulos de YouTube).
_ALUCINACIONES = re.compile(
    r"^(¡?suscr[ií]bete.*|subt[ií]tulos.*|gracias por ver.*|m[uú]sica|\.+|amara\.org.*)$", re.IGNORECASE
)
# Palabra de activación que el aparato manda delante de la pregunta («Hey, Klo, …», «Hey Claude, …»).
_ACTIVACION = re.compile(r"^\W*(hey|hei|ey|jei)[\s,.]+(cl?l?aud[ei]?|kl?o(u?d)?|clo(u?d)?|claus?)\b[\s,.:!¡?¿]*", re.IGNORECASE)


def limpiar(texto: str) -> str:
    """Quita la palabra de activación del principio y descarta alucinaciones conocidas."""
    texto = _ACTIVACION.sub("", (texto or "").strip()).strip()
    if _ALUCINACIONES.match(texto.strip(" ¡!¿?")):
        return ""
    return texto


def es_texto_vacio(texto: str) -> bool:
    """Vacío, solo puntuación o una letra suelta (lo que Whisper saca del ruido: «y», «a»)."""
    return len(re.findall(r"\w", texto or "")) < 2
