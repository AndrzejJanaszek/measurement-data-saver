# Ściągawka - measurement-data-saver

Szybkie notatki do codziennej obsługi usługi. Pełny opis projektu i architektury:
patrz `README.md`.

Firmware Arduino (osobne repozytorium): https://github.com/AndrzejJanaszek/dms-arduino

---

## systemctl - obsługa usługi

### Status i logi

```bash
# status usługi (czy działa, od kiedy, ile razy była restartowana)
sudo systemctl status measurement-data-saver.service

# logi na żywo (Ctrl+C żeby wyjść)
sudo journalctl -u measurement-data-saver.service -f

# logi z ostatniej godziny
sudo journalctl -u measurement-data-saver.service --since "1 hour ago"

# logi z konkretnego dnia
sudo journalctl -u measurement-data-saver.service --since "2026-08-30" --until "2026-08-31"

# tylko błędy/warningi (przydatne po dłuższym działaniu, żeby szybko znaleźć problem)
sudo journalctl -u measurement-data-saver.service -p warning

# ostatnie 200 linii bez trybu "follow"
sudo journalctl -u measurement-data-saver.service -n 200

# ile razy usługa była (re)startowana - szukaj "Started"/"Stopped"
sudo journalctl -u measurement-data-saver.service | grep -E "Started|Stopped|Main process exited"
```

### Sterowanie usługą

```bash
sudo systemctl start measurement-data-saver.service
sudo systemctl stop measurement-data-saver.service
sudo systemctl restart measurement-data-saver.service

# po każdej zmianie w pliku .service (nie po zmianie kodu Pythona!)
sudo systemctl daemon-reload

# czy usługa odpala się automatycznie po restarcie RPi
sudo systemctl is-enabled measurement-data-saver.service
sudo systemctl enable measurement-data-saver.service
sudo systemctl disable measurement-data-saver.service
```

### Watchdog - jak to czytać w logach

Usługa wysyła `WATCHDOG=1` do systemd co ~5s, tylko jeśli oba wątki robocze
(miernik + Arduino) zgłosiły heartbeat w ciągu ostatnich 5s. Systemd sam
zabija i restartuje proces, jeśli nie dostanie `WATCHDOG=1` przez czas
`WatchdogSec=15s` z pliku `.service`.

W `journalctl` warto szukać:

```bash
# czy watchdog kiedykolwiek ubił proces (oznacza że któryś wątek się zawiesił
# bez rzucenia wyjątku - inaczej zobaczylibyśmy log "CRITICAL" przed restartem)
sudo journalctl -u measurement-data-saver.service | grep -i "watchdog"

# czy aplikacja SAMA się zamknęła przez fatalny błąd (np. problem z bazą)
sudo journalctl -u measurement-data-saver.service | grep "CRITICAL"
```

Jeśli restart nastąpił z komunikatem `CRITICAL` w logach tuż przed nim -
to zadziałał nasz mechanizm (`fatal_signal` + `sys.exit(1)`), nie sam
systemd. Jeśli restart nastąpił BEZ takiego komunikatu - winny jest
watchdog systemd, co oznacza że jakiś wątek się zawiesił bez wyjątku
(np. utknął na blokującym I/O) - to sygnał do głębszego debugowania.

---

## Uruchamianie ręczne (bez systemd) - do debugowania

```bash
cd /home/andrzej/Storage/measurement-data-saver

# cała usługa (miernik + arduino + baza), w terminalu, logi na żywo
poetry run python src/main.py
# Ctrl+C żeby zatrzymać - powinno się ładnie zamknąć (patrz logi "Aplikacja
# została bezpiecznie zatrzymana")
```

## Sniffer - debugowanie portów szeregowych PRZED odpaleniem usługi

Zawsze warto sprawdzić snifferem, czy dane z portów wyglądają poprawnie
(ramkowanie, parsowanie), zanim odpali się cała usługa z zapisem do bazy.

