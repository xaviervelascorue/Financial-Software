"""IRPF: modelo 130 (pagos fraccionados de autónomos en estimación directa), atribución de rentas
de comunidades de bienes, modelos 111, 115 y 123 de retenciones practicadas y modelo 184."""

from collections import defaultdict
from decimal import Decimal

from ..entidades import Entidad, get_entidad, participadas, titulares
from ..fechas import acumulado, ejercicio as rango_ejercicio, trimestre
from ..ledger import saldos
from . import AVISO

PCT_130 = Decimal("20")
PCT_GASTOS_DIFICIL_JUSTIFICACION = Decimal("5")
MAX_GASTOS_DIFICIL_JUSTIFICACION = 200000  # céntimos (2.000 €)
LIMITE_SEGURO_SALUD = 50000  # céntimos por persona asegurada y año (art. 30.2.5ª.a LIRPF)


def rendimientos_actividad(conn, entidad: Entidad, desde: str, hasta: str) -> dict:
    """Ingresos y gastos de la actividad económica (excluye rendimientos/pérdidas financieras
    de inversiones, que tributan como capital mobiliario, y el impuesto sobre beneficios)."""
    s = saldos(conn, entidad, desde, hasta, excluir_origen=("regularizacion",))
    ingresos = gastos = seguro_salud = 0
    for c, (d, h) in s.items():
        if c.startswith("7") and not c.startswith(("760", "761", "763", "766")):
            ingresos += h - d
        elif c.startswith("6251"):
            seguro_salud += d - h
        elif c.startswith("6") and not c.startswith(("630", "663", "666")):
            gastos += d - h
    # Seguro de enfermedad del titular y familia: deducible hasta 500 € por persona y año
    if entidad.tipo == "autonomo":
        gastos += min(seguro_salud, LIMITE_SEGURO_SALUD * max(entidad.asegurados_salud, 1))
    else:
        gastos += seguro_salud
    retenciones = conn.execute(
        "SELECT COALESCE(SUM(retencion),0) FROM facturas WHERE entidad_id=? AND tipo='emitida' AND fecha BETWEEN ? AND ?",
        (entidad.id, desde, hasta),
    ).fetchone()[0]
    return {"ingresos": ingresos, "gastos": gastos, "rendimiento": ingresos - gastos, "retenciones": retenciones}


def atribucion_rentas(conn, cb: Entidad, desde: str, hasta: str) -> list[dict]:
    """Reparto entre comuneros de los rendimientos de una comunidad de bienes."""
    r = rendimientos_actividad(conn, cb, desde, hasta)
    out = []
    for t in titulares(conn, cb):
        pct = Decimal(str(t["porcentaje"]))
        out.append({
            "comunero": t["titular_nombre"], "nif": t["titular_nif"], "entidad_id": t["titular_entidad_id"],
            "porcentaje": float(pct),
            **{k: int(round(v * pct / 100)) for k, v in r.items()},
        })
    return out


def _cuota_atribuida(conn, autonomo: Entidad, desde: str, hasta: str, renta: str = "actividad") -> dict:
    """Parte del comunero en las CB cuya renta es del tipo indicado ('actividad' → 130;
    'capital_inmobiliario' → sólo declaración de la Renta)."""
    total = defaultdict(int)
    detalle = []
    for p in participadas(conn, autonomo):
        if p["participada_tipo"] != "comunidad_bienes":
            continue
        cb = get_entidad(conn, p["participada_id"])
        if cb.renta_atribuida != renta:
            continue
        for a in atribucion_rentas(conn, cb, desde, hasta):
            if a["entidad_id"] == autonomo.id:
                for k in ("ingresos", "gastos", "rendimiento", "retenciones"):
                    total[k] += a[k]
                detalle.append({"cb": cb.codigo, **a})
    return {"total": dict(total), "detalle": detalle}


def _minoracion_110(rend_anterior: int | None) -> int:
    """Minoración art. 110.3.c RIRPF según rendimientos netos del ejercicio anterior (céntimos)."""
    if rend_anterior is None:
        return 0
    for limite, importe in ((900000, 10000), (1000000, 7500), (1100000, 5000), (1200000, 2500)):
        if rend_anterior <= limite:
            return importe
    return 0


