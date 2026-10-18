#!/bin/bash

set -u

TOOL="${1:-}"
shift || true

case "$TOOL" in
    check)
        ;;
    set_time)
        REQUESTED="${1:-}"
        if ! [[ "$REQUESTED" =~ ^[0-9]+$ ]]; then
            echo "Użycie: run.sh set_time <unix_timestamp>" >&2
            exit 2
        fi
        shift
        ;;
    *)
        echo "Nieznane narzędzie: '${TOOL}'. Dostępne: check, set_time." >&2
        exit 2
        ;;
esac

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_DIR" || exit 1

POETRY_PATH="$(command -v poetry || echo "$HOME/.local/bin/poetry")"
if [ ! -x "$POETRY_PATH" ]; then
    echo "[BŁĄD] Nie znaleziono Poetry na Raspberry Pi (szukano w PATH i w $HOME/.local/bin)." >&2
    exit 1
fi

if [ "$TOOL" = "set_time" ]; then
    BEFORE=$(date +%s)
    DATE_ERROR=$(sudo -n /usr/local/sbin/measurement-data-saver-set-clock "$REQUESTED" 2>&1 >/dev/null)
    DATE_STATUS=$?

    set -- --requested "$REQUESTED" --before "$BEFORE" --date-status "$DATE_STATUS" "--date-error=${DATE_ERROR}" "$@"
fi

export PYTHONUNBUFFERED=1
export PYTHONUTF8=1

exec "$POETRY_PATH" run python -m "src.tools.${TOOL}" "$@"
