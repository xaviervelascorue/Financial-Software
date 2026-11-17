from decimal import Decimal

from finsoft import facturas, operaciones
from finsoft.entidades import add_participacion, crear_entidad, crear_linea
from finsoft.facturas import FacturaInput, LineaFactura
from finsoft.impuestos import renta


def test_ficha_renta_comunero_vut(conn):
    x = crear_entidad(conn, "XX", "Comunero", "22222222J", "autonomo")
    cb = crear_entidad(conn, "VUT", "CB VUT", "E11111111", "comunidad_bienes", "exento",
                       renta_atribuida="capital_inmobiliario", valor_catastral=80000,
                       valor_catastral_construccion=50000)
    add_participacion(conn, cb, 50, x)
    add_participacion(conn, cb, 50, titular_nombre="Hermana", titular_nif="33333333P")
    crear_linea(conn, cb, "VUT", "Vivienda")
    facturas.registrar(conn, cb, FacturaInput("emitida", "1", "2026-08-31", "", "Huéspedes",
                                              [LineaFactura(base=Decimal("10000"), tipo_iva=Decimal("0"))]))
    operaciones.movimiento_directo(conn, cb, "2026-03-01", "IBI", "6310", 730)
    operaciones.movimiento_directo(conn, cb, "2026-03-01", "Comunidad", "6295", 365)
    operaciones.movimiento_directo(conn, cb, "2026-08-01", "Limpiezas", "6230", 1000)
    f = renta.ficha_comunero(conn, x, 2026, dias_alquilado=146, dias_uso_propio=73)[0]
    g = f["gastos"]
    assert f["factor_prorrateo"] == 0.4
    assert g["tributos"] == 29200 and g["comunidad"] == 14600 and g["servicios"] == 100000
    assert g["amortizacion_inmueble"] == round(150000 * 0.4)  # 3 % de 50.000 € prorrateado
    assert f["rendimiento_neto"] == 1000000 - 29200 - 14600 - 100000 - 60000
    assert f["imputacion_rentas_100"] == round(8000000 * 0.011 * 73 / 365)
    assert f["atribuido"]["rendimiento_neto"] == f["rendimiento_neto"] // 2
    assert [c["nombre"] for c in f["comuneros"]] == ["Comunero", "Hermana"]
