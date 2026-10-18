#!/bin/bash

set -e

SERVICE_NAME="measurement-data-saver.service"
SUDOERS_FILE="/etc/sudoers.d/measurement-data-saver-tools"
TARGET_USER="${SUDO_USER:-$USER}"
DATE_PATH="$(command -v date)"
SYSTEMCTL_PATH="$(command -v systemctl)"

echo "=== Przygotowanie narzędzi dla obsługi na obiekcie (użytkownik: ${TARGET_USER}) ==="

echo "[1/3] Uprawnienia sudo bez hasła: ustawianie czasu i restart usługi..."
TMP_FILE="$(mktemp)"
trap 'rm -f "$TMP_FILE"' EXIT
echo "${TARGET_USER} ALL=(root) NOPASSWD: ${DATE_PATH} -u -s @*, ${SYSTEMCTL_PATH} restart ${SERVICE_NAME}" > "$TMP_FILE"
sudo visudo -cf "$TMP_FILE"
sudo install -m 0440 -o root -g root "$TMP_FILE" "$SUDOERS_FILE"
echo "      Zapisano ${SUDOERS_FILE}:"
sudo cat "$SUDOERS_FILE" | sed 's/^/      /'

echo "[2/3] Dostęp do logów usługi (grupa systemd-journal)..."
if id -nG "$TARGET_USER" | tr ' ' '\n' | grep -qx "systemd-journal"; then
    echo "      ${TARGET_USER} już jest w grupie systemd-journal."
else
    sudo usermod -aG systemd-journal "$TARGET_USER"
    echo "      Dodano ${TARGET_USER} do grupy systemd-journal."
    echo "      Nowe połączenia SSH mają ją od razu, bieżąca sesja dopiero po ponownym zalogowaniu."
fi

echo "[3/3] Samosprawdzenie (bez hasła)..."
sudo -k
FAILED=0
if sudo -n -l "$DATE_PATH" -u -s @0 >/dev/null 2>&1; then
    echo "      [ OK ] ustawianie czasu"
else
    echo "      [BŁĄD] ustawianie czasu - sudo nadal wymaga hasła"
    FAILED=1
fi
if sudo -n -l "$SYSTEMCTL_PATH" restart "$SERVICE_NAME" >/dev/null 2>&1; then
    echo "      [ OK ] restart usługi"
else
    echo "      [BŁĄD] restart usługi - sudo nadal wymaga hasła"
    FAILED=1
fi

if [ "$FAILED" -ne 0 ]; then
    echo "=== Przygotowanie niepełne - sprawdź ręcznie: sudo -n -l (patrz deploy/docs.md) ==="
    exit 1
fi

echo "=== Narzędzia gotowe ==="
