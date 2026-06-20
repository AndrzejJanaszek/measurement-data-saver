# src/sniffer.py
import sys
from pathlib import Path

# Upewniamy się, że Python widzi folder główny (PYTHONPATH)
sys.path.append(str(Path(__file__).resolve().parent.parent))

import serial
import time
from src import config

def run_sniffer():
    print(f"=== Uruchamianie sniffera portu: {config.SERIAL_PORT} ===")
    print(f"Parametry: {config.BAUDRATE} baud, timeout={config.TIMEOUT}s")
    print("Wciśnij Ctrl+C, aby zatrzymać.\n")
    
    try:
        ser = serial.Serial(
            port=config.SERIAL_PORT,
            baudrate=config.BAUDRATE,
            timeout=config.TIMEOUT
        )
        print("--- Połączono pomyślnie. Oczekiwanie na dane... ---\n")
    except Exception as e:
        print(f"BŁĄD: Nie można otworzyć portu: {e}")
        return

    try:
        while True:
            # Czytamy wszystko, co aktualnie znajduje się w buforze portu
            if ser.in_waiting > 0:
                raw_bytes = ser.read(ser.in_waiting)
                
                # Wyświetlamy surowe bajty (Python automatycznie pokaże np. \x02, \r, \n)
                print(f"[RAW BYTES]: {raw_bytes}")
                
                # Wyświetlamy reprezentację tekstową (zastępując błędy, jeśli wpadną śmieci)
                try:
                    text = raw_bytes.decode('utf-8', errors='replace')
                    # Reprezentacja repr() pokaże fizyczne znaki sterujące jako tekst (np. '\r\n')
                    print(f"[TEXT REPR]: {repr(text)}")
                except Exception:
                    pass
                    
                print("-" * 50)
                
            time.sleep(0.1)  # Małe uśpienie, żeby nie obciążać procesora
            
    except KeyboardInterrupt:
        print("\nZatrzymano sniffer.")
    finally:
        ser.close()
        print("Port zamknięty.")

if __name__ == "__main__":
    run_sniffer()