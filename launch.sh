#!/usr/bin/env sh

# Permite ejecutar el proyecto desde cualquier directorio.
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$PROJECT_DIR"

if [ -x ".venv/bin/python" ]; then
    exec ".venv/bin/python" app.py "$@"
elif command -v python3 >/dev/null 2>&1; then
    exec python3 app.py "$@"
else
    printf '%s\n' \
        'Python 3 no está disponible. Instálalo antes de continuar.' >&2
    exit 1
fi