from decimal import Decimal

from finsoft import banco, facturas, informes
from finsoft.entidades import crear_entidad, crear_linea
from finsoft.facturas import FacturaInput, LineaFactura

EXTRACTO = """fecha;concepto;fecha valor;importe;importe
01/07/2026;VENTAS T.C. OTRAS TARJ (OP.    1);01/07/2026;90,00;
01/07/2026;COMIS. OTRAS TARJ  G0594;01/07/2026;-0,54;
02/07/2026;VENTAS T.C. OTRAS TARJ (OP.    2);02/07/2026;123,00;
03/07/2026;ENVIO BIZUM Terapia energetica;03/07/2026;33,00;
04/07/2026;ENVIO BIZUM Sin concepto;04/07/2026;66,00;
05/07/2026;ENVIO BIZUM Astrologia medica 2;05/07/2026;231,00;
06/07/2026;ENVIO BIZUM devolucion consulta Astro;06/07/2026;-90,00;
07/07/2026;ENVIO BIZUM dinero prestado compras;07/07/2026;30,96;
08/07/2026;TRANSF. ADMON.COMUNIDAD DE MADRI;08/07/2026;960,00;
09/07/2026;RECIBO ORANGE ESPAGNE SA;09/07/2026;;-123,94
"""


def preparar(conn, tmp_path):
    e = crear_entidad(conn, "TT", "Terapeuta", "99999997R", "autonomo")
    crear_linea(conn, e, "ASTROMED", "Astromedicina", palabras=["astro", "curso"], por_defecto=True)
    crear_linea(conn, e, "TERAPIA", "Terapia", palabras=["terapia"], multiplo_importe=33)
    ruta = tmp_path / "extracto.csv"
    ruta.write_text(EXTRACTO, encoding="utf-8")
    return e, ruta


def test_clasifica_y_reparte_cobros(conn, tmp_path):
    e, ruta = preparar(conn, tmp_path)
    movs = banco.repartir_cobros(banco.leer_movimientos(ruta), banco.Reglas.de_entidad(conn, e))
    tpv = next(m for m in movs if m.importe == 123)
    assert tpv.reparto == {"TERAPIA": 33.0, "ASTROMED": 90.0}       # 2 operaciones: 90 + 33
    astro = next(m for m in movs if "Astrologia" in m.concepto)
    assert astro.reparto == {"ASTROMED": 231.0}                      # el concepto manda sobre el múltiplo
    assert next(m for m in movs if "prestado" in m.concepto).reparto == {}
    assert next(m for m in movs if "COMUNIDAD" in m.concepto).reparto == {}
    res = banco.resumen_mensual(movs)["2026-07"]
    assert res["TERAPIA"] == 33 + 33 + 66 and res["ASTROMED"] == 90 + 90 + 231 - 90
    assert res["_comisiones"] == -0.54


def test_repartir_factura_resumen(conn, tmp_path):
    e, ruta = preparar(conn, tmp_path)
    fid = facturas.registrar(conn, e, FacturaInput("emitida", "2026-007", "2026-07-31", "", "CLIENTES VARIOS",
                                                   [LineaFactura(base=Decimal("1000"), cuota_iva=Decimal("210"))]))
    nuevo = banco.repartir_factura(conn, e, fid, {"TERAPIA": 132.0, "ASTROMED": 321.0})
    ls = conn.execute("SELECT base, cuota_iva FROM factura_lineas WHERE factura_id=?", (nuevo,)).fetchall()
    assert sum(r["base"] for r in ls) == 100000 and sum(r["cuota_iva"] for r in ls) == 21000
    rent = {r["codigo"]: r for r in informes.rentabilidad_por_linea(conn, e, "2026-07-01", "2026-07-31")["lineas"]}
    assert rent["TERAPIA"]["ingresos"] == round(100000 * 132 / 453)


def test_extracto_con_columna_saldo(tmp_path):
    ruta = tmp_path / "e.csv"
    ruta.write_text("fecha;concepto;fecha valor;importe;saldo;\n01/01/2026;RECIBO X;01/01/2026;-10,00;990,00;nota\n"
                    "02/01/2026;ENVIO BIZUM Sesion;02/01/2026;33,00;1023,00;\n;;;23,00;;\n", encoding="utf-8")
    movs = banco.leer_movimientos(ruta)
    assert [m.importe for m in movs] == [-10.0, 33.0]