```bash
# oba porty naraz, tryb "framed" (pokazuje ramkę + wynik parsowania -
# dokładnie to co zobaczy main.py)
poetry run python -m src.sniffer

# tylko miernik
poetry run python -m src.sniffer --meter

# tylko Arduino
poetry run python -m src.sniffer --arduino

# surowy podgląd bajtów bez ramkowania - przydatne gdy trzeba ustalić/
# zweryfikować znaki START/END albo baudrate od zera
poetry run python -m src.sniffer --raw
poetry run python -m src.sniffer --arduino --raw
```

Ctrl+C zatrzymuje sniffer (zamyka porty czysto).

**Uwaga:** Arduino po starcie/reset wysyła banner diagnostyczny (`Locating
devices...`, `Found device N with address: ...`, `io pin (INPUT_PULLUP) -
on pin: N`). To normalne - sniffer i `main.py` poprawnie je ignorują (linie
niebędące JSON-em są odrzucane cicho, na poziomie DEBUG, nie WARNING).
Prawdziwe dane zaczynają się dopiero od pierwszej linii `{"pins":...}` albo
`{"temps":...}`.

---

## Konfiguracja portów i urządzeń

Wszystko w `src/config.py`:

| Zmienna | Znaczenie |
|---|---|
| `SERIAL_PORT` | Port miernika, domyślnie `/dev/ttyUSB0` |
| `BAUDRATE`, `TIMEOUT` | Parametry portu miernika |
| `START_CHAR`, `END_CHAR_1`, `END_CHAR_2` | Znaki ramkowania miernika (obecnie: brak START, CR+LF jako END) |
| `ARDUINO_SERIAL_PORT` | Port Arduino, domyślnie `/dev/ttyACM0` |
| `ARDUINO_BAUDRATE`, `ARDUINO_TIMEOUT` | Parametry portu Arduino |
| `ARDUINO_START_CHAR`, `ARDUINO_END_CHAR_1`, `ARDUINO_END_CHAR_2` | Znaki ramkowania Arduino (obecnie: brak START, `\n` jako END) |
| `RECONNECT_DELAY` | Ile sekund odczekać przed próbą ponownego otwarcia portu po błędzie |
| `SAVE_DELAY` | Co ile sekund miernik zapisuje najnowszą wartość do bazy (Arduino zapisuje na bieżąco, bez tego opóźnienia) |
| `DB_DIR`, `DB_PATH` | Katalog/ścieżka bazy danych (domyślnie `db/measurement-data-saver.db`, w repo) |

Jak sprawdzić, do jakiego portu jest podłączone urządzenie:

```bash
# przed podłączeniem i po podłączeniu - porównaj listę
ls /dev/tty*

# albo bezpośrednio
dmesg | tail -20   # po podłączeniu USB pokaże przypisany /dev/ttyUSBx lub /dev/ttyACMx
```

---

## Format danych z Arduino

Arduino wysyła asynchronicznie dwa rodzaje ramek JSON, każda zakończona `\n`,
przeplatane w dowolnej kolejności/częstotliwości (piny częściej niż temperatury):

```json
{"temps":[{"a":"28A8F126AB240B1C","t":25.44},{"a":"28989298AA240B81","t":25.44}]}
{"pins":{"4":1,"5":1}}
```

- `temps` - lista czujników temperatury. `a` = adres czujnika (string, hex),
  `t` = temperatura w °C (float). Może zawierać jeden lub więcej czujników
  w jednej ramce - wszystkie zapisywane do bazy naraz.
- `pins` - pełny snapshot stanu WSZYSTKICH monitorowanych pinów przy każdej
  wysyłce (nie tylko zmienionych). RPi sam filtruje i zapisuje do bazy tylko
  te piny, których wartość faktycznie się zmieniła względem poprzedniego
  odczytu (`src/pin_tracker.py`).

Kod źródłowy szkicu Arduino: https://github.com/AndrzejJanaszek/dms-arduino

Konwencja logowania w firmware: linie diagnostyczne (nie-danowe) powinny mieć
prefiks `[DEBUG]` lub `[INFO]` (np. `[DEBUG] Found device 0 with address: ...`).
RPi i tak odróżnia je od danych po tym, że nie zaczynają się od `{`, ale
prefiks ułatwia czytanie surowego wyjścia ręcznie przez Arduino Serial Monitor.

---

## Baza danych

