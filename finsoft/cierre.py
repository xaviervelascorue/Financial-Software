"""Paquetes de cierre: trimestral (estados, impuestos e informes en un Excel por entidad) y anual."""

from pathlib import Path

from . import informes, ledger
from .entidades import Entidad, listar_entidades
from .estados import balance_filas, cierre_regularizacion, pyg_filas
from .excel import Libro
from .fechas import acumulado, ejercicio as rango_ejercicio, trimestre
from .impuestos import informativas, iva, irpf, sociedades
from .operaciones import dotar_amortizaciones

PERIODO_202 = {1: 1, 3: 2, 4: 3}  # trimestre en el que se presenta → periodo (abril, octubre, diciembre)


def _hojas_estados(lib: Libro, conn, e: Entidad, ejercicio: int, t: int):
    fin = trimestre(ejercicio, t)[1]
    fin_ant = f"{ejercicio - 1}-12-31"
    cols = [("concepto", "Concepto"), ("actual", fin), ("comparativo", fin_ant)]
    b = balance_filas(conn, e, fin, fin_ant)
    lib.hoja("Balance", f"Balance de situación abreviado · {e.nombre}", cols, b, {"actual", "comparativo"},
             f"NIF {e.nif} · a {fin}", nivel_key="nivel")
    p = pyg_filas(conn, e, ejercicio, t)
    cols = [("concepto", "Concepto")] + [(k, k) for k in p[0] if k not in ("concepto", "nivel")]
    lib.hoja("PyG", f"Cuenta de pérdidas y ganancias abreviada · {e.nombre}", cols, p,
             {k for k, _ in cols if k != "concepto"}, f"Ejercicio {ejercicio}, hasta {t}T", nivel_key="nivel")
    da, ha = acumulado(ejercicio, t)
    lib.hoja("Sumas y saldos", "Balance de sumas y saldos", [
        ("cuenta", "Cuenta"), ("nombre", "Nombre"), ("debe", "Debe"), ("haber", "Haber"),
        ("saldo_deudor", "Saldo deudor"), ("saldo_acreedor", "Saldo acreedor")],
        ledger.sumas_y_saldos(conn, e, da, ha), {"debe", "haber", "saldo_deudor", "saldo_acreedor"}, f"{da} a {ha}")
    dq, hq = trimestre(ejercicio, t)
    lib.hoja("Diario", f"Libro diario {t}T {ejercicio}", [
        ("numero", "Asiento"), ("fecha", "Fecha"), ("concepto", "Concepto"), ("cuenta", "Cuenta"),
        ("nombre_cuenta", "Nombre cuenta"), ("tercero", "Tercero"), ("debe", "Debe"), ("haber", "Haber")],
        ledger.diario(conn, e, dq, hq), {"debe", "haber"})


def _hojas_gestion(lib: Libro, conn, e: Entidad, ejercicio: int, t: int):
    ig = informes.informe_gestion(conn, e, ejercicio, t)
    lib.hoja("Gestión", f"Informe de gestión {t}T {ejercicio} · {e.nombre}", [
        ("indicador", "Indicador"), ("actual", str(ejercicio)), ("trim_anterior", f"Trim. anterior ({ig['trimestre_anterior']})"),
        ("variacion_trim_pct", "Var. % s/trim. anterior"), ("anterior", str(ejercicio - 1)),
        ("variacion_pct", "Var. % s/año anterior")], ig["kpis"], {"actual", "anterior", "trim_anterior"},
        notas=[f"Tesorería al cierre: {ig['tesoreria'] / 100:,.2f} €",
               f"Pendiente de cobro: {ig['pendiente_cobro'] / 100:,.2f} €",
               f"Pendiente de pago: {ig['pendiente_pago'] / 100:,.2f} €"]
        + [f"{k.replace('_', ' ')}: {v}" for k, v in ig["ratios"].items()]
        + [f"ALERTA: {a}" for a in ig["alertas"]])
    lib.hoja("Rentabilidad líneas", f"Rentabilidad por línea de ingreso (acumulado {t}T {ejercicio})", [
        ("codigo", "Código"), ("linea", "Línea"), ("tipo", "Tipo"), ("ingresos", "Ingresos"),
        ("costes_directos", "Costes directos"), ("margen_contribucion", "Margen contribución"),
        ("margen_pct", "Margen %"), ("gastos_estructura", "Gastos estructura"), ("resultado", "Resultado"),
        ("resultado_pct", "Resultado %"), ("peso_ingresos_pct", "Peso %"), ("capital_invertido", "Capital invertido"),
        ("rentabilidad_capital_anual_pct", "Rent. s/capital anual %")], ig["rentabilidad_lineas"],
        {"ingresos", "costes_directos", "margen_contribucion", "gastos_estructura", "resultado", "capital_invertido"},
        notas=["Los gastos sin línea asignada se reparten en proporción a los ingresos de cada línea."])
    lib.hoja("Gastos naturaleza", "Gastos por naturaleza (acumulado)",
             [("cuenta", "Cuenta"), ("nombre", "Nombre"), ("importe", "Importe")], ig["gastos_por_naturaleza"], {"importe"})
    top = [dict(x, tipo="Cliente") for x in ig["top_clientes"]] + [dict(x, tipo="Proveedor") for x in ig["top_proveedores"]]
    lib.hoja("Top terceros", "Principales clientes y proveedores (acumulado)", [
        ("tipo", "Tipo"), ("nombre", "Nombre"), ("nif", "NIF"), ("facturas", "Nº facturas"), ("base", "Base imponible")],
        top, {"base"})
    return ig


