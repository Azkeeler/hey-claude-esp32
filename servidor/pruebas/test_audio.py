import numpy as np

from asistente.audio import CodificadorOpus, DecodificadorOpus, muestras_por_frame, remuestrear


def tono(tasa: int, segundos: float, f: float = 440.0) -> np.ndarray:
    t = np.arange(int(tasa * segundos)) / tasa
    return (np.sin(2 * np.pi * f * t) * 12000).astype(np.int16)


def test_ida_y_vuelta_opus_24k():
    pcm = tono(24000, 1.0)
    enc, dec = CodificadorOpus(24000), DecodificadorOpus(24000)
    frames = enc.codificar(pcm)
    assert len(frames) == 17  # 1 s = 16,67 frames de 60 ms; el último se rellena
    salida = np.concatenate([dec.decodificar(f) for f in frames])
    assert len(salida) == 17 * muestras_por_frame(24000)
    # El tono sigue ahí: energía parecida (ignorando los primeros frames del códec).
    a = np.abs(pcm[4000:20000].astype(float)).mean()
    b = np.abs(salida[4000:20000].astype(float)).mean()
    assert 0.6 * a < b < 1.4 * a


def test_frames_de_60ms_a_16k():
    enc = CodificadorOpus(16000)
    frames = enc.codificar(tono(16000, 0.5))
    assert len(frames) == 9
    dec = DecodificadorOpus(16000)
    assert all(len(dec.decodificar(f)) == 960 for f in frames)


def test_remuestreo():
    r = remuestrear(tono(22050, 1.0), 22050, 24000)
    assert abs(len(r) - 24000) <= 2
    assert r.dtype == np.int16
    assert remuestrear(tono(16000, 0.1), 16000, 16000).shape == (1600,)
