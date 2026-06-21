# src/main.py
import time
import logging
import sys
from datetime import datetime
from src import config
from src.database import init_db, save_measurement
from src.serial_reader import SerialReader
from src.parse import parse_raw_frame
import sdnotify

# Konfiguracja logowania
logging.basicConfig(level=logging.INFO, format='%(asctime)s - [%(levelname)s] - %(message)s')

def main():
    logging.info("Uruchamianie aplikacji RPi Serial Logger...")
    
    notifier = sdnotify.SystemdNotifier()  # <-- DODANO
    notifier.notify("READY=1")

    # 1. Inicjalizacja bazy danych (tworzenie tabeli jeśli nie istnieje)
    try:
        init_db()
    except Exception as e:
        logging.critical(f"Nie można zainicjalizować bazy danych. Zamykanie aplikacji. Bieżący błąd: {e}")
        sys.exit(1)

    # 2. Inicjalizacja czytnika portu szeregowego
    reader = SerialReader()
    
    # Zmienne pomocnicze do sterowania czasem zapisu
    last_saved_time = time.time()
    latest_value = None

    logging.info("Wejście w główną pętlę programu.")
    
    try:
        while True:
            # KROK A: Próba odczytania kolejnej ramki (operacja nieblokująca)
            raw_frame = reader.read_next_frame()
            
            if raw_frame is not None:
                # Jeśli dostaliśmy ramkę, parsujemy ją na float
                parsed_value = parse_raw_frame(raw_frame)
                
                if parsed_value is not None:
                    # Zapamiętujemy najnowszą poprawną wartość
                    latest_value = parsed_value
                    # Opcjonalny log diagnostyczny (można wyłączyć na produkcji)
                    logging.debug(f"Odebrano nową wartość: {latest_value}")

            # KROK B: Sprawdzanie, czy minął czas wyznaczony na zapis do bazy
            current_time = time.time()
            if current_time - last_saved_time >= config.SAVE_DELAY:
                
                if latest_value is not None:
                    # Mamy świeżą wartość - zapisujemy ją z aktualnym czasem systemowym
                    success = save_measurement(current_time, latest_value)
                    
                    if success:
                        human_time = datetime.fromtimestamp(current_time).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                        
                        notifier.notify("WATCHDOG=1")
                        # wyłączone na czas wdrożenia
                        # logging.info(f"Zapisano do bazy: {latest_value} o godzinie {human_time}")
                        
                        # Po zapisie możemy wyczyścić latest_value, jeśli chcemy wymusić 
                        # posiadanie NOWEJ ramki w kolejnej sekundzie. Zostawiamy ją jednak,
                        # na wypadek gdyby urządzenie nadawało rzadziej niż co sekundę (zapisze ostatnią znaną).
                    
                    latest_value = None
                else:
                    logging.warning("Minęła sekunda, ale nie odebrano jeszcze żadnej poprawnej ramki danych z portu.")
                
                # Aktualizujemy czas ostatniego zapisu (niezależnie od tego czy zapis się udał)
                last_saved_time = current_time

            # KROK C: Małe mikrouśpienie, żeby pętla WHILE TRUE nie zjadła 100% procesora RPi
            # 1 milisekunda to wystarczająco krótko, by nie stracić ramek, i wystarczająco długo, by odciążyć procesor.
            time.sleep(0.001)

    except KeyboardInterrupt:
        logging.info("Wykryto przerwanie z klawiatury (Ctrl+C). Zamykanie aplikacji...")
    except Exception as e:
        logging.critical(f"Nieoczekiwany krytyczny błąd w pętli głównej: {e}", exc_info=True)
    finally:
        # Tutaj ewentualne czyszczenie zasobów przy zamknięciu
        if reader.ser and reader.ser.is_open:
            reader.ser.close()
        logging.info("Aplikacja została bezpiecznie zatrzymana.")

if __name__ == "__main__":
    main()