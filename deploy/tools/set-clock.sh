#!/bin/bash

set -eu

if [ "$#" -ne 1 ] || ! [[ "$1" =~ ^[0-9]+$ ]]; then
    echo "Użycie: $(basename "$0") <unix_timestamp>" >&2
    exit 2
fi

exec /usr/bin/date -u -s "@$1"
