from decimal import Decimal

import pytest

from finsoft import facturas, informes, ledger, operaciones
from finsoft.cierre import paquete_anual, paquete_trimestral, paquete_grupo
from finsoft.estados import balance, cierre_regularizacion, pyg
from finsoft.facturas import FacturaInput, LineaFactura
from finsoft.impuestos import informativas, irpf, iva, sociedades


def fra(tipo, numero, fecha, nif, nombre, lineas, **kw):
    return FacturaInput(tipo=tipo, numero=numero, fecha=fecha, tercero_nif=nif, tercero_nombre=nombre,
                        lineas=[LineaFactura(**ln) for ln in lineas], **kw)


def cargar_autonomo(conn, aut1):
    facturas.registrar(conn, aut1, fra("emitida", "2026-001", "2026-01-15", "B11111111", "Cliente SL",
                                       [dict(base=Decimal("2000"), linea="SERV", concepto="Consultoría")],
                                       retencion_tipo="profesional", retencion_pct=Decimal("15")))
    facturas.registrar(conn, aut1, fra("recibida", "A-889", "2026-01-20", "B22222222", "Informática SL",
                                       [dict(base=Decimal("1200"), cuenta="2170", concepto="Portátil")],
                                       bien_inversion=True))
    facturas.registrar(conn, aut1, fra("recibida", "F-12", "2026-02-03", "B33333333", "Telefonía SA",
                                       [dict(base=Decimal("60"), cuenta="6280", concepto="Fibra")]))


def apuntes(conn, factura_id):
    aid = conn.execute("SELECT asiento_id FROM facturas WHERE id=?", (factura_id,)).fetchone()[0]
    return {r["cuenta"]: (r["debe"], r["haber"]) for r in
            conn.execute("SELECT cuenta, debe, haber FROM apuntes WHERE asiento_id=?", (aid,))}


def test_asiento_factura_emitida_con_retencion(conn, aut1):
    fid = facturas.registrar(conn, aut1, fra("emitida", "1", "2026-01-15", "B11111111", "Cliente SL",
                                             [dict(base=Decimal("2000"))], retencion_tipo="profesional",
                                             retencion_pct=Decimal("15")))
    a = apuntes(conn, fid)
    assert a["4300"] == (212000, 0)
    assert a["4730"] == (30000, 0)
    assert a["7050"] == (0, 200000)
    assert a["4770"] == (0, 42000)


def test_factura_recibida_holding_no_sujeta_iva_es_gasto(conn, hold):
    fid = facturas.registrar(conn, hold, fra("recibida", "AS-1", "2026-02-10", "00000009X", "Asesor", [
        dict(base=Decimal("500"), cuenta="6230")], retencion_tipo="profesional", retencion_pct=Decimal("15")))
    a = apuntes(conn, fid)
    assert a["6230"] == (60500, 0)  # IVA no deducible incorporado al gasto
    assert "4720" not in a
    assert a["4751"] == (0, 7500)
    assert a["4100"] == (0, 53000)
    m111 = irpf.modelo_111(conn, hold, 2026, 1)
    assert m111["casillas"]["08"] == 50000 and m111["casillas"]["09"] == 7500
    assert iva.modelo_303(conn, hold, 2026, 1)["obligado"] is False


def test_duplicada(conn, aut1):
    f = fra("recibida", "X1", "2026-01-01", "B33333333", "Telefonía SA", [dict(base=Decimal("10"))])
    facturas.registrar(conn, aut1, f)
    with pytest.raises(facturas.FacturaDuplicada):
        facturas.registrar(conn, aut1, f)


def test_aprende_cuenta_habitual(conn, aut1):
    facturas.registrar(conn, aut1, fra("recibida", "1", "2026-01-01", "B33333333", "Telefonía SA",
                                       [dict(base=Decimal("10"), cuenta="6280")]))
    p = facturas.preparar(conn, aut1, fra("recibida", "2", "2026-02-01", "B33333333", "Telefonía SA",
                                          [dict(base=Decimal("10"))]))
    assert p["lineas"][0]["cuenta"] == "6280"


def test_modelo_303(conn, aut1):
    cargar_autonomo(conn, aut1)
    c = iva.modelo_303(conn, aut1, 2026, 1)["casillas"]
    assert c["07"] == 200000 and c["09"] == 42000
    assert c["30"] == 120000 and c["31"] == 25200
    assert c["28"] == 6000 and c["29"] == 1260
    assert c["27"] == 42000 and c["45"] == 26460
    assert c["71"] == 15540


