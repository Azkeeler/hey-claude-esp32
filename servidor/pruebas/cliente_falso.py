"""Simula el ESP32 de punta a punta, sin placa.

Uso: python pruebas/cliente_falso.py --wav pregunta.wav [--salida respuesta.wav]
     python pruebas/cliente_falso.py --texto "¿qué hora es?" [--salida respuesta.wav]

Con --texto el WAV se genera con Piper. Con --abort-tras N se manda un abort N segundos después del
primer audio recibido (para probar la cancelación).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import wave
from pathlib import Path

import aiohttp
import numpy as np

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from asistente.audio import (  # noqa: E402
    CodificadorOpus,
    DecodificadorOpus,
    muestras_por_frame,
    remuestrear,
)
from asistente.config import cargar  # noqa: E402

DEVICE_ID = "02:00:00:00:00:01"  # distinto de la placa: las pruebas no le cambian el proyecto ni la sesión
CLIENT_ID = "11111111-2222-4333-8444-555555555555"


def leer_wav(ruta: Path) -> np.ndarray:
    with wave.open(str(ruta), "rb") as w:
        n, canales, ancho, tasa = w.getnframes(), w.getnchannels(), w.getsampwidth(), w.getframerate()
        datos = w.readframes(n)
    if ancho != 2:
        raise SystemExit("El WAV debe ser de 16 bits")
    pcm = np.frombuffer(datos, dtype=np.int16)
    if canales > 1:
        pcm = pcm.reshape(-1, canales).mean(axis=1).astype(np.int16)
    return remuestrear(pcm, tasa, 16000)


def escribir_wav(ruta: Path, pcm: np.ndarray, tasa: int) -> None:
    with wave.open(str(ruta), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(tasa)
        w.writeframes(np.asarray(pcm, dtype=np.int16).tobytes())


def sintetizar(texto: str) -> np.ndarray:
    from asistente.config import cargar as _c
    from piper import PiperVoice

    cfg = _c(None, validar=False)
    voz = PiperVoice.load(cfg.raiz / "modelos" / "piper" / f"{cfg.tts_voz}.onnx")
    pcm = np.concatenate([c.audio_int16_array for c in voz.synthesize(texto)])
    return remuestrear(pcm, voz.config.sample_rate, 16000)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav")
    ap.add_argument("--texto")
    ap.add_argument("--salida", default="respuesta.wav")
    ap.add_argument("--config", default=None)
    ap.add_argument("--url", default=None, help="base http del servidor (por defecto, la del config)")
    ap.add_argument("--token", default=None, help="forzar un token (para probar el 401)")
    ap.add_argument("--abort-tras", type=float, default=None, help="segundos tras el primer audio")
    ap.add_argument("--ruido", type=float, default=None,
                    help="en vez de la pregunta, manda N segundos de golpes cortos de voz (uno por segundo): "
                         "el servidor debe descartarlos y cerrar la conexión (reposo) a los ~8 s")
    ap.add_argument("--max-segundos", type=float, default=150.0)
    args = ap.parse_args()
    if not args.wav and not args.texto:
        ap.error("hace falta --wav o --texto")

    cfg = cargar(args.config, validar=False)
    base = args.url or f"http://{cfg.escucha}:{cfg.puerto}"
    pcm = leer_wav(Path(args.wav)) if args.wav else sintetizar(args.texto)
    if args.ruido:
        golpe = pcm[len(pcm) // 2:][:1920]  # 120 ms de voz
        seg = np.concatenate([golpe, np.zeros(16000 - len(golpe), dtype=np.int16)])
        pcm = np.tile(seg, int(args.ruido))
    print(f"Pregunta: {len(pcm) / 16000:.1f} s de audio")

    cabeceras = {"Device-Id": DEVICE_ID, "Client-Id": CLIENT_ID, "User-Agent": "cliente-falso/0.1", "Content-Type": "application/json"}
    t0 = time.monotonic()

    def marca() -> str:
        return f"[{time.monotonic() - t0:7.2f}s]"

    async with aiohttp.ClientSession() as http:
        async with http.post(f"{base}/xiaozhi/ota/", headers=cabeceras, json={"application": {"version": "0.0.0-prueba"}}) as r:
            ota = await r.json()
        print(marca(), "OTA:", json.dumps({**ota, "websocket": {**ota["websocket"], "token": "***"}}))
        url, token = ota["websocket"]["url"], args.token or ota["websocket"]["token"]

        enc = CodificadorOpus(16000)
        dec = DecodificadorOpus(24000)
        paso = muestras_por_frame(16000)
        voz = np.concatenate([pcm, np.zeros((-len(pcm)) % paso, dtype=np.int16)])
        silencio = np.zeros(2 * 16000, dtype=np.int16)
        frames_voz = enc.codificar(voz)
        frames_silencio = enc.codificar(silencio)

        try:
            ws = await http.ws_connect(url, headers={**cabeceras, "Authorization": f"Bearer {token}", "Protocol-Version": "1"})
        except aiohttp.WSServerHandshakeError as e:
            print(marca(), f"El servidor rechazó la conexión: HTTP {e.status}")
            return 1
        async with ws:
            await ws.send_json({"type": "hello", "version": 1, "transport": "websocket", "features": {"mcp": True},
                                "audio_params": {"format": "opus", "sample_rate": 16000, "channels": 1, "frame_duration": 60}})
            hello = json.loads((await ws.receive(timeout=10)).data)
            print(marca(), "<-", json.dumps(hello))
            sid = hello["session_id"]
            await ws.send_json({"session_id": sid, "type": "listen", "state": "detect", "text": "hey claude"})
            await ws.send_json({"session_id": sid, "type": "listen", "state": "start", "mode": "auto"})

            t = {}
            audio_out: list[np.ndarray] = []
            n_frames = 0

            async def emisor() -> None:
                nonlocal n_frames
                siguiente = time.monotonic()
                for i, f in enumerate(frames_voz + frames_silencio):
                    if i == len(frames_voz):
                        t["fin_voz"] = time.monotonic()
                        print(marca(), "-> (fin de la voz; empiezan 2 s de silencio)")
                    await ws.send_bytes(f)
                    siguiente += 0.06
                    await asyncio.sleep(max(0, siguiente - time.monotonic()))
                    if t.get("stt"):
                        break

            tarea = asyncio.create_task(emisor())
            abortado = False
            try:
                async with asyncio.timeout(args.max_segundos):
                    while True:
                        m = await ws.receive()
                        ahora = time.monotonic()
                        if m.type == aiohttp.WSMsgType.BINARY:
                            n_frames += 1
                            if "audio" not in t:
                                t["audio"] = ahora
                                print(marca(), "<- primer frame de audio")
                            audio_out.append(dec.decodificar(m.data))
                            if args.abort_tras is not None and not abortado and ahora - t["audio"] >= args.abort_tras:
                                abortado = True
                                t["abort"] = ahora
                                print(marca(), "-> abort")
                                await ws.send_json({"session_id": sid, "type": "abort", "reason": "wake_word_detected"})
                        elif m.type == aiohttp.WSMsgType.TEXT:
                            d = json.loads(m.data)
                            print(marca(), "<-", json.dumps(d, ensure_ascii=False))
                            if d.get("type") == "stt":
                                t["stt"] = ahora
                            elif d.get("type") == "tts" and d.get("state") == "sentence_start" and "frase" not in t:
                                t["frase"] = ahora
                            elif d.get("type") == "tts" and d.get("state") == "stop":
                                t["stop"] = ahora
                                if not args.ruido:
                                    break
                        else:
                            print(marca(), "conexión cerrada:", m.type)
                            t["cierre"] = ahora
                            break
            finally:
                tarea.cancel()

    if audio_out:
        escribir_wav(Path(args.salida), np.concatenate(audio_out), 24000)
        print(f"Audio recibido: {n_frames} frames ({n_frames * 0.06:.1f} s) -> {args.salida}")
    else:
        print("No llegó audio.")

    fv = t.get("fin_voz")
    if fv:
        def d(k: str) -> str:
            return f"{(t[k] - fv) * 1000:.0f} ms" if k in t else "-"
        print("Tiempos vistos por el cliente (desde que acaba la voz; incluyen ~700 ms de silencio del VAD):")
        print(f"  stt={d('stt')}  primera_frase={d('frase')}  primer_audio={d('audio')}  tts_stop={d('stop')}")
    if args.ruido:
        return 0 if "cierre" in t and "stt" not in t else 1
    return 0 if "stop" in t else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
