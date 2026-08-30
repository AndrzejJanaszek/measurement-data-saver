# src/serial_reader.py
import logging
import serial
from src import config
import time


class SerialReader:
    """
    Ogólny czytnik ramek z portu szeregowego.

    Parametryzowany, żeby jedna klasa obsługiwała dowolną liczbę
    niezależnych portów (miernik, Arduino, ...) - każdy z własną
    konfiguracją start/end char i baudrate.
    """

    def __init__(
        self,
        port: str,
        baudrate: int,
        timeout: float,
        start_char: bytes | None,
        end_char_1: bytes | None,
        end_char_2: bytes | None,
        reconnect_delay: float = config.RECONNECT_DELAY,
        name: str = "serial",
    ):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.start_char = start_char
        self.end_char_1 = end_char_1
        self.end_char_2 = end_char_2
        self.reconnect_delay = reconnect_delay
        self.name = name  # etykieta do logów, np. "miernik" / "arduino"

        self.ser = None
        self.buffer = b""
        self._connect()

    def _connect(self):
        try:
            self.ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=self.timeout
            )
            logging.info(f"[{self.name}] Połączono pomyślnie z {self.port}")
        except Exception as e:
            logging.error(f"[{self.name}] Nie udało się otworzyć portu: {e}")
            self.ser = None

    def read_next_frame(self):
        # Jeśli port nie jest otwarty, próba połączenia
        if not self.ser or not self.ser.is_open:
            self._connect()
            if not self.ser or not self.ser.is_open:
                time.sleep(self.reconnect_delay)  # Odczekaj przed kolejną próbą, jeśli się nie udało
                return None

        try:
            if self.ser.in_waiting > 0:
                self.buffer += self.ser.read(self.ser.in_waiting)

            # Budujemy sekwencję końca
            end_seq = self.end_char_1 if self.end_char_1 else b""
            if self.end_char_2:
                end_seq += self.end_char_2

            if not end_seq:
                logging.error(f"[{self.name}] Konfiguracja błędu: Brak zdefiniowanego znaku końca!")
                return None

            # Sprawdzamy, czy w buforze jest znacznik końca
            if end_seq in self.buffer:
                end_idx = self.buffer.index(end_seq)

                # PRZYPADEK A: Ze znakiem startu
                if self.start_char is not None:
                    if self.start_char in self.buffer:
                        start_idx = self.buffer.index(self.start_char)
                        if start_idx < end_idx:
                            frame = self.buffer[start_idx + len(self.start_char):end_idx]
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
            logging.error(f"[{self.name}] Błąd portu szeregowego: {e}. Czyszczenie i ponowna próba za {self.reconnect_delay}s...")
            self._handle_disconnect()
        except Exception as e:
            logging.error(f"[{self.name}] Nieoczekiwany błąd odczytu (np. Input/output error): {e}. Próba rekonfiguracji za {self.reconnect_delay}s...")
            self._handle_disconnect()

        return None

    def force_reconnect(self, reason: str = ""):
        """
        Wymusza zamknięcie i ponowne otwarcie portu, MIMO że pyserial nie
        zgłosił żadnego wyjątku. Używane przez workery, gdy port technicznie
        "działa" (żadnego błędu I/O), ale przez dłuższy czas nie napłynęła
        żadna poprawna ramka danych - to sygnał, że coś może być nie tak
        z połączeniem/urządzeniem, więc reset jest rozsądną pierwszą linią
        obrony (nie gwarantuje naprawy - jeśli urządzenie jest fizycznie
        odłączone lub zepsute, reconnect na tym poziomie nic nie da, ale
        SerialReader i tak będzie dalej próbował co reconnect_delay sekund).
        """
        suffix = f" ({reason})" if reason else ""
        logging.warning(f"[{self.name}] Wymuszam ponowne połączenie z portem{suffix}...")
        self._handle_disconnect()

    def _handle_disconnect(self):
        """Pomocnicza metoda do bezpiecznego czyszczenia zasobów po odpięciu kabla."""
        try:
            if self.ser:
                self.ser.close()
        except Exception:
            pass
        self.ser = None
        self.buffer = b""  # Czyścimy bufor ze starych, urwanych śmieci
        time.sleep(self.reconnect_delay)  # Kluczowe! Nie pozwalamy pętli zajechać procesora