Plik: `db/measurement-data-saver.db` (katalog `db/` jest częścią repozytorium,
NIE jest w `.gitignore`).

```bash
# szybki podgląd zawartości bez pisania SQL
sqlite3 db/measurement-data-saver.db ".tables"
sqlite3 db/measurement-data-saver.db "SELECT * FROM sessions ORDER BY id DESC LIMIT 5;"
sqlite3 db/measurement-data-saver.db "SELECT * FROM measurements ORDER BY id DESC LIMIT 10;"
sqlite3 db/measurement-data-saver.db "SELECT * FROM temperature_measurements ORDER BY id DESC LIMIT 10;"
sqlite3 db/measurement-data-saver.db "SELECT * FROM pin_states ORDER BY id DESC LIMIT 10;"

# ile rekordów zebrano w danej sesji
sqlite3 db/measurement-data-saver.db "SELECT session_id, COUNT(*) FROM measurements GROUP BY session_id;"
```

### Schemat tabel

**`sessions`** - jeden wiersz na każde uruchomienie usługi (RPi nie ma RTC,
więc `started_at` to tylko przybliżenie czasu startu - `session_id` służy
głównie do grupowania rekordów z tego samego uruchomienia, niezależnie od
tego czy zegar systemowy jest akurat wiarygodny):
- `id`, `started_at` (unix timestamp)

**`measurements`** - dane z miernika:
- `id`, `timestamp`, `value` (float), `session_id`

**`temperature_measurements`** - dane z czujników temperatury Arduino:
- `id`, `timestamp`, `sensor_address` (text), `value` (float), `session_id`

**`pin_states`** - zmiany stanu pinów Arduino (tylko rzeczywiste zmiany,
nie każdy odebrany snapshot):
- `id`, `timestamp`, `pin` (integer), `value` (integer), `session_id`

Baza pracuje w trybie `WAL` (Write-Ahead Logging) - stąd obok głównego pliku
`.db` mogą pojawić się `.db-wal` i `.db-shm`, to normalne, nie kasować ręcznie
podczas działania usługi.

---

## Typowe problemy

| Objaw | Co sprawdzić |
|---|---|
| `could not open port /dev/ttyUSB0` | Czy urządzenie jest podłączone, `ls /dev/tty*`, czy użytkownik `andrzej` jest w grupie `dialout` (`groups andrzej`) |
| Usługa restartuje się w kółko | `journalctl -u measurement-data-saver.service \| grep CRITICAL` - poszukaj przyczyny fatalnego błędu |
| Brak nowych danych w bazie mimo działającej usługi | Sprawdź sniffer em, czy port w ogóle wysyła dane; sprawdź czy `SAVE_DELAY`/logika nie czeka na pierwszą poprawną ramkę |
| Dziwne/urwane linie w logach jako WARNING | Zobacz sekcję "Format danych z Arduino" wyżej - jeśli to nie banner startowy, może być realny problem z transmisją (np. luźny kabel USB) |

---

## Narzędzia dla obsługi na obiekcie

RPi na obiekcie włącza i podłącza osoba nietechniczna. Z laptopa z Windows,
połączonego z RPi kablem Ethernet (`ssh andrzej@10.10.10.1`), uruchamia
dwukliknięciem dwa programy:

| Program na laptopie | Kiedy | Co robi na RPi |
|---|---|---|
| `Ustaw_czas_Raspberry_Pi.bat` | raz, po każdym włączeniu RPi | `deploy/tools/run.sh set_time <czas laptopa>`: ustawia zegar (`sudo -n /usr/local/sbin/measurement-data-saver-set-clock <ts>`), restartuje usługę (nowa sesja z poprawnym czasem), wypisuje czas przed i po |
| `Sprawdz_Raspberry_Pi.bat` | dowolnie wiele razy | `deploy/tools/run.sh check --laptop-time <czas laptopa>`: ok. 15 s obserwacji (`--window`), raport i werdykt; niczego nie zmienia |

RPi nie ma RTC ani internetu, więc po każdym włączeniu `systemd-timesyncd`
odtwarza czas ostatniego zamknięcia i zegar jest do tyłu. Dlatego
najpierw ustawienie czasu, potem sprawdzanie.

