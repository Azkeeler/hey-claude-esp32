"""python -m asistente [--config config.toml]"""
from __future__ import annotations

import argparse
import logging
import sys

from aiohttp import web

from .config import ErrorConfig, cargar


def main() -> int:
    ap = argparse.ArgumentParser(prog="asistente", description="Servidor de voz xiaozhi -> Claude Code")
    ap.add_argument("--config", default=None, help="ruta de config.toml (por defecto, el de la carpeta del servidor)")
    args = ap.parse_args()

    try:
        cfg = cargar(args.config)
    except ErrorConfig as e:
        print(e, file=sys.stderr)
        return 2

    logging.basicConfig(
        level=getattr(logging, cfg.log.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)

    # Los modelos pesados se importan aquí para que un error de configuración salga rápido.
    from .claude import Conversaciones
    from .equipos import Equipos
    from .http import crear_app
    from .sesion import Servicios
    from .stt import Transcriptor
    from .tts import Sintetizador

    equipos = Equipos(cfg) if cfg.equipos else None
    servicios = Servicios(cfg, Transcriptor(cfg), Sintetizador(cfg), Conversaciones(cfg, equipos))
    logging.getLogger(__name__).info("Escuchando en %s:%d", cfg.escucha, cfg.puerto)
    web.run_app(crear_app(servicios), host=cfg.escucha, port=cfg.puerto, print=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
