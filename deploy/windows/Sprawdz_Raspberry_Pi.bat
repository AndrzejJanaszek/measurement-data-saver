@echo off
chcp 65001 >nul
setlocal

set "RPI_HOST=10.10.10.1"
set "RPI_USER=andrzej"
set "SSH_KEY=%USERPROFILE%\.ssh\dms_diagnoza"
set "RPI_REPO=~/Storage/measurement-data-saver"

title Sprawdzanie Raspberry Pi
echo Sprawdzanie Raspberry Pi
echo.

ping -n 2 -w 1500 %RPI_HOST% | find "TTL=" >nul
if errorlevel 1 goto no_network

if not exist "%SSH_KEY%" goto no_key

for /f %%t in ('powershell -NoProfile -Command "[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()"') do set "LAPTOP_TIME=%%t"

if not defined LAPTOP_TIME goto no_time

ssh -i "%SSH_KEY%" -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new -o LogLevel=ERROR %RPI_USER%@%RPI_HOST% "bash %RPI_REPO%/deploy/tools/run.sh check --laptop-time %LAPTOP_TIME%"
if errorlevel 255 goto ssh_failed
goto end

:no_network
echo [BŁĄD] Brak połączenia z Raspberry Pi (%RPI_HOST%).
echo        Kabel Ethernet albo przejściówka Ethernet–USB-C w laptopie może nie stykać.
echo        Raspberry Pi może się jeszcze uruchamiać – odczekaj minutę i uruchom ten program jeszcze raz.
goto end

:no_key
echo [BŁĄD] Na tym laptopie nie ma klucza SSH: %SSH_KEY%
echo        Laptop nie został przygotowany do łączenia z Raspberry Pi (deploy/docs.md).
goto end

:no_time
echo [BŁĄD] Nie udało się odczytać czasu laptopa (PowerShell).
goto end

:ssh_failed
echo [BŁĄD] Nie udało się zalogować do Raspberry Pi.
echo        Raspberry Pi może się jeszcze uruchamiać – odczekaj minutę i uruchom ten program jeszcze raz.
echo        Jeśli to nie pomoże: klucz SSH tego laptopa może nie być dodany na Raspberry Pi.
goto end

:end
echo.
echo Naciśnij dowolny klawisz, aby zamknąć to okno.
pause >nul