Kod narzędzi: `src/tools/` (`check.py`, `set_time.py`, wspólne `report.py`
i `system.py`, adresy czujników w `sensors.py`). Usługa ich nie importuje.

### Jak czytać wynik sprawdzania

Każda linia ma znacznik, pod nią czasem wskazówka:

| Znacznik | Znaczenie |
|---|---|
| `[ OK ]` | działa |
| `[INFO]` | informacja, nigdy problem (np. zmiana sygnału w trakcie sprawdzania) |
| `[UWAGA]` | działa, ale coś wymaga uwagi (zegar, autostart, logi, dysk, nieznany czujnik) |
| `[BŁĄD]` | nie działa |

Na dole `WYNIK: WSZYSTKO DZIAŁA` albo `WYNIK: N problem(y/ów) – <lista>`
(każda `[UWAGA]` i każdy `[BŁĄD]` to jeden problem).

Jak walidacja rozpoznaje przyczyny (pierwsza pasująca reguła wygrywa):

| Komunikat | Skąd to wiadomo |
|---|---|
| `Miernik: wskazanie … (+N zapisów)` | przybyło wierszy w `measurements` (`MAX(id)` przed i po oknie) |
| `Miernik: nie wykryto przejściówki USB` | brak pliku `SERIAL_PORT` z `config.py`; inne `/dev/ttyUSB*` są wypisywane |
| `Miernik: … nie da się otworzyć portu` | w logach z okna `[miernik] Nie udało się otworzyć portu` |
| `Miernik: przychodzą dane w nieoczekiwanym formacie` | w logach `(niesparsowana) ramka:` |
| `Miernik: … brak danych z miernika` | w logach `cisza na porcie` |
| `Arduino: podłączone, wysyła dane` | przybyły temperatury lub zmiany pinów albo w logach jest pusta ramka `temps` |
| `Temperatury: Arduino nie wykrywa żadnego czujnika` | tylko puste ramki `{"temps":[]}` w oknie |
| `Czujnik N: błąd odczytu` | ostatni odczyt w oknie to `-127` albo `85` (wartości błędu w firmware) |
| `Sygnały: brak stanu sygnałów` | brak wierszy `pin_states` w bieżącej sesji (pełny stan jest zapisywany przy pierwszej ramce po starcie) |
| `Usługa uruchomiła się ponownie w trakcie sprawdzania` | zmiana `MainPID` albo `NRestarts` w oknie |
| `Usługa zgłaszała zawieszenie wątku` | w logach `Pomijam watchdog` |

Wzorce logów są stałymi na górze `src/tools/check.py` - zmiana treści logów
usługi wymaga zmiany walidacji. Bez dostępu do journala (grupa
`systemd-journal`) przyczyny nie zostaną rozpoznane, walidacja zgłosi to jako
`[UWAGA]`. Gdy Arduino nie widzi żadnego czujnika i nie zmienia się żaden
sygnał, jedynym śladem działającego Arduino są te logi.

### Jednorazowe przygotowanie RPi

```bash
cd /home/andrzej/Storage/measurement-data-saver
git pull
./deploy/install-tools.sh
```

Skrypt można uruchamiać wielokrotnie. Zmienia tylko trzy rzeczy:

1. Kopiuje `deploy/tools/set-clock.sh` do
   `/usr/local/sbin/measurement-data-saver-set-clock` (właściciel root, więc
   zmiana pliku w repo nic nie daje bez ponownego uruchomienia skryptu).
   Skrypt przyjmuje tylko jedną liczbę i wykonuje `date -u -s @<liczba>`.
2. Plik `/etc/sudoers.d/measurement-data-saver-tools` z jedną regułą: `andrzej`
   może bez hasła wykonać ten skrypt i
   `systemctl restart measurement-data-saver.service` - nic więcej. Plik jest
   sprawdzany `visudo -cf` przed zapisem. Bez tej reguły `sudo` przez SSH bez
   terminala zawiesiłby się na pytaniu o hasło. Osobny skrypt zamiast reguły
   `date -u -s @*`, bo `sudo-rs` nie dopuszcza `*` w argumentach, a w
   klasycznym `sudo` `*` przepuściłby dowolne dodatkowe argumenty `date`.
