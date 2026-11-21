# Servidor de voz del asistente (xiaozhi -> Claude Code)

Servidor asyncio que habla el protocolo del firmware xiaozhi-esp32 (audio binario versión 1): OTA, WebSocket,
VAD (webrtcvad), STT (faster-whisper), Claude Code (claude-agent-sdk) y TTS (Piper).

## Arranque

```
cp config.ejemplo.toml config.toml        # ajusta IP, proyectos y dispositivos
cp .env.ejemplo .env && chmod 600 .env    # pon un ASISTENTE_TOKEN largo
scripts/descargar_modelos.sh              # voces de Piper y whisper «small» a modelos/
.venv/bin/python -m asistente             # o: --config otra.toml
.venv/bin/python -m pytest -q
.venv/bin/python pruebas/cliente_falso.py --texto "hola" --salida respuesta.wav [--abort-tras 1.5]
```

El entorno se crea con `uv venv --python 3.12 .venv` y `uv pip install` de las dependencias de `pyproject.toml`
(versiones fijadas).

## Notas

- Reposo: en `application.cc`, `tts stop` solo saca al aparato de «Speaking» (en modo auto lo devuelve a
  «Listening»). Para que se duerma tras 8 s sin voz, el servidor manda `tts stop` y **cierra el WebSocket**; la
  palabra de activación lo vuelve a abrir. Tras cada respuesta el aparato vuelve a escuchar solo (8 s de ventana).
- Claude: la API de `claude-agent-sdk` 0.2.187 coincide con la spec. Se añade `include_partial_messages=True`
  (streaming por `text_delta`) y `setting_sources=["project"]` (lee el `CLAUDE.md` del proyecto, no el global de
  usuario). Se retira `ANTHROPIC_API_KEY` del entorno para usar la suscripción. Tras un abort se interrumpe el turno y,
  si no se puede vaciar, se reinicia el cliente retomando la sesión (`resume`).
- Requiere que `claude` tenga sesión iniciada para el usuario que ejecuta el servidor (`claude auth login`).
- `logs/tiempos.csv` guarda tiempos por pregunta (sin texto).

## Órdenes por voz, sesiones y control de los PC

- Órdenes locales (`asistente/ordenes.py`): «cambia al proyecto X», «nueva conversación», «¿en qué proyecto estás?»,
  «lista las sesiones», «ve a la sesión dos» / «ve a la sesión de <palabras del título>» y, si hay algo pendiente de
  confirmar, «sí» / «no».
- Sesiones (`asistente/sesiones.py`): las de Claude Code de este usuario (no las del propio asistente). Se retoman
  **en una copia** (`fork_session`): la original no se toca. Con los permisos del proyecto al que pertenece su carpeta.
- Equipos (`[[equipos]]` en `config.toml`, `asistente/equipos.py`): `local` (el mini PC: `gtk-launch`, `xdg-open`,
  juegos de Steam) y `windows-ssh` (el PC grande: se busca por la MAC, SSH con la clave que ya usa Movicio —solo se
  lee— y, para que las ventanas salgan en el escritorio, una tarea programada interactiva `AsistenteVoz` que ejecuta
  `C:\asistente-voz\lanzar.ps1`).
- Herramientas MCP `pc` (`asistente/herramientas.py`): `estado_equipos`, `encender_equipo`, `listar_apps`, `abrir_app`,
  `abrir_web`, `youtube`, `cerrar_app`, `apagar_equipo`.
- Permisos por proyecto: `permisos` se aprueban solos; `confirmar` deja la acción pendiente y Claude pregunta «¿Lo
  hago? Di sí o no»; el «sí» (60 s de plazo) lo ejecuta el servidor, no el modelo. Lo demás se deniega. No hay Bash,
  ni edición, ni herramientas de compra, contraseñas o mensajes. El mini PC no se puede apagar por voz.
- Reposo: el plazo de 8 s cuenta desde `listen start`; los ruidos descartados y las transcripciones vacías no lo
  reinician. Prueba sin placa: `pruebas/cliente_falso.py --texto "hola hola hola" --ruido 20`.
