"""Opus <-> PCM, remuestreo y troceo en frames de 60 ms."""
from __future__ import annotations

import warnings

import numpy as np
import soxr

with warnings.catch_warnings():  # opuslib 3.0.1 lanza un SyntaxWarning inofensivo
    warnings.simplefilter("ignore")
    import opuslib

FRAME_MS = 60
TASA_SUBIDA = 16000
TASA_BAJADA = 24000
BITRATE = 24000


def muestras_por_frame(tasa: int, ms: int = FRAME_MS) -> int:
    return tasa * ms // 1000


class DecodificadorOpus:
    """Decodifica los frames Opus que manda el aparato a PCM int16 mono."""

    def __init__(self, tasa: int = TASA_SUBIDA, frame_ms: int = FRAME_MS) -> None:
        self.tasa = tasa
        self.frame_ms = frame_ms
        self._dec = opuslib.Decoder(tasa, 1)

    def decodificar(self, frame: bytes) -> np.ndarray:
        # Margen de 120 ms por si el aparato manda un frame más largo de lo anunciado.
        pcm = self._dec.decode(frame, muestras_por_frame(self.tasa, 120))
        return np.frombuffer(pcm, dtype=np.int16)


class CodificadorOpus:
    """Codifica PCM int16 mono en frames Opus de 60 ms."""

    def __init__(self, tasa: int = TASA_BAJADA, frame_ms: int = FRAME_MS, bitrate: int = BITRATE) -> None:
        self.tasa = tasa
        self.frame_ms = frame_ms
        self.muestras = muestras_por_frame(tasa, frame_ms)
        self._enc = opuslib.Encoder(tasa, 1, opuslib.APPLICATION_AUDIO)
        self._enc.bitrate = bitrate

    def codificar(self, pcm: np.ndarray) -> list[bytes]:
        """Trocea `pcm` en frames; el último se rellena con silencio."""
        pcm = np.asarray(pcm, dtype=np.int16)
        resto = len(pcm) % self.muestras
        if resto:
            pcm = np.concatenate([pcm, np.zeros(self.muestras - resto, dtype=np.int16)])
        frames = []
        for i in range(0, len(pcm), self.muestras):
            frames.append(self._enc.encode(pcm[i : i + self.muestras].tobytes(), self.muestras))
        return frames


def remuestrear(pcm: np.ndarray, origen: int, destino: int) -> np.ndarray:
    """Remuestrea PCM int16 mono con soxr."""
    pcm = np.asarray(pcm, dtype=np.int16)
    if origen == destino or len(pcm) == 0:
        return pcm
    return soxr.resample(pcm, origen, destino, quality="HQ")


def a_float(pcm: np.ndarray) -> np.ndarray:
    return np.asarray(pcm, dtype=np.float32) / 32768.0
