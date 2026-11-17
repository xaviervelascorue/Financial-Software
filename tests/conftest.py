from pathlib import Path

import pytest

from finsoft import db
from finsoft.entidades import cargar_config, get_entidad

CONFIG = Path(__file__).resolve().parent.parent / "config" / "entidades.example.toml"


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    cargar_config(c, CONFIG)
    yield c
    c.close()


@pytest.fixture
def aut1(conn):
    return get_entidad(conn, "AUT1")


@pytest.fixture
def cb(conn):
    return get_entidad(conn, "CB")


@pytest.fixture
def hold(conn):
    return get_entidad(conn, "HOLD")
