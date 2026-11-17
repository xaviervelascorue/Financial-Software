from decimal import Decimal

from finsoft import facturas, ledger
from finsoft.entidades import add_participacion, crear_entidad, crear_linea, get_entidad
from finsoft.facturas import FacturaInput, LineaFactura
from finsoft.impuestos import irpf, iva


def fra(tipo, numero, fecha, lineas, nif="B1", **kw):
    return FacturaInput(tipo=tipo, numero=numero, fecha=fecha, tercero_nif=nif, tercero_nombre="Tercero",
                        lineas=[LineaFactura(**ln) for ln in lineas], **kw)


def test_sectores_diferenciados_y_regularizacion_prorrata(conn):
    x = crear_entidad(conn, "XV", "Profesional mixto", "11111111H", "autonomo", "prorrata", prorrata_pct=40,
                      sectores_diferenciados=1)
    crear_linea(conn, x, "CONSULT", "Consultoría")
    crear_linea(conn, x, "DOCENCIA", "Docencia", iva_exento=True)
    facturas.registrar(conn, x, fra("emitida", "1", "2026-10-05", [dict(base=Decimal("10000"), linea="CONSULT")]))
    fi = fra("emitida", "2", "2026-10-06", [dict(base=Decimal("10000"), linea="DOCENCIA")])
    p = facturas.preparar(conn, x, fi)
    assert p["cuota"] == 0 and p["tipo_operacion"] == "exenta" and p["avisos"]
    facturas.registrar(conn, x, fi)
    f_c = facturas.registrar(conn, x, fra("recibida", "A", "2026-10-10", [dict(base=Decimal("100"), linea="CONSULT")], nif="B2"))
    f_d = facturas.registrar(conn, x, fra("recibida", "B", "2026-10-11", [dict(base=Decimal("100"), linea="DOCENCIA")], nif="B3"))
    f_m = facturas.registrar(conn, x, fra("recibida", "C", "2026-10-12", [dict(base=Decimal("100"))], nif="B4"))
    ded = {fid: conn.execute("SELECT cuota_deducible FROM facturas WHERE id=?", (fid,)).fetchone()[0]
           for fid in (f_c, f_d, f_m)}
    assert ded == {f_c: 2100, f_d: 0, f_m: 840}

    pr = iva.prorrata_definitiva(conn, x, 2026)
    assert pr["prorrata_definitiva"] == 50 and pr["regularizacion"] == 1050 - 840
    m = iva.modelo_303(conn, x, 2026, 4)
    c = m["casillas"]
    assert c["44"] == 210 and c["45"] == 2100 + 840 + 210
    assert c["71"] == 210000 - c["45"]
    iva.contabilizar_303(conn, x, m)
    assert ledger.saldo(conn, x, "4720") == 0 and ledger.saldo(conn, x, "6390") == -210


def test_cb_capital_inmobiliario_no_va_al_130(conn):
    a = crear_entidad(conn, "A1", "Comunero", "22222222J", "autonomo")
    cb = crear_entidad(conn, "VUT", "CB vivienda turística", "E11111111", "comunidad_bienes", "exento",
                       renta_atribuida="capital_inmobiliario")
    add_participacion(conn, cb, 50, a)
    fi = fra("emitida", "R1", "2026-11-01", [dict(base=Decimal("1000"), tipo_iva=Decimal("10"))])
    assert facturas.preparar(conn, cb, fi)["cuota"] == 0
    facturas.registrar(conn, cb, fi)
    m = irpf.modelo_130(conn, a, 2026, 4)
    assert m["casillas"]["01"] == 0 and not m["atribucion_cb"]
    r = irpf.resumen_anual_autonomo(conn, a, 2026)
    assert r["atribuido_cb_capital_inmobiliario"]["total"]["rendimiento"] == 50000
    assert iva.modelo_303(conn, cb, 2026, 4)["obligado"] is False
    assert irpf.modelo_184(conn, cb, 2026)["tipo_renta"] == "capital_inmobiliario"


def test_migracion_bd_existente(tmp_path):
    import sqlite3
    from finsoft import db
    ruta = tmp_path / "vieja.db"
    c = sqlite3.connect(ruta)
    c.execute("CREATE TABLE lineas (id INTEGER PRIMARY KEY, entidad_id INTEGER, codigo TEXT, nombre TEXT,"
              " tipo TEXT, capital_invertido INTEGER, UNIQUE(entidad_id, codigo))")
    c.commit()
    c.close()
    conn = db.connect(ruta)
    assert "iva_exento" in {r["name"] for r in conn.execute("PRAGMA table_info(lineas)")}
