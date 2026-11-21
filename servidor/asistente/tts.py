"""Síntesis con Piper -> PCM int16 a 24 kHz."""
from __future__ import annotations

import asyncio
import logging

import numpy as np
from piper import PiperVoice, SynthesisConfig

from .audio import TASA_BAJADA, remuestrear
from .config import Config

log = logging.getLogger(__name__)


class Sintetizador:
    def __init__(self, cfg: Config) -> None:
        ruta = cfg.raiz / "modelos" / "piper" / f"{cfg.tts_voz}.onnx"
        if not ruta.is_file():
            raise FileNotFoundError(f"Falta la voz de Piper {ruta}. Ejecuta scripts/descargar_modelos.sh")
        log.info("Cargando voz de Piper %s", cfg.tts_voz)
        self._voz = PiperVoice.load(ruta)
        self._ajuste = SynthesisConfig(length_scale=cfg.tts_velocidad)
        self.tasa_voz = self._voz.config.sample_rate

    def _sintetizar(self, texto: str) -> np.ndarray:
        trozos = [c.audio_int16_array for c in self._voz.synthesize(texto, syn_config=self._ajuste)]
        if not trozos:
            return np.zeros(0, dtype=np.int16)
        return np.concatenate(trozos)

    async def sintetizar(self, texto: str) -> np.ndarray:
        """PCM int16 mono a 24 kHz."""
        pcm = await asyncio.to_thread(self._sintetizar, texto)
        return remuestrear(pcm, self.tasa_voz, TASA_BAJADA)