def _hay_movimientos(conn, entidad: Entidad, desde: str, hasta: str) -> bool:
    return conn.execute("SELECT 1 FROM apuntes ap JOIN asientos a ON a.id=ap.asiento_id WHERE a.entidad_id=?"
                        " AND a.fecha BETWEEN ? AND ? AND (ap.cuenta LIKE '6%' OR ap.cuenta LIKE '7%') LIMIT 1",
                        (entidad.id, desde, hasta)).fetchone() is not None


def pct_ingresos_con_retencion(conn, entidad: Entidad, desde: str, hasta: str) -> float | None:
    """% de los ingresos de la actividad (facturas emitidas) sometidos a retención."""
    total, con_ret = conn.execute(
        "SELECT COALESCE(SUM(base),0), COALESCE(SUM(CASE WHEN retencion > 0 THEN base ELSE 0 END),0) FROM facturas"
        " WHERE entidad_id=? AND tipo='emitida' AND fecha BETWEEN ? AND ?", (entidad.id, desde, hasta)).fetchone()
    return round(con_ret * 100 / total, 1) if total > 0 else None


def obligacion_130(conn, autonomo: Entidad, ejercicio: int, t: int) -> dict:
    """Art. 109.1 RIRPF: los profesionales no presentan el 130 si al menos el 70 % de sus ingresos de la
    actividad del año anterior tuvieron retención. En el año de inicio se mide con los ingresos del trimestre."""
    if autonomo.exento_130:
        return {"obligado": False, "criterio": "Marcada como exenta en la configuración", "pct": None}
    pct_ant = pct_ingresos_con_retencion(conn, autonomo, f"{ejercicio - 1}-01-01", f"{ejercicio - 1}-12-31")
    if pct_ant is None and autonomo.pct_retencion_anterior is not None and \
            (autonomo.anio_inicio_actividad or 0) < ejercicio:
        pct_ant = autonomo.pct_retencion_anterior  # año anterior no contabilizado en FinSoft (dato de configuración)
    if pct_ant is not None:
        criterio, pct = f"ingresos de {ejercicio - 1}", pct_ant
    else:
        pct = pct_ingresos_con_retencion(conn, autonomo, *trimestre(ejercicio, t))
        criterio = f"ingresos del {t}T (año de inicio de actividad)"
    if pct is None:
        return {"obligado": True, "criterio": "sin ingresos facturados en el periodo", "pct": None}
    return {"obligado": pct < 70, "criterio": criterio, "pct": pct}


