#!/usr/bin/env bash
# Ejecuta las MISMAS pruebas contra el código de la rama main (sin los arreglos) para ver qué fallaba antes.
# Uso: bash tests/comprobar_contra_main.sh   (desde la raíz del repo)
set -u
DIR=$(mktemp -d)
git worktree add -q "$DIR/main" main
PORCINO_SRC="$DIR/main" WORKER_SRC="$DIR/main/worker/worker.js" python3 -m pytest -q -p no:cacheprovider "$@"
rc=$?
git worktree remove --force "$DIR/main"
exit $rc
