import re

def parse_raw_frame(frame_bytes: bytes) -> float | None:
    """
    Przyjmuje surowe bajty ramki (bez znaków START i END).
    Dekoduje do stringa i wyciąga z niego pierwszą znalezioną liczbę float.
    Zwraca float lub None w przypadku błędu/braku danych.
    """
    if not frame_bytes:
        return None
        
    try:
        text_data = frame_bytes.decode('utf-8', errors='ignore').strip()
 
        match = re.search(r"[-+]?\d*\.\d+|\d+", text_data)
        
        if match:
            return float(match.group())
            
        return None
        
    except Exception:
        return None