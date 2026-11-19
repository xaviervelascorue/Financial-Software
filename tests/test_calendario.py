from datetime import date

from finsoft import calendario
from finsoft.entidades import actualizar_entidad, get_entidad


def test_calendario_por_entidad(conn):
    actualizar_entidad(conn, get_entidad(conn, "HOLD"), {"anio_inicio_actividad": 2026})
    actualizar_entidad(conn, get_entidad(conn, "CB"), {"regimen_iva": "exento"})
    obs = calendario.obligaciones(conn, 2026)
    by = {(o.entidad, o.modelo, o.periodo): o for o in obs}
    assert by[("AUT1", "303", "4T")].fecha == date(2027, 2, 1)       # 30/01/2027 es sábado
    assert by[("AUT1", "303", "1T")].domiciliacion == date(2026, 4, 15)
    assert ("AUT1", "100", "2026") in by and ("CB", "184", "2026") in by
    assert "primer ejercicio" in by[("HOLD", "202", "1P")].condicion
    assert ("HOLD", "200", "2026") in by and ("HOLD", "Depósito", "2026") in by
    assert ("HOLD", "303", "1T") not in by                            # holding no sujeta
    assert by[("CB", "303", "1T")].condicion.startswith("Sólo si")   # CB exenta: sólo por inversión del sujeto pasivo
    assert sum(1 for o in obs if o.modelo == "Envío") == 12