def modelo_130(conn, autonomo: Entidad, ejercicio: int, t: int) -> dict:
    if autonomo.tipo != "autonomo":
        raise ValueError("El modelo 130 sólo aplica a autónomos (las CB atribuyen a sus comuneros)")
    from . import presentadas
    desde, hasta = acumulado(ejercicio, t)
    # Lo presentado manda: los acumulados (01, 02, 06) del último trimestre presentado del año son el punto
    # de partida y sólo se suma lo contabilizado en los trimestres posteriores.
    base_previa, desde_libros = None, desde
    for tt in range(t - 1, 0, -1):
        prev = presentadas.obtener(conn, autonomo, "130", ejercicio, f"{tt}T")
        if prev:
            base_previa, desde_libros = prev["casillas"], trimestre(ejercicio, tt + 1)[0]
            break
    propio = rendimientos_actividad(conn, autonomo, desde_libros, hasta)
    atrib = _cuota_atribuida(conn, autonomo, desde_libros, hasta)
    ingresos = propio["ingresos"] + atrib["total"].get("ingresos", 0)
    gastos = propio["gastos"] + atrib["total"].get("gastos", 0)
    djust = 0
    if autonomo.regimen_irpf == "eds" and ingresos - gastos > 0:
        djust = min(int(round((ingresos - gastos) * PCT_GASTOS_DIFICIL_JUSTIFICACION / 100)),
                    MAX_GASTOS_DIFICIL_JUSTIFICACION)
    c = {}
    c["01"] = ingresos + (base_previa or {}).get("01", 0)
    c["02"] = gastos + djust + (base_previa or {}).get("02", 0)
    c["03"] = c["01"] - c["02"]
    c["04"] = int(round(c["03"] * PCT_130 / 100)) if c["03"] > 0 else 0
    # pagos fraccionados de trimestres anteriores (casilla 07 positiva) y negativos pendientes
    previos, negativos = 0, 0
    for tt in range(1, t):
        presentada = presentadas.obtener(conn, autonomo, "130", ejercicio, f"{tt}T")
        if presentada:  # lo realmente presentado manda sobre el cálculo
            prev = {"07": presentada["casillas"].get("07", 0), "19": presentada["resultado"],
                    "15": presentada["casillas"].get("15", 0)}
        else:
            prev_m = modelo_130(conn, autonomo, ejercicio, tt)
            prev = prev_m["casillas"]
            if not prev_m["presentar"]:
                continue  # trimestre sin obligación: no hubo pago que descontar
        previos += max(prev["07"], 0)
        if prev["19"] < 0:
            negativos += -prev["19"]
        else:
            negativos = max(negativos - prev["15"], 0)
    c["05"] = previos
    c["06"] = propio["retenciones"] + atrib["total"].get("retenciones", 0) + (base_previa or {}).get("06", 0)
    c["07"] = c["04"] - c["05"] - c["06"]
    c["12"] = c["07"]
    c["13"] = _minoracion_110(autonomo.rend_neto_anterior)
    c["14"] = c["12"] - c["13"]
    c["15"] = min(negativos, c["14"]) if c["14"] > 0 else 0
    c["16"] = 0
    c["17"] = c["14"] - c["15"] - c["16"]
    c["18"] = 0
    c["19"] = c["17"] - c["18"]
    avisos = []
    ob = obligacion_130(conn, autonomo, ejercicio, t)
    # Sin obligación, el cálculo sirve como provisión: diferencia entre el 20 % del rendimiento acumulado
    # y las retenciones ya soportadas, que se regulariza en la Renta.
    provision = max(c["04"] - c["06"], 0)
    if not ob["obligado"]:
        avisos.append(f"No obligado a presentar el 130: {ob['pct']} % de ingresos con retención ({ob['criterio']}); "
                      "art. 109.1 RIRPF.")
        if provision:
            avisos.append(f"Provisión recomendada para la Renta: {provision / 100:,.2f} € (20 % del rendimiento menos "
                          "retenciones). La cuota real depende de la escala y del resto de rentas.")
    return {
        "modelo": "130", "entidad": autonomo.codigo, "nif": autonomo.nif, "ejercicio": ejercicio, "periodo": f"{t}T",
        "desde": desde, "hasta": hasta, "casillas": c,
        "resultado": max(c["19"], 0) if ob["obligado"] else 0,
        "tipo_resultado": ("A ingresar" if c["19"] > 0 else "Negativa / cero") if ob["obligado"] else "No obligado",
        "obligacion": ob, "provision_renta": provision,
        "gastos_dificil_justificacion": djust, "atribucion_cb": atrib["detalle"],
        "presentar": ob["obligado"], "avisos": avisos, "aviso": AVISO,
    }


def _retenciones(conn, entidad, modelo, desde, hasta):
    return conn.execute(
        "SELECT clave, COUNT(DISTINCT perceptor_nif) AS perceptores, SUM(base) AS base, SUM(retencion) AS retencion"
        " FROM retenciones WHERE entidad_id=? AND modelo=? AND fecha BETWEEN ? AND ? GROUP BY clave",
        (entidad.id, modelo, desde, hasta),
    ).fetchall()


def modelo_111(conn, entidad: Entidad, ejercicio: int, t: int) -> dict:
    desde, hasta = trimestre(ejercicio, t)
    c = defaultdict(int)
    for r in _retenciones(conn, entidad, "111", desde, hasta):
        if r["clave"] == "A":
            c["01"] += r["perceptores"]
            c["02"] += r["base"]
            c["03"] += r["retencion"]
        else:
            c["07"] += r["perceptores"]
            c["08"] += r["base"]
            c["09"] += r["retencion"]
    c["28"] = c["03"] + c["09"]
    c["30"] = c["28"]
    return {"modelo": "111", "entidad": entidad.codigo, "ejercicio": ejercicio, "periodo": f"{t}T",
            "casillas": dict(c), "resultado": c["30"], "presentar": c["30"] > 0, "aviso": AVISO}