def test_303_compensacion_trimestre_siguiente(conn, aut1):
    facturas.registrar(conn, aut1, fra("recibida", "1", "2026-01-10", "B22222222", "Prov", [dict(base=Decimal("1000"))]))
    facturas.registrar(conn, aut1, fra("emitida", "1", "2026-04-10", "B11111111", "Cli", [dict(base=Decimal("2000"))]))
    t1 = iva.modelo_303(conn, aut1, 2026, 1)
    assert t1["resultado"] == -21000
    t2 = iva.modelo_303(conn, aut1, 2026, 2)
    assert t2["casillas"]["67"] == 21000
    assert t2["resultado"] == 42000 - 21000


def test_modelo_130_con_atribucion_cb(conn, aut1, cb):
    cargar_autonomo(conn, aut1)
    m = irpf.modelo_130(conn, aut1, 2026, 1)["casillas"]
    assert m["01"] == 200000 and m["02"] == 6000 + 9700  # 5 % difícil justificación sobre 1.940
    assert m["04"] == 36860 and m["06"] == 30000 and m["19"] == 6860

    facturas.registrar(conn, cb, fra("emitida", "CB-1", "2026-02-01", "B44444444", "Inquilino SL",
                                     [dict(base=Decimal("1000"), linea="ALQ1")], retencion_tipo="alquiler",
                                     retencion_pct=Decimal("19")))
    operaciones.movimiento_directo(conn, cb, "2026-03-01", "IBI", "6310", "300", linea="ALQ1")
    reparto = irpf.atribucion_rentas(conn, cb, "2026-01-01", "2026-03-31")
    assert [r["rendimiento"] for r in reparto] == [35000, 35000]
    r = irpf.modelo_130(conn, aut1, 2026, 1)
    c = r["casillas"]
    assert c["01"] == 250000 and c["02"] == 21000 + 11450
    assert c["06"] == 30000 + 9500
    assert c["19"] == 43510 - 39500
    assert r["atribucion_cb"][0]["cb"] == "CB"


def test_130_resta_pagos_previos(conn, aut1):
    facturas.registrar(conn, aut1, fra("emitida", "1", "2026-01-10", "B1", "Cli", [dict(base=Decimal("10000"))]))
    t1 = irpf.modelo_130(conn, aut1, 2026, 1)
    facturas.registrar(conn, aut1, fra("emitida", "2", "2026-04-10", "B1", "Cli", [dict(base=Decimal("10000"))]))
    t2 = irpf.modelo_130(conn, aut1, 2026, 2)
    assert t2["casillas"]["05"] == t1["casillas"]["07"]
    assert t2["resultado"] == t2["casillas"]["04"] - t1["casillas"]["07"]


def test_balance_cuadra_y_pyg(conn, aut1):
    cargar_autonomo(conn, aut1)
    operaciones.dotar_amortizaciones(conn, aut1, "2026-03-31")
    b = balance(conn, aut1, "2026-03-31")
    assert b["cuadra"] and not b["sin_clasificar"]
    p = pyg(conn, aut1, "2026-01-01", "2026-03-31")
    amort = p["partidas"]["8. Amortización del inmovilizado"]
    # portátil 1.452 € (IVA no deducible: 0) → 1.200 €, 4 años, 71 días
    assert amort == -round(120000 / 4 * 71 / 365)
    assert p["D) RESULTADO DEL EJERCICIO"] == 200000 - 6000 + amort


def test_holding_inversiones_e_is(conn, hold):
    operaciones.movimiento_directo(conn, hold, "2026-01-02", "Aportación socios", "7780", "0.01")  # sin efecto relevante
    operaciones.compra_inversion(conn, hold, "2026-01-10", "Compra ETF", "5400", "10000", linea="CARTERA")
    operaciones.dividendo_recibido(conn, hold, "2026-03-10", "Dividendo ETF", "1000", linea="CARTERA")
    operaciones.interes_recibido(conn, hold, "2026-03-31", "Intereses depósito", "100", linea="DEP")
    facturas.registrar(conn, hold, fra("recibida", "AS-1", "2026-02-10", "00000009X", "Asesor",
                                       [dict(base=Decimal("500"), cuenta="6230")]))
    est = sociedades.estimacion_is(conn, hold, 2026)
    assert est["resultado_contable_antes_impuestos"] == 100000 + 10000 - 60500 + 1
    assert est["cuota_integra"] == round(est["base_imponible"] * 0.25)
    assert est["retenciones"] == 19000 + 1900
    rent = {r["codigo"]: r for r in informes.rentabilidad_por_linea(conn, hold, "2026-01-01", "2026-03-31")["lineas"]}
    assert rent["CARTERA"]["ingresos"] == 100000 and rent["CARTERA"]["capital_invertido"] == 1000000
    assert rent["CARTERA"]["rentabilidad_capital_anual_pct"] is not None
    # holding: dividendos del grupo en cifra de negocios; de otras empresas, ingresos financieros
    p = pyg(conn, hold, "2026-01-01", "2026-03-31")
    assert p["partidas"]["13. Ingresos financieros"] == 110000
    assert balance(conn, hold, "2026-03-31")["cuadra"]
    sociedades.contabilizar_is(conn, hold, est)
    assert sociedades.estimacion_is(conn, hold, 2026)["retenciones"] == est["retenciones"]


