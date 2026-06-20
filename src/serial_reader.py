# src/serial_reader.py
import logging
import serial
from src import config
import time

class SerialReader:
    def __init__(self):
        self.ser = None
        self.buffer = b""
        self._connect()

    def _connect(self):
        try:
            self.ser = serial.Serial(
                port=config.SERIAL_PORT,
                baudrate=config.BAUDRATE,
                timeout=config.TIMEOUT
            )
            logging.info(f"Połączono pomyślnie z {config.SERIAL_PORT}")
        except Exception as e:
            logging.error(f"Nie udało się otworzyć portu: {e}")
            self.ser = None

    def read_next_frame(self):
        # Jeśli port nie jest otwarty, próba połączenia
        if not self.ser or not self.ser.is_open:
            self._connect()
            if not self.ser or not self.ser.is_open:
                time.sleep(5)  # Odczekaj 5 sekund przed kolejną próbą, jeśli się nie udało
                return None

        try:
            if self.ser.in_waiting > 0:
                self.buffer += self.ser.read(self.ser.in_waiting)

            # Budujemy sekwencję końca
            end_seq = config.END_CHAR_1 if config.END_CHAR_1 else b""
            if config.END_CHAR_2:
                end_seq += config.END_CHAR_2

            if not end_seq:
                logging.error("Konfiguracja błędu: Brak zdefiniowanego znaku końca!")
                return None

            # Sprawdzamy, czy w buforze jest znacznik końca
            if end_seq in self.buffer:
                end_idx = self.buffer.index(end_seq)
                
                # PRZYPADEK A: Ze znakiem startu
                if config.START_CHAR is not None:
                    if config.START_CHAR in self.buffer:
                        start_idx = self.buffer.index(config.START_CHAR)
                        if start_idx < end_idx:
                            frame = self.buffer[start_idx + len(config.START_CHAR):end_idx]
                            self.buffer = self.buffer[end_idx + len(end_seq):]
                            return frame
                        else:
                            self.buffer = self.buffer[start_idx:]
                            return None
                    else:
                        self.buffer = self.buffer[end_idx + len(end_seq):]
                        return None

                # PRZYPADEK B: Bez znaku startu
                else:
                    frame = self.buffer[:end_idx]
                    self.buffer = self.buffer[end_idx + len(end_seq):]
                    return frame

        except serial.SerialException as e:
            logging.error(f"Błąd portu szeregowego: {e}. Czyszczenie i ponowna próba za 5s...")
            self._handle_disconnect()
        except Exception as e:
            logging.error(f"Nieoczekiwany błąd odczytu (np. Input/output error): {e}. Próba rekonfiguracji za 5s...")
            self._handle_disconnect()

        return None

    def _handle_disconnect(self):
        """Pomocnicza metoda do bezpiecznego czyszczenia zasobów po odpięciu kabla."""
        try:
            if self.ser:
                self.ser.close()
        except Exception:
            pass
        self.ser = None
        self.buffer = b""  # Czyścimy bufor ze starych, urwanych śmieci
        time.sleep(5)      # Kluczowe! Nie pozwalamy pętli zajechać procesora