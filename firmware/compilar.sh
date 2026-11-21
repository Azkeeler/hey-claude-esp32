#!/usr/bin/env bash
# Compila y graba el firmware xiaozhi-esp32 con «Hey Claude» para la Waveshare ESP32-S3-AUDIO-Board.
# Uso: ./compilar.sh [--grabar]     (necesita Docker; la placa en /dev/ttyACM0 para grabar)
set -euo pipefail
cd "$(dirname "$0")"
COMMIT=831ffd14188553cc887408b75396ae38618538e0   # xiaozhi-esp32, 19/11/2026
IDF=espressif/idf:release-v6.1

if [ ! -d xiaozhi-esp32 ]; then
  git clone https://github.com/78/xiaozhi-esp32.git
  git -C xiaozhi-esp32 checkout -q "$COMMIT"
  git -C xiaozhi-esp32 apply ../ganancia-micro.patch    # ganancia 37,5 dB y sin referencia de eco
  git -C xiaozhi-esp32 apply ../activacion.patch       # varias frases de activación y oírlas mientras habla
fi
cp heyclaude.sdkconfig.defaults xiaozhi-esp32/build-hc.defaults

docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD/xiaozhi-esp32":/project -w /project "$IDF" \
  bash -c 'git config --global --add safe.directory "*" &&
           idf.py -B build-hc -DSDKCONFIG=sdkconfig.hc "-DSDKCONFIG_DEFAULTS=sdkconfig.defaults;build-hc.defaults" \
                  -DIDF_TARGET=esp32s3 -DBOARD_NAME=esp32-s3-audio-board build'

if [ "${1:-}" = "--grabar" ]; then
  docker run --rm --device /dev/ttyACM0 -v "$PWD/xiaozhi-esp32/build-hc":/b python:3.12-slim sh -c \
    'pip install -q esptool && cd /b && esptool --port /dev/ttyACM0 --baud 921600 write-flash @flash_args'
fi