def test_exencion_art21_y_202(conn, hold):
    operaciones.dividendo_recibido(conn, hold, "2026-02-01", "Dividendo filial", "10000", retencion_pct=0,
                                   grupo=True, exento_art21=True)
    est = sociedades.estimacion_is(conn, hold, 2026)
    assert est["total_ajustes"] == -950000
    assert est["base_imponible"] == 50000
    m = sociedades.modelo_202(conn, hold, 2026, 1, modalidad="40.3")
    assert m["detalle"]["porcentaje"] == 17
    assert m["resultado"] == round(50000 * 0.17)
    assert pyg(conn, hold, "2026-01-01", "2026-03-31")["partidas"]["1. Importe neto de la cifra de negocios"] == 1000000


def test_reparto_dividendo_modelo_123(conn, hold):
    operaciones.distribuir_dividendo(conn, hold, "2026-06-30", "1000")
    m = irpf.modelo_123(conn, hold, 2026, 2)
    assert m["casillas"]["02"] == 100000 and m["casillas"]["03"] == 19000 and m["casillas"]["01"] == 2


def test_347(conn, aut1):
    for i in range(2):
        facturas.registrar(conn, aut1, fra("recibida", f"G{i}", f"2026-0{i + 1}-05", "B55555555", "Gestoría SL",
                                           [dict(base=Decimal("1500"))]))
    facturas.registrar(conn, aut1, fra("emitida", "P1", "2026-03-01", "B66666666", "Cliente con retención",
                                       [dict(base=Decimal("5000"))], retencion_tipo="profesional",
                                       retencion_pct=Decimal("15")))
    m = informativas.modelo_347(conn, aut1, 2026)
    assert [(d["nif"], d["clave"], d["total"]) for d in m["declarados"]] == [("B55555555", "A", 363000)]


def test_cobro_pago_y_pendientes(conn, aut1):
    fid = facturas.registrar(conn, aut1, fra("emitida", "1", "2026-01-15", "B1", "Cli", [dict(base=Decimal("100"))]))
    facturas.registrar_cobro_pago(conn, aut1, fid, "2026-01-30", "50")
    assert facturas.pendiente(conn, fid) == 7100
    with pytest.raises(ValueError):
        facturas.registrar_cobro_pago(conn, aut1, fid, "2026-01-31", "1000")
    assert ledger.saldo(conn, aut1, "5720") == 5000


def test_liquidacion_iva_salda_cuentas(conn, aut1):
    cargar_autonomo(conn, aut1)
    iva.contabilizar_303(conn, aut1, iva.modelo_303(conn, aut1, 2026, 1))
    assert ledger.saldo(conn, aut1, "4770") == 0 and ledger.saldo(conn, aut1, "4720") == 0
    assert ledger.saldo(conn, aut1, "4750") == -15540


def test_regularizacion(conn, aut1):
    cargar_autonomo(conn, aut1)
    antes = pyg(conn, aut1, "2026-01-01", "2026-12-31")["D) RESULTADO DEL EJERCICIO"]
    cierre_regularizacion(conn, aut1, 2026)
    assert ledger.saldo(conn, aut1, "1290") == -antes
    assert ledger.saldo(conn, aut1, "6") == 0 and ledger.saldo(conn, aut1, "7") == 0
    assert pyg(conn, aut1, "2026-01-01", "2026-12-31")["D) RESULTADO DEL EJERCICIO"] == antes
    assert balance(conn, aut1, "2026-12-31")["cuadra"]
    with pytest.raises(ValueError):
        cierre_regularizacion(conn, aut1, 2026)


def test_asiento_descuadrado(conn, aut1):
    with pytest.raises(ledger.AsientoDescuadrado):
        ledger.crear_asiento(conn, aut1, "2026-01-01", "x", [ledger.Apunte("5720", debe=10), ledger.Apunte("7050", haber=9)])