def _hojas_impuestos(lib: Libro, conn, e: Entidad, ejercicio: int, t: int) -> list[dict]:
    resumen = []
    m303 = iva.modelo_303(conn, e, ejercicio, t)
    if m303["obligado"]:
        lib.casillas("303", f"Modelo 303 · IVA {t}T {ejercicio}", m303["casillas"], iva.NOMBRES_303,
                     f"{e.nombre} · NIF {e.nif} · {m303['tipo_resultado']}", notas=[m303["aviso"]])
        resumen.append({"modelo": "303", "periodo": f"{t}T", "resultado": m303["resultado"], "nota": m303["tipo_resultado"]})
    else:
        resumen.append({"modelo": "303", "periodo": f"{t}T", "resultado": 0, "nota": m303["motivo"]})
    if e.tipo == "autonomo":
        m130 = irpf.modelo_130(conn, e, ejercicio, t)
        lib.casillas("130", f"Modelo 130 · IRPF pago fraccionado {t}T {ejercicio}", m130["casillas"],
                     {"01": "Ingresos computables acumulados", "02": "Gastos deducibles acumulados (incl. difícil justificación)",
                      "03": "Rendimiento neto", "04": "20 % de la casilla 03", "05": "Pagos fraccionados de trimestres anteriores",
                      "06": "Retenciones e ingresos a cuenta", "07": "Pago fraccionado previo", "12": "Total",
                      "13": "Minoración art. 110.3.c RIRPF", "14": "Diferencia", "15": "Resultados negativos anteriores",
                      "17": "Total", "19": "Resultado de la autoliquidación"},
                     f"{e.nombre} · NIF {e.nif} · {m130['tipo_resultado']}",
                     notas=m130["avisos"] + [f"Atribución CB: {a['cb']} {a['porcentaje']} % → rendimiento "
                                             f"{a['rendimiento'] / 100:,.2f} €" for a in m130["atribucion_cb"]] + [m130["aviso"]])
        resumen.append({"modelo": "130", "periodo": f"{t}T", "resultado": m130["resultado"],
                        "nota": "No presentar (exento)" if not m130["presentar"] else m130["tipo_resultado"]})
    if e.tipo == "comunidad_bienes":
        da, ha = acumulado(ejercicio, t)
        lib.hoja("Atribución CB", f"Atribución de rentas a comuneros (acumulado {t}T)", [
            ("comunero", "Comunero"), ("nif", "NIF"), ("porcentaje", "%"), ("ingresos", "Ingresos"),
            ("gastos", "Gastos"), ("rendimiento", "Rendimiento"), ("retenciones", "Retenciones")],
            irpf.atribucion_rentas(conn, e, da, ha), {"ingresos", "gastos", "rendimiento", "retenciones"},
            notas=["Cada comunero incluye su parte en su propio modelo 130 y en su declaración de la renta."])
    if e.tipo == "sociedad" and t in PERIODO_202:
        m202 = sociedades.modelo_202(conn, e, ejercicio, PERIODO_202[t])
        lib.clave_valor("202", f"Modelo 202 · Pago fraccionado IS {m202['periodo']} {ejercicio}", m202["detalle"],
                        {k for k in m202["detalle"] if k != "porcentaje"}, f"{e.nombre} · {m202['modalidad']}",
                        notas=m202["avisos"] + [m202["aviso"]])
        resumen.append({"modelo": "202", "periodo": m202["periodo"], "resultado": m202["resultado"], "nota": m202["modalidad"]})
    for fn, modelo in ((irpf.modelo_111, "111"), (irpf.modelo_115, "115"), (irpf.modelo_123, "123")):
        m = fn(conn, e, ejercicio, t)
        if m["presentar"]:
            lib.casillas(modelo, f"Modelo {modelo} · Retenciones {t}T {ejercicio}", m["casillas"], subtitulo=e.nombre,
                         notas=[m["aviso"]], no_moneda=("01", "07"))
            resumen.append({"modelo": modelo, "periodo": f"{t}T", "resultado": m["resultado"], "nota": "A ingresar"})
    dq, hq = trimestre(ejercicio, t)
    cols_libro = [("fecha", "Fecha"), ("numero", "Número"), ("nif", "NIF"), ("nombre", "Nombre"),
                  ("tipo_operacion", "Operación"), ("concepto", "Concepto"), ("base", "Base"), ("tipo_iva", "% IVA"),
                  ("cuota_iva", "Cuota"), ("retencion", "Retención"), ("total", "Total factura")]
    lib.hoja("Libro emitidas", f"Libro registro de facturas expedidas {t}T {ejercicio}", cols_libro,
             iva.libro_registro(conn, e, "emitida", dq, hq), {"base", "cuota_iva", "retencion", "total"})
    lib.hoja("Libro recibidas", f"Libro registro de facturas recibidas {t}T {ejercicio}",
             cols_libro[:9] + [("cuota_deducible_linea", "Cuota deducible")] + cols_libro[9:],
             iva.libro_registro(conn, e, "recibida", dq, hq),
             {"base", "cuota_iva", "cuota_deducible_linea", "retencion", "total"})
    return resumen


