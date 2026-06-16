# src/serial_reader.py
import time
import logging
import serial
from src import config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class SerialReader:
    def __init__(self):
        self.ser = None
        self.buffer = b""
        self.max_buffer_size = 1024  # Bezpiecznik: max 1KB śmieci w buforze

    def connect(self):
        """Zapętla się aż do skutku, próbując otworzyć port."""
        while True:
            try:
                logging.info(f"Próba połączenia z {config.SERIAL_PORT}...")
                self.ser = serial.Serial(
                    port=config.SERIAL_PORT,
                    baudrate=config.BAUDRATE,
                    timeout=config.TIMEOUT
                )
                logging.info("Połączono pomyślnie!")
                self.buffer = b""
                return
            except serial.SerialException as e:
                logging.error(f"Błąd połączenia: {e}. Ponowna próba za 5 sekund...")
                time.sleep(5)

    def read_next_frame(self) -> bytes | None:
        """
        Czyta dane, dokleja do bufora i próbuje wyciąć kompletną ramkę.
        Jeśli ramka jest poszarpana (brak END), zostawia bufor i czeka na resztę.
        """
        if self.ser is None or not self.ser.is_open:
            self.connect()

        try:
            if self.ser.in_waiting > 0:
                # 1. Doklejamy NOWE bajty na koniec naszego bufora
                self.buffer += self.ser.read(self.ser.in_waiting)

            # 2. Szukamy znaku START
            start_idx = self.buffer.find(config.START_CHAR)
            if start_idx == -1:
                # Brak START? To znaczy, że cały dotychczasowy bufor to śmieci.
                # Czyścimy, żeby nie rosły.
                self.buffer = b""
                return None

            # 3. Jeśli START nie jest na początku (indeks > 0), 
            # odrzucamy śmieci przed znakiem START
            if start_idx > 0:
                self.buffer = self.buffer[start_idx:]

            # 4. Konstruujemy sekwencję końca
            end_sequence = config.END_CHAR_1
            if config.END_CHAR_2 is not None:
                end_sequence += config.END_CHAR_2

            # 5. Szukamy znaku końca
            end_idx = self.buffer.find(end_sequence)
            if end_idx != -1:
                # MAMY TO! Jest START i jest END. Wycinamy dane.
                raw_frame = self.buffer[len(config.START_CHAR):end_idx]
                
                # Usuwamy z bufora tylko to, co już skonsumowaliśmy.
                # Reszta (np. początek kolejnej ramki) zostaje!
                self.buffer = self.buffer[end_idx + len(end_sequence):]
                return raw_frame

            # 6. BEZPIECZNIK: Jeśli mamy START, ale bufor puchnie w nieskończoność 
            # i nie może znaleźć END (np. zakłócenia zerwały transmisję), czyścimy go.
            if len(self.buffer) > self.max_buffer_size:
                logging.warning("Bufor przekroczył limit bez znaku końca. Czyszczenie...")
                self.buffer = b""

            # Jeśli jest START, ale nie ma jeszcze END -> zwracamy None, 
            # dane zostają w buforze i czekają na kolejną porcję w następnej iteracji.
            return None

        except (serial.SerialException, OSError) as e:
            logging.error(f"Urządzenie rozłączone: {e}")
            if self.ser:
                try:
                    self.ser.close()
                except Exception:
                    pass
            self.ser = None
            return None