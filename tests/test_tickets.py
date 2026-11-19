from finsoft import banco, tickets
from finsoft.entidades import crear_entidad, crear_linea
from finsoft.impuestos import iva

EXTRACTO = """fecha;concepto;fecha valor;importe;saldo
01/12/2026;ENVIO BIZUM Terapia energetica;01/12/2026;33,00;100
01/12/2026;VENTAS T.C. OTRAS TARJ (OP.    1);01/12/2026;90,00;190
01/12/2026;VENTAS T.C. OTRAS TARJ (OP.    2);01/12/2026;123,00;223
01/12/2026;VENTAS T.C. OTRAS TARJ (OP.    3);01/12/2026;400,00;623
02/12/2026;ENVIO BIZUM Consulta astromedicina;02/12/2026;180,00;803
03/12/2026;ENVIO BIZUM devolucion consulta Astro;03/12/2026;-180,00;623
03/12/2026;ENVIO BIZUM curso intensivo astro;03/12/2026;450,00;1073
"""


def preparar(conn, tmp_path):
    e = crear_entidad(conn, "TK", "Terapeuta", "99999996Q", "autonomo", domicilio="Calle Mayor 1, Madrid")
    crear_linea(conn, e, "ASTROMED", "Astromedicina", palabras=["astro", "curso"], por_defecto=True)
    crear_linea(conn, e, "TERAPIA", "Terapia", palabras=["terapia"], multiplo_importe=33)
    ruta = tmp_path / "e.csv"
    ruta.write_text(EXTRACTO, encoding="utf-8")
    return e, banco.leer_movimientos(ruta)


def test_tiques_desde_banco(conn, tmp_path):
    e, movs = preparar(conn, tmp_path)
    r = tickets.desde_banco(conn, e, movs, fecha_expedicion="2026-12-03")
    nums = [t["numero"] for t in r["emitidos"]]
    assert nums[0] == "T2026-00001" and "R2026-00001" in nums
    totales = sorted(float(t["total"]) for t in r["emitidos"])
    assert totales == [-180.0, 33.0, 33.0, 90.0, 90.0, 180.0, 450.0]  # TPV 123 = 33 + 90
    assert len(r["pendientes"]) == 1                                  # TPV de 3 operaciones sin descomponer
    rect = next(t for t in r["emitidos"] if t["total"] < 0)
    assert rect["rectifica_id"] is not None
    assert any("400 €" in a for t in r["emitidos"] for a in t["avisos"])   # curso de 450 €
    assert any("más de un día" in a for t in r["emitidos"] for a in t["avisos"])
    # una segunda pasada no duplica
    assert tickets.desde_banco(conn, e, movs, fecha_expedicion="2026-12-03")["emitidos"] == []
    texto = tickets.texto_tique(conn, e, rect["id"])
    assert "RECTIFICATIVA" in texto and "Rectifica la factura simplificada T2026-" in texto and "NIF 99999996Q" in texto
    c = iva.modelo_303(conn, e, 2026, 4)["casillas"]
    assert c["07"] + c["09"] == round((33 + 90 + 33 + 90 + 180 + 450 - 180) * 100)


def test_no_fecha_hacia_atras(conn, tmp_path):
    import pytest
    e, _ = preparar(conn, tmp_path)
    with pytest.raises(ValueError):
        tickets.emitir(conn, e, "2026-12-05", 33, "Sesión", fecha_expedicion="2026-12-01")