def paquete_trimestral(conn, e: Entidad, ejercicio: int, t: int, salida: str | Path = "informes",
                       amortizar: bool = True) -> Path:
    if amortizar:
        dotar_amortizaciones(conn, e, trimestre(ejercicio, t)[1])
    lib = Libro()
    resumen = _hojas_impuestos(lib, conn, e, ejercicio, t)
    lib.hoja("Resumen fiscal", f"Resumen de obligaciones fiscales {t}T {ejercicio} · {e.nombre}", [
        ("modelo", "Modelo"), ("periodo", "Periodo"), ("resultado", "Resultado"), ("nota", "Situación")],
        resumen, {"resultado"}, notas=["Plazo general de presentación: del 1 al 20 del mes siguiente al trimestre "
                                       "(4T: hasta el 30 de enero)."])
    lib.wb.move_sheet("Resumen fiscal", -(len(lib.wb.sheetnames) - 1))
    _hojas_estados(lib, conn, e, ejercicio, t)
    _hojas_gestion(lib, conn, e, ejercicio, t)
    return lib.guardar(Path(salida) / str(ejercicio) / f"{t}T" / f"{e.codigo}_{ejercicio}_{t}T.xlsx")


def paquete_grupo(conn, ejercicio: int, t: int, salida: str | Path = "informes") -> Path:
    g = informes.resumen_grupo(conn, ejercicio, t)
    lib = Libro()
    lib.hoja("Grupo", f"Resumen del grupo · acumulado {t}T {ejercicio}", [
        ("codigo", "Código"), ("nombre", "Entidad"), ("tipo", "Tipo"), ("ingresos", "Ingresos"), ("gastos", "Gastos"),
        ("resultado_antes_impuestos", "Resultado a.i."), ("tesoreria", "Tesorería"), ("patrimonio_neto", "Patrimonio neto"),
        ("total_activo", "Total activo")], g["entidades"],
        {"ingresos", "gastos", "resultado_antes_impuestos", "tesoreria", "patrimonio_neto", "total_activo"},
        notas=[g["nota"]])
    lib.hoja("Participaciones", "Estructura societaria", [
        ("titular", "Titular"), ("participada", "Participada"), ("porcentaje", "%"), ("coste", "Coste"),
        ("resultado_atribuible", "Resultado atribuible")], g["participaciones"], {"coste", "resultado_atribuible"})
    return lib.guardar(Path(salida) / str(ejercicio) / f"{t}T" / f"GRUPO_{ejercicio}_{t}T.xlsx")