3. Dodaje `andrzej` do grupy `systemd-journal`, żeby `journalctl` bez `sudo`
   widział logi usługi.

Na końcu sam sprawdza, czy reguła działa. Ręczne sprawdzenie:

```bash
sudo -k; sudo -n -l          # powinno pokazać obie komendy z NOPASSWD, bez pytania o hasło
groups                       # powinno zawierać systemd-journal (po ponownym zalogowaniu)
journalctl -u measurement-data-saver.service -n 5   # bez sudo, powinno pokazać logi
```

Cofnięcie: `sudo rm /etc/sudoers.d/measurement-data-saver-tools /usr/local/sbin/measurement-data-saver-set-clock` oraz
`sudo gpasswd -d andrzej systemd-journal`.

### Jednorazowe przygotowanie laptopa (Windows 11)

1. Klucz SSH (PowerShell; w PowerShellu `$env:USERPROFILE`, nie `%USERPROFILE%`):

   ```powershell
   ssh-keygen -t ed25519 -f $env:USERPROFILE\.ssh\dms_diagnoza -N '""'
   type $env:USERPROFILE\.ssh\dms_diagnoza.pub
   ```

   Wypisaną linię dopisać na RPi do `~/.ssh/authorized_keys`. Klucz jest
   per konto Windows - inne konto na tym samym laptopie potrzebuje własnego.
2. Pobrać oba pliki z `deploy/windows/` prosto z GitHuba (strona pliku →
   „Download raw file”) i zapisać na pulpicie. Pliki są w repo z końcami linii
   CRLF (`.gitattributes`: `*.bat -text`), więc działają bez poprawek.
   Nie przenosić ich przez `scp` z Linuksa ani nie edytować edytorem, który
   zmienia końce linii.
3. Odblokować pobrane pliki: Właściwości → „Odblokuj”, albo w PowerShellu
   `Unblock-File $env:USERPROFILE\Desktop\*_Raspberry_Pi.bat`.
4. Test: kabel podłączony, dwuklik `Sprawdz_Raspberry_Pi.bat`. Pierwsze
   połączenie samo zapisze klucz hosta RPi (`StrictHostKeyChecking=accept-new`).

Ustawienia (adres, użytkownik, klucz, katalog repo na RPi) są na górze
każdego `.bat`.

### Ręczne odpowiedniki

Z laptopa (PowerShell), bez plików `.bat`:

```powershell
$ts = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
ssh -i $env:USERPROFILE\.ssh\dms_diagnoza andrzej@10.10.10.1 "bash ~/Storage/measurement-data-saver/deploy/tools/run.sh set_time $ts"
ssh -i $env:USERPROFILE\.ssh\dms_diagnoza andrzej@10.10.10.1 "bash ~/Storage/measurement-data-saver/deploy/tools/run.sh check --laptop-time $ts"
```

Na RPi, krok po kroku to samo co walidacja:

```bash
systemctl show measurement-data-saver.service --timestamp=unix -p ActiveState,UnitFileState,MainPID,NRestarts,ActiveEnterTimestamp
journalctl -u measurement-data-saver.service --since "-1min" -o cat --no-pager
ls /dev/ttyUSB* /dev/ttyACM*
sqlite3 db/measurement-data-saver.db "SELECT MAX(id) FROM measurements;"     # dwa razy w odstępie kilku sekund
sqlite3 db/measurement-data-saver.db "SELECT sensor_address, value FROM temperature_measurements ORDER BY id DESC LIMIT 4;"
sqlite3 db/measurement-data-saver.db "SELECT pin, value FROM pin_states WHERE session_id = (SELECT MAX(id) FROM sessions);"
```

i ustawianie czasu:

```bash
sudo date -u -s @$(date +%s)        # zamiast $(date +%s) czas laptopa
sudo systemctl restart measurement-data-saver.service
```

Stary `~/diagnoza/` na RPi i `Diagnostyka Raspberry Pi.bat` na laptopie
zastępują te narzędzia - można je usunąć.
