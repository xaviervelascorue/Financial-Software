"""Informes de gestión: rentabilidad por línea de ingreso, informe trimestral de gestión y vista de grupo."""

from collections import defaultdict

from . import pgc
from .entidades import Entidad, lineas, listar_entidades, participadas
from .estados import balance, pyg
from .facturas import pendientes
from .fechas import acumulado, dias, trimestre
from .ledger import saldo, saldos_por_linea


def _var(actual, anterior):
    if not anterior:
        return None
    return round((actual - anterior) / abs(anterior) * 100, 1)


def rentabilidad_por_linea(conn, entidad: Entidad, desde: str, hasta: str) -> dict:
    """Margen por línea de ingreso. Los gastos no asignados a una línea (estructura) se reparten
    en proporción a los ingresos de cada línea. La rentabilidad sobre capital se anualiza."""
    s = saldos_por_linea(conn, entidad, desde, hasta)
    info = {r["id"]: r for r in lineas(conn, entidad)}
    ingresos, costes = defaultdict(int), defaultdict(int)
    estructura = 0
    for (cuenta, lid), neto in s.items():
        if cuenta.startswith("630"):
            continue
        if cuenta.startswith("7"):
            ingresos[lid] += -neto
        elif lid is None:
            estructura += neto
        else:
            costes[lid] += neto
    ids = sorted(set(ingresos) | set(costes), key=lambda x: (x is None, x or 0))
    ingresos_asignados = sum(v for k, v in ingresos.items() if k is not None)
    factor_anual = 365 / dias(desde, hasta)
    filas = []
    for lid in ids:
        ing = ingresos.get(lid, 0)
        cd = costes.get(lid, 0)
        reparto = int(round(estructura * ing / ingresos_asignados)) if (lid is not None and ingresos_asignados) else 0
        margen = ing - cd
        resultado = margen - reparto
        capital = info[lid]["capital_invertido"] if lid in info else 0
        filas.append({
            "codigo": info[lid]["codigo"] if lid in info else "SIN-LINEA",
            "linea": info[lid]["nombre"] if lid in info else "Ingresos/costes sin línea asignada",
            "tipo": info[lid]["tipo"] if lid in info else "",
            "ingresos": ing, "costes_directos": cd, "margen_contribucion": margen,
            "margen_pct": round(margen / ing * 100, 1) if ing else None,
            "gastos_estructura": reparto, "resultado": resultado,
            "resultado_pct": round(resultado / ing * 100, 1) if ing else None,
            "peso_ingresos_pct": round(ing / sum(ingresos.values()) * 100, 1) if sum(ingresos.values()) else None,
            "capital_invertido": capital,
            "rentabilidad_capital_anual_pct": round(resultado * factor_anual / capital * 100, 2) if capital else None,
        })
    if not ingresos_asignados and estructura:
        filas.append({"codigo": "ESTRUCTURA", "linea": "Gastos generales no repartidos", "tipo": "", "ingresos": 0,
                      "costes_directos": 0, "margen_contribucion": 0, "margen_pct": None,
                      "gastos_estructura": estructura, "resultado": -estructura, "resultado_pct": None,
                      "peso_ingresos_pct": None, "capital_invertido": 0, "rentabilidad_capital_anual_pct": None})
    return {"entidad": entidad.codigo, "desde": desde, "hasta": hasta, "lineas": filas,
            "gastos_estructura_total": estructura}


def _top_terceros(conn, entidad, tipo, desde, hasta, n=5):
    return [dict(r) for r in conn.execute(
        "SELECT t.nombre, t.nif, SUM(f.base) AS base, COUNT(*) AS facturas FROM facturas f JOIN terceros t"
        " ON t.id=f.tercero_id WHERE f.entidad_id=? AND f.tipo=? AND f.fecha BETWEEN ? AND ?"
        " GROUP BY t.id ORDER BY base DESC LIMIT ?", (entidad.id, tipo, desde, hasta, n))]


def _gastos_por_naturaleza(conn, entidad, desde, hasta):
    from .ledger import saldos
    out = defaultdict(int)
    for c, (d, h) in saldos(conn, entidad, desde, hasta, prefijo="6", excluir_origen=("regularizacion",)).items():
        out[c] += d - h
    return sorted(({"cuenta": c, "nombre": pgc.nombre_cuenta(c), "importe": v} for c, v in out.items() if v),
                  key=lambda x: -x["importe"])