def paquete_anual(conn, e: Entidad, ejercicio: int, salida: str | Path = "informes", cerrar: bool = False) -> Path:
    """Declaraciones anuales (390, 347, 184, IS / resumen renta). Con cerrar=True contabiliza el IS y regulariza."""
    if cerrar:
        dotar_amortizaciones(conn, e, rango_ejercicio(ejercicio)[1])
    lib = Libro()
    m390 = iva.modelo_390(conn, e, ejercicio)
    if m390["obligado"]:
        lib.casillas("390", f"Modelo 390 · Resumen anual IVA {ejercicio}", m390["casillas_acumuladas"], iva.NOMBRES_303,
                     e.nombre, notas=[f"Volumen de operaciones: {m390['volumen_operaciones'] / 100:,.2f} €", m390["aviso"]])
    m347 = informativas.modelo_347(conn, e, ejercicio)
    lib.hoja("347", f"Modelo 347 · Operaciones con terceros {ejercicio}", [
        ("clave", "Clave"), ("nif", "NIF"), ("nombre", "Nombre"), ("total", "Importe anual"), ("T1", "1T"), ("T2", "2T"),
        ("T3", "3T"), ("T4", "4T")], m347["declarados"], {"total", "T1", "T2", "T3", "T4"},
        notas=["Umbral: 3.005,06 € IVA incluido por tercero y año. " + m347["aviso"]])
    if e.tipo == "comunidad_bienes":
        m184 = irpf.modelo_184(conn, e, ejercicio)
        lib.hoja("184", f"Modelo 184 · Entidades en atribución de rentas {ejercicio}", [
            ("comunero", "Comunero"), ("nif", "NIF"), ("porcentaje", "%"), ("ingresos", "Ingresos"), ("gastos", "Gastos"),
            ("rendimiento", "Rendimiento neto"), ("retenciones", "Retenciones")], m184["comuneros"],
            {"ingresos", "gastos", "rendimiento", "retenciones"}, notas=m184["avisos"] + [m184["aviso"]])
    if e.tipo == "autonomo":
        r = irpf.resumen_anual_autonomo(conn, e, ejercicio)
        datos = {**{f"propia_{k}": v for k, v in r["actividad_propia"].items()},
                 **{f"cb_{k}": v for k, v in r["atribuido_cb"]["total"].items()},
                 "pagos_fraccionados_130": r["pagos_fraccionados_130_contabilizados"]}
        lib.clave_valor("Renta", f"Datos de actividad para la Renta {ejercicio}", datos, set(datos), e.nombre,
                        notas=[r["nota"]])
    if e.tipo == "sociedad":
        est = sociedades.estimacion_is(conn, e, ejercicio)
        datos = {k: v for k, v in est.items() if k not in ("ajustes", "avisos", "aviso", "modelo", "entidad", "ejercicio")}
        lib.clave_valor("IS", f"Estimación Impuesto sobre Sociedades {ejercicio}", datos,
                        {k for k in datos if k not in ("tipo_gravamen", "tipo_resultado")}, e.nombre,
                        notas=[f"{a['fecha']} {a['concepto']}: {a['importe'] / 100:,.2f} €" for a in est["ajustes"]]
                        + est["avisos"] + [est["aviso"]])
        if cerrar:
            sociedades.contabilizar_is(conn, e, est)
    if cerrar:
        cierre_regularizacion(conn, e, ejercicio)
    _hojas_estados(lib, conn, e, ejercicio, 4)
    return lib.guardar(Path(salida) / str(ejercicio) / "anual" / f"{e.codigo}_{ejercicio}_anual.xlsx")


def cerrar_trimestre_todas(conn, ejercicio: int, t: int, salida: str | Path = "informes") -> list[Path]:
    rutas = [paquete_trimestral(conn, e, ejercicio, t, salida) for e in listar_entidades(conn)]
    rutas.append(paquete_grupo(conn, ejercicio, t, salida))
    return rutas