def modelo_115(conn, entidad: Entidad, ejercicio: int, t: int) -> dict:
    desde, hasta = trimestre(ejercicio, t)
    c = defaultdict(int)
    for r in _retenciones(conn, entidad, "115", desde, hasta):
        c["01"] += r["perceptores"]
        c["02"] += r["base"]
        c["03"] += r["retencion"]
    c["05"] = c["03"]
    return {"modelo": "115", "entidad": entidad.codigo, "ejercicio": ejercicio, "periodo": f"{t}T",
            "casillas": dict(c), "resultado": c["05"], "presentar": c["05"] > 0, "aviso": AVISO}


def modelo_123(conn, entidad: Entidad, ejercicio: int, t: int) -> dict:
    desde, hasta = trimestre(ejercicio, t)
    c = defaultdict(int)
    for r in _retenciones(conn, entidad, "123", desde, hasta):
        c["01"] += r["perceptores"]
        c["02"] += r["base"]
        c["03"] += r["retencion"]
    c["06"] = c["03"]
    return {"modelo": "123", "entidad": entidad.codigo, "ejercicio": ejercicio, "periodo": f"{t}T",
            "casillas": dict(c), "resultado": c["06"], "presentar": c["06"] > 0, "aviso": AVISO}


def modelo_184(conn, cb: Entidad, ejercicio: int) -> dict:
    """Declaración informativa anual de entidades en régimen de atribución de rentas."""
    if cb.tipo != "comunidad_bienes":
        raise ValueError("El modelo 184 sólo aplica a comunidades de bienes / entidades en atribución")
    desde, hasta = rango_ejercicio(ejercicio)
    total = rendimientos_actividad(conn, cb, desde, hasta)
    reparto = atribucion_rentas(conn, cb, desde, hasta)
    pct = sum(r["porcentaje"] for r in reparto)
    avisos = [] if abs(pct - 100) < 0.01 else [f"Los porcentajes de los comuneros suman {pct} %, no 100 %"]
    if cb.renta_atribuida == "capital_inmobiliario":
        avisos.append("Rendimientos del capital inmobiliario (vivienda de uso turístico sin servicios de hostelería): "
                      "sin reducción por alquiler de vivienda habitual; intereses y conservación limitados a los ingresos.")
    return {"modelo": "184", "entidad": cb.codigo, "nif": cb.nif, "ejercicio": ejercicio,
            "tipo_renta": cb.renta_atribuida,
            "rendimientos_actividad": total, "comuneros": reparto, "avisos": avisos, "aviso": AVISO}


def resumen_anual_autonomo(conn, autonomo: Entidad, ejercicio: int) -> dict:
    """Datos de la actividad para la declaración de la renta (modelo 100)."""
    desde, hasta = rango_ejercicio(ejercicio)
    propio = rendimientos_actividad(conn, autonomo, desde, hasta)
    atrib = _cuota_atribuida(conn, autonomo, desde, hasta)
    inmob = _cuota_atribuida(conn, autonomo, desde, hasta, "capital_inmobiliario")
    pagos = conn.execute(
        "SELECT COALESCE(SUM(ap.debe - ap.haber),0) FROM apuntes ap JOIN asientos a ON a.id=ap.asiento_id"
        " WHERE a.entidad_id=? AND ap.cuenta LIKE '4731%' AND a.fecha BETWEEN ? AND ?",
        (autonomo.id, desde, hasta)).fetchone()[0]
    return {"entidad": autonomo.codigo, "ejercicio": ejercicio, "actividad_propia": propio,
            "atribuido_cb": atrib, "atribuido_cb_capital_inmobiliario": inmob,
            "pagos_fraccionados_130_contabilizados": pagos,
            "nota": "La reducción por gastos de difícil justificación y demás reducciones se aplican en el modelo 100."}

