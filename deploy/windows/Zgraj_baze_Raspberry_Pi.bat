@echo off
chcp 65001 >nul
setlocal

set "RPI_HOST=10.10.10.1"
set "RPI_USER=andrzej"
set "SSH_KEY=%USERPROFILE%\.ssh\dms_diagnoza"
set "RPI_REPO=~/Storage/measurement-data-saver"
set "RPI_DUMP_DIR=measurement-data-saver-dumps"
set "TARGET_FOLDER=Zrzuty_bazy"
set "SSH_OPTIONS=-i "%SSH_KEY%" -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new -o LogLevel=ERROR"

title Zgrywanie bazy z Raspberry Pi
echo Zgrywanie bazy z Raspberry Pi
echo.

ping -n 2 -w 1500 %RPI_HOST% | find "TTL=" >nul
if errorlevel 1 goto no_network

if not exist "%SSH_KEY%" goto no_key

for /f "usebackq delims=" %%d in (`powershell -NoProfile -Command "[Environment]::GetFolderPath('Desktop')"`) do set "DESKTOP=%%d"
if not defined DESKTOP goto no_desktop

for /f %%t in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HHmm"') do set "STAMP=%%t"
if not defined STAMP goto no_time

set "TARGET_DIR=%DESKTOP%\%TARGET_FOLDER%"
set "TARGET_DB=%TARGET_DIR%\measurement-data-saver_%STAMP%.db"
set "TARGET_SUM=%TARGET_DIR%\measurement-data-saver_%STAMP%.sha256"

if not exist "%TARGET_DIR%" mkdir "%TARGET_DIR%"
if not exist "%TARGET_DIR%" goto no_folder

ssh %SSH_OPTIONS% %RPI_USER%@%RPI_HOST% "true"
if errorlevel 255 goto ssh_failed

ssh %SSH_OPTIONS% %RPI_USER%@%RPI_HOST% "bash %RPI_REPO%/deploy/tools/run.sh dump"
set "DUMP_STATUS=%errorlevel%"
if "%DUMP_STATUS%"=="255" goto connection_lost
if "%DUMP_STATUS%"=="0" goto download
if "%DUMP_STATUS%"=="3" goto download
goto not_dumped

:download
echo.
echo Pobieranie kopii na laptopa...
scp %SSH_OPTIONS% %RPI_USER%@%RPI_HOST%:%RPI_DUMP_DIR%/dump.db "%TARGET_DB%"
if errorlevel 1 goto download_failed
scp %SSH_OPTIONS% %RPI_USER%@%RPI_HOST%:%RPI_DUMP_DIR%/dump.sha256 "%TARGET_SUM%"
if errorlevel 1 goto download_failed

powershell -NoProfile -Command "if ((Get-FileHash -Algorithm SHA256 -LiteralPath $env:TARGET_DB).Hash -ieq (Get-Content -LiteralPath $env:TARGET_SUM -TotalCount 1).Trim()) { exit 0 } else { exit 1 }"
if errorlevel 1 goto checksum_mismatch

ssh %SSH_OPTIONS% %RPI_USER%@%RPI_HOST% "bash %RPI_REPO%/deploy/tools/run.sh dump_cleanup"
if errorlevel 1 echo [UWAGA] Nie udało się usunąć kopii z Raspberry Pi – zostanie usunięta przy następnym zgraniu.

echo.
echo [ OK ]   Baza zapisana, suma SHA-256 zgodna:
echo          %TARGET_DB%
if "%DUMP_STATUS%"=="3" echo [UWAGA] Zgrywanie zgłosiło problemy (raport wyżej) – uruchom Sprawdz_Raspberry_Pi.bat.
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

:no_desktop
echo [BŁĄD] Nie udało się odczytać położenia pulpitu (PowerShell).
goto end

:no_folder
echo [BŁĄD] Nie udało się przygotować folderu na pulpicie: %TARGET_DIR%
goto end

:no_time
echo [BŁĄD] Nie udało się odczytać czasu laptopa (PowerShell).
goto end

:ssh_failed
echo [BŁĄD] Nie udało się zalogować do Raspberry Pi.
echo        Raspberry Pi może się jeszcze uruchamiać – odczekaj minutę i uruchom ten program jeszcze raz.
echo        Jeśli to nie pomoże: klucz SSH tego laptopa może nie być dodany na Raspberry Pi.
goto end

:connection_lost
echo.
echo [BŁĄD] Połączenie z Raspberry Pi zostało przerwane w trakcie zgrywania.
echo        Kabel Ethernet albo przejściówka Ethernet–USB-C w laptopie może nie stykać.
echo        Sprawdź połączenie i uruchom ten program jeszcze raz.
goto end

:not_dumped
echo.
echo [BŁĄD] Baza nie została zgrana (powód w raporcie wyżej, kod %DUMP_STATUS%).
goto end

:download_failed
if exist "%TARGET_DB%" del "%TARGET_DB%"
if exist "%TARGET_SUM%" del "%TARGET_SUM%"
echo [BŁĄD] Nie udało się pobrać kopii z Raspberry Pi. Kopia zostaje na Raspberry Pi.
echo        Uruchom ten program jeszcze raz.
goto end

:checksum_mismatch
if exist "%TARGET_DB%" del "%TARGET_DB%"
if exist "%TARGET_SUM%" del "%TARGET_SUM%"
echo [BŁĄD] Pobrany plik różni się od kopii na Raspberry Pi (suma SHA-256). Kopia zostaje na Raspberry Pi.
echo        Uruchom ten program jeszcze raz.
goto end

:end
echo.
echo Naciśnij dowolny klawisz, aby zamknąć to okno.
pause >nul