def informe_gestion(conn, entidad: Entidad, ejercicio: int, t: int) -> dict:
    dq, hq = trimestre(ejercicio, t)
    dqa, hqa = trimestre(ejercicio - 1, t)
    da, ha = acumulado(ejercicio, t)
    daa, haa = acumulado(ejercicio - 1, t)
    p_t, p_ta = pyg(conn, entidad, dq, hq), pyg(conn, entidad, dqa, hqa)
    ep, tp = (ejercicio, t - 1) if t > 1 else (ejercicio - 1, 4)
    p_tp = pyg(conn, entidad, *trimestre(ep, tp))
    p_a, p_aa = pyg(conn, entidad, da, ha), pyg(conn, entidad, daa, haa)
    b = balance(conn, entidad, hq)
    est = b["estructura"]
    activo_corriente = sum(est["ACTIVO"][pgc.AC].values())
    pasivo_corriente = sum(est["PN_PASIVO"][pgc.PC].values())
    pasivo_nc = sum(est["PN_PASIVO"][pgc.PNC].values())
    pn = sum(est["PN_PASIVO"][pgc.PN].values())
    tesoreria = saldo(conn, entidad, "57", None, hq)
    cobros = pendientes(conn, entidad, "emitida", hq)
    pagos = pendientes(conn, entidad, "recibida", hq)

    def kpi(nombre, actual, anterior, trim_anterior=None):
        return {"indicador": nombre, "actual": actual, "anterior": anterior, "variacion_pct": _var(actual, anterior),
                "trim_anterior": trim_anterior, "variacion_trim_pct": _var(actual, trim_anterior)}

    rai = "C) RESULTADO ANTES DE IMPUESTOS"
    kpis = [
        kpi(f"Ingresos {t}T", p_t["ingresos"], p_ta["ingresos"], p_tp["ingresos"]),
        kpi(f"Gastos {t}T", p_t["gastos"], p_ta["gastos"], p_tp["gastos"]),
        kpi(f"Resultado antes de impuestos {t}T", p_t[rai], p_ta[rai], p_tp[rai]),
        kpi("Ingresos acumulados", p_a["ingresos"], p_aa["ingresos"]),
        kpi("Gastos acumulados", p_a["gastos"], p_aa["gastos"]),
        kpi("Resultado acumulado antes de impuestos", p_a["C) RESULTADO ANTES DE IMPUESTOS"],
            p_aa["C) RESULTADO ANTES DE IMPUESTOS"]),
        kpi("EBITDA acumulado", p_a["A) RESULTADO DE EXPLOTACIÓN"] - p_a["partidas"]["8. Amortización del inmovilizado"],
            p_aa["A) RESULTADO DE EXPLOTACIÓN"] - p_aa["partidas"]["8. Amortización del inmovilizado"]),
    ]
    ratios = {
        "margen_neto_acumulado_pct": round(p_a["C) RESULTADO ANTES DE IMPUESTOS"] / p_a["ingresos"] * 100, 1)
        if p_a["ingresos"] else None,
        "liquidez_corriente": round(activo_corriente / pasivo_corriente, 2) if pasivo_corriente > 0 else None,
        "endeudamiento_pct": round((pasivo_corriente + pasivo_nc) / b["total_pn_pasivo"] * 100, 1)
        if b["total_pn_pasivo"] else None,
        "roe_anualizado_pct": round(p_a["C) RESULTADO ANTES DE IMPUESTOS"] * 365 / dias(da, ha) / pn * 100, 1)
        if pn > 0 else None,
        "periodo_medio_cobro_dias": round(sum(c["pendiente"] for c in cobros) / (p_a["ingresos"] / dias(da, ha)))
        if p_a["ingresos"] > 0 else None,
    }
    alertas = []
    vencidas = [c for c in cobros if c["dias"] > 60]
    if vencidas:
        alertas.append(f"{len(vencidas)} facturas emitidas con más de 60 días pendientes de cobro "
                       f"({sum(c['pendiente'] for c in vencidas) / 100:,.2f} €)")
    if tesoreria < 0:
        alertas.append("Saldo de tesorería negativo")
    if not b["cuadra"]:
        alertas.append("El balance no cuadra: revisa cuentas sin clasificar")
    if b["sin_clasificar"]:
        alertas.append(f"Cuentas sin clasificar en balance: {', '.join(b['sin_clasificar'])}")
    return {
        "entidad": entidad.codigo, "nombre": entidad.nombre, "ejercicio": ejercicio, "trimestre": t,
        "trimestre_anterior": f"{tp}T {ep}",
        "kpis": kpis, "ratios": ratios, "tesoreria": tesoreria,
        "pendiente_cobro": sum(c["pendiente"] for c in cobros), "pendiente_pago": sum(p["pendiente"] for p in pagos),
        "top_clientes": _top_terceros(conn, entidad, "emitida", da, ha),
        "top_proveedores": _top_terceros(conn, entidad, "recibida", da, ha),
        "gastos_por_naturaleza": _gastos_por_naturaleza(conn, entidad, da, ha),
        "rentabilidad_lineas": rentabilidad_por_linea(conn, entidad, da, ha)["lineas"],
        "alertas": alertas,
    }


def resumen_grupo(conn, ejercicio: int, t: int) -> dict:
    """Vista agregada de todas las entidades (no es una consolidación: no elimina operaciones intragrupo)."""
    da, ha = acumulado(ejercicio, t)
    filas = []
    for e in listar_entidades(conn):
        p = pyg(conn, e, da, ha)
        b = balance(conn, e, ha)
        pn = sum(b["estructura"]["PN_PASIVO"][pgc.PN].values())
        filas.append({"codigo": e.codigo, "nombre": e.nombre, "tipo": e.tipo, "ingresos": p["ingresos"],
                      "gastos": p["gastos"], "resultado_antes_impuestos": p["C) RESULTADO ANTES DE IMPUESTOS"],
                      "tesoreria": saldo(conn, e, "57", None, ha), "patrimonio_neto": pn,
                      "total_activo": b["total_activo"]})
    estructura = []
    for e in listar_entidades(conn):
        for p in participadas(conn, e):
            res = next((f["resultado_antes_impuestos"] for f in filas if f["codigo"] == p["codigo"]), 0)
            estructura.append({"titular": e.codigo, "participada": p["codigo"], "porcentaje": p["porcentaje"],
                               "coste": p["coste"], "resultado_atribuible": int(round(res * p["porcentaje"] / 100))})
    totales = {k: sum(f[k] for f in filas) for k in ("ingresos", "gastos", "resultado_antes_impuestos", "tesoreria")}
    return {"ejercicio": ejercicio, "trimestre": t, "entidades": filas, "participaciones": estructura,
            "totales_agregados": totales,
            "nota": "Suma simple de entidades; no elimina operaciones ni dividendos intragrupo."}
