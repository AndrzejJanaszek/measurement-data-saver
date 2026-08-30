import pytest

from src import config, database


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path):
    """
    Uruchamia się automatycznie przed KAŻDYM testem w całym katalogu tests/.
    Przekierowuje config.DB_PATH na tymczasowy plik i inicjalizuje schemat,
    żeby żaden test nie mógł przypadkiem dotknąć prawdziwej bazy z db/.
    Testy, które nie dotykają bazy w ogóle, po prostu nie zauważą tej fixture.
    """
    original_dir = config.DB_DIR
    original_path = config.DB_PATH

    temp_dir = tmp_path / "db"
    config.DB_DIR = temp_dir
    config.DB_PATH = str(temp_dir / "test_measurements.db")

    database.init_db()

    yield

    config.DB_DIR = original_dir
    config.DB_PATH = original_path
