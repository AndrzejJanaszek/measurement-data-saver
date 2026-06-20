#!/bin/bash

set -e

SERVICE_NAME="measurement-data-saver.service"
REPO_SERVICE_PATH="$(pwd)/deploy/${SERVICE_NAME}"
SYSTEMD_PATH="/etc/systemd/system/${SERVICE_NAME}"

echo "=== Rozpoczynam instalację usługi systemowej ==="

# 1. Dynamiczne wykrywanie ścieżki do Poetry na tym konkretnym systemie
POETRY_PATH=$(which poetry || echo "")
if [ -z "$POETRY_PATH" ]; then
    echo "Błąd: Poetry nie jest zainstalowane lub nie ma go w PATH systemu!"
    exit 1
fi
echo "[IaC] Wykryto Poetry w ścieżce: $POETRY_PATH"

# 2. Aktualizacja ścieżki ExecStart bezpośrednio w pliku z repozytorium
# Używamy narzędzia 'sed' do podmiany linijki zaczynającej się od ExecStart
sed -i "s|^ExecStart=.*|ExecStart=${POETRY_PATH} run python src/main.py|" "$REPO_SERVICE_PATH"

# 3. Tworzenie bezpiecznego powiązania (idempotentne dzięki -f)
echo "Tworzenie dowiązania symbolicznego..."
sudo ln -sf "$REPO_SERVICE_PATH" "$SYSTEMD_PATH"

# 4. Przeładowanie i restart (idempotentne)
echo "Przeładowanie systemd i restart usługi..."
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

echo "=== Usługa zainstalowana/zaktualizowana pomyślnie! ==="