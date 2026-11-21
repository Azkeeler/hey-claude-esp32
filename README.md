# Hey Claude: asistente de voz con un ESP32-S3 y un mini PC

Asistente de voz en español con la placa **Waveshare ESP32-S3-AUDIO-Board** y un mini PC de la red local:

- **Palabra de activación en la placa** («Hey Claude», MultiNet), con el firmware [xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) y dos parches.
- **Servidor en el mini PC:** VAD con webrtcvad, transcripción con faster-whisper, respuestas con Claude Code (`claude-agent-sdk`) y voz con Piper.
- **Órdenes por voz:** cambiar de proyecto y de sesión de Claude Code, y abrir aplicaciones, webs o vídeos en los PC de casa. Lo que cierra, apaga o ejecuta comandos pide un «sí» antes.

Guía completa, con fotos y los problemas que hubo (ganancia del micro ES7210, la cancelación de eco que se comía la voz…): **https://minibestia.com/asistente-voz-claude-esp32/**

## Contenido

| Carpeta | Qué hay |
|---|---|
| `firmware/` | `compilar.sh` (clona xiaozhi-esp32 en un commit fijo, aplica los parches y compila con ESP-IDF en Docker), los parches y la configuración de «Hey Claude». |
| `servidor/` | El servidor de voz en Python 3.12 (ver `servidor/README.md`). |
| `asistente-voz.service` | Servicio de systemd de usuario. Ajusta las rutas. |

## Antes de usarlo

- **Cambia las IP** (`192.168.1.10` es el mini PC de ejemplo) en `firmware/heyclaude.sdkconfig.defaults` y en `servidor/config.toml`.
- **Pon un `ASISTENTE_TOKEN` largo** en `servidor/.env`. El servidor escucha solo en la LAN: no lo expongas a Internet.
- **Claude Code necesita sesión iniciada** (`claude auth login`) con el usuario que ejecuta el servidor.

---

**English:** a Spanish-language voice assistant. The wake word runs on an ESP32-S3 with a patched xiaozhi-esp32 firmware; faster-whisper, Claude Code and Piper run on a mini PC on the LAN. Full write-up (in Spanish) at the link above.

Licencia: MIT (ver `LICENSE`). El firmware xiaozhi-esp32 es de sus autores y tiene su propia licencia.
