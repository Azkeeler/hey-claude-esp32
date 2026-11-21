#!/usr/bin/env bash
# Baja las voces de Piper y el modelo de faster-whisper a servidor/modelos/.
# Uso: scripts/descargar_modelos.sh [modelo_whisper]   (por defecto: small)
set -euo pipefail

RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
WHISPER="${1:-small}"
BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_ES"
DESTINO="$RAIZ/modelos/piper"
mkdir -p "$DESTINO" "$RAIZ/modelos/whisper"

# voz/calidad
for par in davefx/medium sharvard/medium carlfm/x_low; do
    voz="${par%/*}"
    calidad="${par#*/}"
    nombre="es_ES-${voz}-${calidad}"
    for ext in onnx onnx.json; do
        if [ ! -s "$DESTINO/$nombre.$ext" ]; then
            echo "Bajando $nombre.$ext"
            curl -fL --retry 3 -o "$DESTINO/$nombre.$ext.tmp" "$BASE/$voz/$calidad/$nombre.$ext"
            mv "$DESTINO/$nombre.$ext.tmp" "$DESTINO/$nombre.$ext"
        fi
    done
done

echo "Bajando modelo de whisper: $WHISPER"
"$RAIZ/.venv/bin/python" - "$WHISPER" "$RAIZ/modelos/whisper" <<'PY'
import sys
from faster_whisper.utils import download_model
modelo, destino = sys.argv[1], sys.argv[2]
ruta = download_model(modelo, output_dir=f"{destino}/{modelo}")
print("Listo:", ruta)
PY
echo "Modelos en $RAIZ/modelos"
