# measurement-data-saver

Usługa systemowa na Raspberry Pi, która w tle zbiera dane pomiarowe z dwóch
niezależnych źródeł podłączonych po USB/UART i zapisuje je do lokalnej bazy
SQLite:

- **miernik** - urządzenie wysyłające pojedynczą wartość liczbową w ramce
  tekstowej,
- **Arduino** - moduł z czujnikami temperatury (DS18B20 lub podobne) oraz
  monitorowanymi pinami cyfrowymi, wysyłający dane w formacie JSON.

Usługa działa jako `systemd` unit z automatycznym restartem i watchdogiem,
więc jest odporna na odpięcie kabla, chwilowe błędy portu czy zawieszenie
się jednego z komponentów.

## Funkcje

- Równoległy, niezależny odczyt z dwóch portów szeregowych (miernik + Arduino)
- Automatyczne ponawianie połączenia po odpięciu/awarii portu
- Zapis temperatury z wielu czujników naraz (batch insert)
- Zapis stanu pinów wyłącznie przy faktycznej zmianie wartości (nie każdy
  odebrany snapshot trafia do bazy)
- Grupowanie danych w "sesje" (jedno uruchomienie usługi = jeden `session_id`)
  jako obejście braku zegara czasu rzeczywistego (RTC) na RPi
- Automatyczny restart usługi przy fatalnym błędzie (np. trwały problem
  z zapisem do bazy) oraz przy zawieszeniu się wątku roboczego (watchdog
  systemd)
- Narzędzie `sniffer` do debugowania obu portów niezależnie od siebie,
  przed odpaleniem właściwej usługi

## Architektura

```
                ┌──────────────────┐        ┌──────────────────┐
                │   meter_worker   │        │  arduino_worker  │
                │   (wątek 1)      │        │   (wątek 2)      │
                └────────┬─────────┘        └────────┬─────────┘
                         │                            │
                 SerialReader                  SerialReader
                 (miernik)                     (Arduino)
                         │                            │
                    parse_raw_frame           parse_arduino_frame
                         │                    ┌───────┴───────┐
                         │                 "temps"          "pins"
                         │                    │           PinStateTracker
                         ▼                    ▼                ▼
                ┌─────────────────────────────────────────────────┐
                │                  database.py                    │
                │   measurements | temperature_measurements |     │
                │              pin_states | sessions               │
                └─────────────────────────────────────────────────┘
                                     ▲
                                     │ heartbeat + fatal_signal
                                     │
                              ┌──────┴──────┐
                              │   main.py   │  ← orkiestrator, watchdog systemd
                              └─────────────┘
```

### Moduły

| Plik | Odpowiedzialność |
|---|---|
| `src/main.py` | Punkt wejścia. Inicjalizuje bazę i sesję, startuje oba wątki robocze, pilnuje ich zdrowia (heartbeat) i zgłasza watchdog do systemd. Kończy proces (`sys.exit(1)`) przy fatalnym błędzie, żeby `systemd` zrobił restart. |
| `src/workers/meter_worker.py` | Pętla robocza dla miernika - odczyt, parsowanie, zapis najnowszej wartości co `SAVE_DELAY` sekund. |
| `src/workers/arduino_worker.py` | Pętla robocza dla Arduino - odczyt, rozpoznanie typu ramki (`temps`/`pins`), zapis temperatury na bieżąco i zmian pinów po przefiltrowaniu przez `PinStateTracker`. |
| `src/serial_reader.py` | Ogólna klasa do odczytu i ramkowania danych z portu szeregowego (współdzielona przez oba workery, parametryzowana). |
| `src/parse.py` | Parsowanie ramek: liczba z miernika, JSON z Arduino (z odróżnieniem logów diagnostycznych Arduino od realnych błędów transmisji). |
| `src/pin_tracker.py` | Śledzenie ostatniego stanu pinów, żeby zapisywać do bazy tylko rzeczywiste zmiany. |
| `src/heartbeat.py` | Mechanizm zgłaszania "żyję" przez wątki robocze oraz sygnalizowania fatalnych błędów do wątku głównego. |
| `src/database.py` | Warstwa dostępu do SQLite: schemat, migracje, zapisy z retry, tryb WAL. |
| `src/config.py` | Cała konfiguracja: porty, baudrate, znaki ramkowania, ścieżka bazy. |
| `src/sniffer.py` | Niezależne narzędzie CLI do podglądu i debugowania portów (miernik/Arduino/oba, tryb surowy lub z parsowaniem). |

### Baza danych

SQLite w trybie WAL, plik w `db/measurement-data-saver.db` (w repozytorium,
nie w `.gitignore`). Cztery tabele: `sessions`, `measurements`,
`temperature_measurements`, `pin_states` - każda z kolumną `session_id`
grupującą dane z jednego uruchomienia usługi. Pełny schemat i przykładowe
zapytania: patrz `docs.md`.

## Wymagania

- Raspberry Pi (testowane na Ubuntu 26.04 LTS)
- Python 3.12+, [Poetry](https://python-poetry.org/)
- Miernik podłączony po USB (domyślnie `/dev/ttyUSB0`)
- Arduino z czujnikami temperatury i monitorowanymi pinami, wgrany firmware
  z [dms-arduino](https://github.com/AndrzejJanaszek/dms-arduino) (domyślnie
  `/dev/ttyACM0`)
- Użytkownik systemowy w grupie `dialout` (dostęp do portów szeregowych)

## Szybki start

```bash
git clone <adres-tego-repozytorium>
cd measurement-data-saver
poetry install

# zanim odpalisz usługę - zweryfikuj snifferem, że oba porty wysyłają
# poprawne dane (patrz docs.md, sekcja "Sniffer")
poetry run python -m src.sniffer

# instalacja jako usługa systemd
./install.sh
```

Pełna instrukcja instalacji, konfiguracji portów, obsługi `systemctl`,
watchdoga i typowych problemów: **[`docs.md`](./docs.md)**.

## Powiązane repozytoria

- Firmware Arduino: https://github.com/AndrzejJanaszek/dms-arduino

## Struktura projektu

```
measurement-data-saver/
├── db/                          # baza SQLite (w repo, nie w .gitignore)
├── deploy/
│   └── measurement-data-saver.service
├── src/
│   ├── workers/
│   │   ├── meter_worker.py
│   │   └── arduino_worker.py
│   ├── config.py
│   ├── database.py
│   ├── heartbeat.py
│   ├── main.py
│   ├── parse.py
│   ├── pin_tracker.py
│   ├── serial_reader.py
│   └── sniffer.py
├── install.sh
├── docs.md                      # ściągawka operacyjna (systemctl, debugowanie)
└── README.md                    # ten plik
```