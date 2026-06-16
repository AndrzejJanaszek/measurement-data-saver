
SERIAL_PORT = "/dev/ttyUSB0"
BAUDRATE = 9600
TIMEOUT = 0.1  # non-blocking read timeout

START_CHAR = b'\x02'
END_CHAR_1 = b'\x03'
END_CHAR_2 = None  

RECONNECT_DELAY = 5.0

SAVE_DELAY = 1.0

DB_PATH = "measurements.db"