def test_paquetes_excel(conn, aut1, cb, hold, tmp_path):
    from openpyxl import load_workbook
    cargar_autonomo(conn, aut1)
    ruta = paquete_trimestral(conn, aut1, 2026, 1, tmp_path)
    hojas = load_workbook(ruta).sheetnames
    assert hojas[0] == "Resumen fiscal"
    for h in ("303", "130", "Balance", "PyG", "Gestión", "Rentabilidad líneas", "Libro emitidas"):
        assert h in hojas
    assert "202" in load_workbook(paquete_trimestral(conn, hold, 2026, 1, tmp_path)).sheetnames
    assert "Atribución CB" in load_workbook(paquete_trimestral(conn, cb, 2026, 1, tmp_path)).sheetnames
    assert paquete_grupo(conn, 2026, 1, tmp_path).exists()
    anual = load_workbook(paquete_anual(conn, cb, 2026, tmp_path)).sheetnames
    assert "184" in anual and "390" in anual and "347" in anual


def test_comparativa_trimestre_anterior(conn, aut1):
    facturas.registrar(conn, aut1, fra("emitida", "Q4", "2026-11-10", "B1", "Cli", [dict(base=Decimal("1000"))]))
    facturas.registrar(conn, aut1, fra("emitida", "Q1", "2027-02-10", "B1", "Cli", [dict(base=Decimal("1500"))]))
    ig = informes.informe_gestion(conn, aut1, 2027, 1)
    k = ig["kpis"][0]
    assert ig["trimestre_anterior"] == "4T 2026"
    assert k["actual"] == 150000 and k["trim_anterior"] == 100000 and k["variacion_trim_pct"] == 50.0
    from finsoft.estados import pyg_filas
    fila = pyg_filas(conn, aut1, 2027, 1)[0]
    assert fila["Trim. anterior (4T 2026)"] == 100000


def test_cli_apertura_y_asiento(tmp_path):
    import json
    from click.testing import CliRunner
    from finsoft.cli import main
    from tests.conftest import CONFIG
    dbp = str(tmp_path / "t.db")
    r = CliRunner()
    assert r.invoke(main, ["--db", dbp, "init", "--config", str(CONFIG)]).exit_code == 0
    csvf = tmp_path / "saldos.csv"
    csvf.write_text("cuenta;saldo\n5720;1.500,00\n1020;-1.500,00\n", encoding="utf-8")
    res = r.invoke(main, ["--db", dbp, "apertura", "--entidad", "AUT1", "--fecha", "2026-10-01", str(csvf)])
    assert res.exit_code == 0, res.output
    js = tmp_path / "a.json"
    js.write_text(json.dumps({"fecha": "2026-10-05", "concepto": "Comisión", "apuntes": [
        {"cuenta": "6260", "debe": 5}, {"cuenta": "5720", "haber": 5}]}))
    assert r.invoke(main, ["--db", dbp, "asiento", "--entidad", "AUT1", str(js)]).exit_code == 0
    malo = tmp_path / "m.csv"
    malo.write_text("cuenta;saldo\n5720;10\n", encoding="utf-8")
    assert r.invoke(main, ["--db", dbp, "apertura", "--entidad", "AUT1", "--fecha", "2026-10-01", str(malo)]).exit_code != 0


def test_347_excluye_factura_resumen_particulares(conn, aut1):
    facturas.registrar(conn, aut1, fra("emitida", "R-01", "2026-01-31", "", "Clientes particulares (resumen enero)",
                                       [dict(base=Decimal("4000"))]))
    assert informativas.modelo_347(conn, aut1, 2026)["declarados"] == []


def test_sustituir_factura_emitida_corregida(conn, aut1):
    f1 = fra("emitida", "2026-002", "2026-07-02", "", "Cliente SL", [dict(base=Decimal("100"))])
    fid = facturas.registrar(conn, aut1, f1)
    f2 = fra("emitida", "2026-002", "2026-07-02", "B12345674", "Cliente SL", [dict(base=Decimal("100"))])
    with pytest.raises(facturas.FacturaDuplicada):
        facturas.registrar(conn, aut1, f2)
    nuevo = facturas.registrar(conn, aut1, f2, sustituir=True)
    assert conn.execute("SELECT COUNT(*) FROM facturas").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM asientos").fetchone()[0] == 1
    assert fid and ledger.saldo(conn, aut1, "7050") == -10000
    facturas.registrar_cobro_pago(conn, aut1, nuevo, "2026-07-30")
    with pytest.raises(ValueError):
        facturas.eliminar(conn, aut1, nuevo)
