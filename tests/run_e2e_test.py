import sys
from pathlib import Path

# Automatycznie wykrywa główny folder projektu i dodaje go do ścieżek wyszukiwania Pythona
sys.path.append(str(Path(__file__).resolve().parent.parent))


import time
import math
import serial
import subprocess
import os
from threading import Thread

# Importujemy aplikację i jej konfigurację
from src import config
from src.main import main

# Definiujemy ścieżki wirtualnych portów
VTTY_SENDER = "/tmp/vtty_sender"
VTTY_READER = "/tmp/vtty_reader"

def generate_sinus_value(t: float) -> float:
    """Generuje wartość funkcji sinus w zależności od czasu."""
    return 20.0 + 10.0 * math.sin(t / 2.0)

def simulator_worker():
    """Funkcja działająca w osobnym wątku, wysyłająca dane typu sinus."""
    print("[Symulator] Oczekiwanie na inicjalizację portu...")
    time.sleep(1)  # Dajemy chwilę na wstanie socata
    
    try:
        ser = serial.Serial(VTTY_SENDER, baudrate=9600, timeout=1)
        print("[Symulator] Połączono z portem nadawczym. Rozpoczynam nadawanie...")
        
        # Nadajemy dane przez 10 sekund, po czym kończymy test
        start_test_time = time.time()
        while time.time() - start_test_time < 10:
            current_time = time.time()
            val = generate_sinus_value(current_time)
            
            # Formatujemy ramkę: [START]Wartosc[END]
            frame = config.START_CHAR + f"{val:.2f}".encode('utf-8') + config.END_CHAR_1
            ser.write(frame)
            print(f"[Symulator] Wysłano: {frame.decode('utf-8', errors='ignore')}")
            
            time.sleep(0.2)  # Częste próbkowanie czujnika
            
        print("[Symulator] Zakończono generowanie danych testowych.")
    except Exception as e:
        print(f"[Symulator] Błąd: {e}")

def run_e2e_test():
    print("=== URUCHAMIANIE TESTU INTEGRACYJNEGO END-TO-END ===")

    # 1. Dynamicznie nadpisujemy konfigurację programu w locie!
    # Dzięki temu NIE musisz nic zmieniać ręcznie w src/config.py
    config.SERIAL_PORT = VTTY_READER
    config.DB_PATH = "e2e_test_measurements.db"
    config.SAVE_DELAY = 1.0  # Zapis co sekundę

    # Czyszczenie starej bazy testowej, jeśli istnieje
    if os.path.exists(config.DB_PATH):
        os.remove(config.DB_PATH)

    # 2. Uruchamiamy socat jako proces w tle (subprocess)
    socat_cmd = [
        "socat", "-d", "-d",
        f"pty,link={VTTY_SENDER},raw,echo=0",
        f"pty,link={VTTY_READER},raw,echo=0"
    ]
    socat_process = subprocess.Popen(socat_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("[System] Wirtualny tunel socat został uruchomiony w tle.")

    # 3. Uruchamiamy nasz symulator sinusa w osobnym wątku (Thread)
    sim_thread = Thread(target=simulator_worker, daemon=True)
    sim_thread.start()

    # 4. Uruchamiamy główną aplikację (w głównym wątku)
    print("[System] Uruchamianie aplikacji głównej main()...")
    print("[System] Test potrwa 10 sekund, po czym zamknie się automatycznie.")
    
    try:
        # Ponieważ main() ma pętlę "while True", aplikacja będzie działać.
        # Odpalimy ją wewnątrz bloku try/except, ale przerwiemy ją czasowo.
        # Aby main() nie działał w nieskończoność, użyjemy prostego mechanizmu:
        # Pozwolimy głównemu wątkowi kręcić aplikacją, dopóki wątek symulatora żyje.
        
        # Mały hack: odpalamy main w osobnym wątku, żebyśmy mogli go ubić po 10 sekundach
        app_thread = Thread(target=main, daemon=True)
        app_thread.start()
        
        # Czekamy 10 sekund na zakończenie pracy przez symulator
        sim_thread.join()
        time.sleep(1) # Chwila na ostatni zapis do bazy
        
        print("\n=== KONIEC TESTU ===")
        print("Test zakończony pomyślnie.")
        
    finally:
        # 5. SPRZĄTANIE: Bezwarunkowo zabijamy proces socat w tle
        socat_process.terminate()
        socat_process.wait()
        print("[System] Wirtualny tunel socat zamknięty.")
        
        # Weryfikacja bazy danych
        if os.path.exists(config.DB_PATH):
            import sqlite3
            conn = sqlite3.connect(config.DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*), AVG(value) FROM measurements")
            count, avg = cursor.fetchone()
            print(f"[Wynik] W bazie e2e_test_measurements.db zapisano {count} pomiarów.")
            print(f"[Wynik] Średnia wartość z sinusa: {avg:.2f}")
            conn.close()

if __name__ == "__main__":
    run_e2e_test()