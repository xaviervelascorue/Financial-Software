"""IVA: modelo 303 (trimestral), modelo 390 (resumen anual) y libros registro."""

from collections import defaultdict

from ..entidades import Entidad
from ..fechas import trimestre
from ..ledger import Apunte, crear_asiento
from ..money import from_cents
from . import AVISO

TIPOS_CASILLA = {4.0: ("01", "02", "03"), 10.0: ("04", "05", "06"), 21.0: ("07", "08", "09")}

NOMBRES_303 = {
    "01": "Régimen general 4 %: base", "03": "Régimen general 4 %: cuota",
    "04": "Régimen general 10 %: base", "06": "Régimen general 10 %: cuota",
    "07": "Régimen general 21 %: base", "09": "Régimen general 21 %: cuota",
    "150": "Otros tipos: base", "152": "Otros tipos: cuota",
    "10": "Adquisiciones intracomunitarias: base", "11": "Adquisiciones intracomunitarias: cuota",
    "12": "Otras operaciones con inversión del sujeto pasivo: base", "13": "Inversión del sujeto pasivo: cuota",
    "27": "Total cuota devengada",
    "28": "Cuotas soportadas operaciones interiores corrientes: base", "29": "Cuota deducible",
    "30": "Cuotas soportadas interiores bienes de inversión: base", "31": "Cuota deducible",
    "32": "Importaciones bienes corrientes: base", "33": "Cuota deducible",
    "34": "Importaciones bienes de inversión: base", "35": "Cuota deducible",
    "36": "Adquisiciones intracomunitarias bienes y servicios corrientes: base", "37": "Cuota deducible",
    "38": "Adquisiciones intracomunitarias bienes de inversión: base", "39": "Cuota deducible",
    "44": "Regularización por aplicación del porcentaje definitivo de prorrata", "45": "Total a deducir", "46": "Resultado régimen general",
    "59": "Entregas intracomunitarias de bienes y servicios", "60": "Exportaciones y operaciones asimiladas",
    "120": "Operaciones no sujetas por reglas de localización", "122": "Operaciones sujetas con inversión del sujeto pasivo",
    "64": "Suma de resultados", "65": "% atribuible a la Administración del Estado", "66": "Atribuible al Estado",
    "67": "Cuotas a compensar de periodos anteriores aplicadas", "69": "Resultado", "71": "Resultado de la liquidación",
}


def _lineas_periodo(conn, entidad, desde, hasta):
    return conn.execute(
        "SELECT f.id, f.tipo, f.tipo_operacion, f.bien_inversion, f.cuota_iva AS cuota_fra,"
        " f.cuota_deducible AS deducible_fra, l.base, l.tipo_iva, l.cuota_iva, l.deducible FROM facturas f"
        " JOIN factura_lineas l ON l.factura_id=f.id WHERE f.entidad_id=? AND f.fecha BETWEEN ? AND ?",
        (entidad.id, desde, hasta),
    ).fetchall()


def _calcular(conn, entidad, desde, hasta) -> dict:
    c = defaultdict(int)
    otros_tipos = set()
    for r in _lineas_periodo(conn, entidad, desde, hasta):
        base, cuota = r["base"], r["cuota_iva"]
        if r["tipo"] == "emitida":
            op = r["tipo_operacion"]
            if op == "interior":
                cas = TIPOS_CASILLA.get(float(r["tipo_iva"]))
                if cas:
                    c[cas[0]] += base
                    c[cas[2]] += cuota
                else:
                    otros_tipos.add(r["tipo_iva"])
                    c["150"] += base
                    c["152"] += cuota
            elif op == "intracomunitaria":
                c["59"] += base
            elif op == "exportacion":
                c["60"] += base
            elif op == "isp":
                c["122"] += base
            elif op == "no_sujeta":
                c["120"] += base
            else:
                c["exentas"] += base
        else:
            ded = r["deducible"]
            base_ded = round(base * ded / cuota) if cuota else 0
            op = r["tipo_operacion"]
            inv = bool(r["bien_inversion"])
            if op == "intracomunitaria":
                c["10"] += base
                c["11"] += cuota
                k = ("38", "39") if inv else ("36", "37")
            elif op == "isp":
                c["12"] += base
                c["13"] += cuota
                k = ("30", "31") if inv else ("28", "29")
            elif op == "importacion":
                k = ("34", "35") if inv else ("32", "33")
            else:
                k = ("30", "31") if inv else ("28", "29")
            if ded:
                c[k[0]] += base_ded
                c[k[1]] += ded
    c["27"] = sum(c[k] for k in ("03", "06", "09", "152", "11", "13"))
    c["45"] = sum(c[k] for k in ("29", "31", "33", "35", "37", "39", "44"))
    c["46"] = c["27"] - c["45"]
    return {"casillas": c, "otros_tipos": sorted(otros_tipos)}


def prorrata_definitiva(conn, entidad: Entidad, ejercicio: int) -> dict:
    """Prorrata del año (art. 104 LIVA): operaciones con derecho a deducción / total, redondeada al entero
    superior. Con ella se regulariza en el 4T el IVA de los gastos comunes y se fija la provisional del año siguiente."""
    rows = conn.execute(
        "SELECT tipo_operacion, SUM(base) FROM facturas WHERE entidad_id=? AND tipo='emitida' AND fecha BETWEEN ? AND ?"
        " GROUP BY tipo_operacion", (entidad.id, f"{ejercicio}-01-01", f"{ejercicio}-12-31")).fetchall()
    con_derecho = sum(r[1] for r in rows if r[0] in ("interior", "intracomunitaria", "exportacion", "isp", "no_sujeta"))
    exentas = sum(r[1] for r in rows if r[0] == "exenta")
    total = con_derecho + exentas
    pct = 100 if total <= 0 else min(100, -(-con_derecho * 100 // total))
    comun = conn.execute(
        "SELECT COALESCE(SUM(l.cuota_iva),0), COALESCE(SUM(l.deducible),0) FROM factura_lineas l JOIN facturas f"
        " ON f.id=l.factura_id WHERE f.entidad_id=? AND f.tipo='recibida' AND l.prorrata=1 AND f.fecha BETWEEN ? AND ?",
        (entidad.id, f"{ejercicio}-01-01", f"{ejercicio}-12-31")).fetchone()
    regularizacion = int(round(comun[0] * pct / 100)) - comun[1]
    return {"ejercicio": ejercicio, "operaciones_con_derecho": con_derecho, "operaciones_exentas": exentas,
            "prorrata_definitiva": pct, "prorrata_provisional": entidad.prorrata_pct,
            "cuota_comun_soportada": comun[0], "cuota_comun_deducida": comun[1], "regularizacion": regularizacion}


def modelo_303(conn, entidad: Entidad, ejercicio: int, t: int, compensar_anterior: int = 0) -> dict:
    """compensar_anterior: cuotas pendientes de compensar del ejercicio anterior (céntimos), sólo para T1."""
    if not entidad.presenta_303:
        return {"modelo": "303", "obligado": False,
                "motivo": f"Régimen de IVA '{entidad.regimen_iva}': no presenta modelo 303. "
                          "El IVA soportado se registra como mayor gasto."}
    desde, hasta = trimestre(ejercicio, t)
    calc = _calcular(conn, entidad, desde, hasta)
    c = calc["casillas"]
    prorrata = None
    if entidad.regimen_iva == "prorrata" and t == 4:
        prorrata = prorrata_definitiva(conn, entidad, ejercicio)
        c["44"] = prorrata["regularizacion"]
        c["45"] += c["44"]
        c["46"] = c["27"] - c["45"]
    # Cuotas a compensar de trimestres anteriores del mismo ejercicio (resultados negativos T1-T3)
    from . import presentadas
    pendiente = compensar_anterior
    for tt in range(1, t):
        pres = presentadas.obtener(conn, entidad, "303", ejercicio, f"{tt}T")
        if pres:  # lo presentado manda: queda pendiente lo que resultó a compensar
            pendiente = -pres["resultado"] if pres["resultado"] < 0 else pres["casillas"].get("87", 0)
            continue
        prev = _calcular(conn, entidad, *trimestre(ejercicio, tt))["casillas"]["46"]
        neto = prev - pendiente
        pendiente = -neto if neto < 0 else 0
    c["64"] = c["46"]
    c["65"] = 100
    c["66"] = c["64"]
    c["67"] = min(pendiente, c["66"]) if c["66"] > 0 else 0
    c["69"] = c["66"] - c["67"]
    c["71"] = c["69"]
    if c["71"] > 0:
        resultado = "A ingresar"
    elif c["71"] < 0:
        resultado = "A devolver (solicitar devolución)" if t == 4 else "A compensar en periodos posteriores"
    else:
        resultado = "Sin actividad / resultado cero"
    return {
        "modelo": "303", "obligado": True, "entidad": entidad.codigo, "nif": entidad.nif,
        "ejercicio": ejercicio, "periodo": f"{t}T", "desde": desde, "hasta": hasta,
        "casillas": dict(c), "resultado": c["71"], "tipo_resultado": resultado,
        "compensacion_pendiente": max(pendiente - c["67"], 0) + (-c["71"] if c["71"] < 0 else 0),
        "otros_tipos": calc["otros_tipos"],
        "prorrata": entidad.prorrata_pct if entidad.regimen_iva == "prorrata" else None,
        "regularizacion_prorrata": prorrata,
        "aviso": AVISO,
    }


def contabilizar_303(conn, entidad: Entidad, m303: dict) -> int | None:
    """Asiento de liquidación del IVA del trimestre (4770 / 4720 → 4750 o 4700)."""
    if not m303.get("obligado"):
        return None
    c = m303["casillas"]
    reg = c.get("44", 0)  # regularización de prorrata: 6390 (más deducción) / 6340 (menos)
    ap = [Apunte("4770", debe=from_cents(c["27"])), Apunte("4720", haber=from_cents(c["45"] - reg))]
    if reg > 0:
        ap.append(Apunte("6390", haber=from_cents(reg)))
    elif reg < 0:
        ap.append(Apunte("6340", debe=from_cents(-reg)))
    if c["67"]:
        ap.append(Apunte("4700", haber=from_cents(c["67"])))
    if c["71"] > 0:
        ap.append(Apunte("4750", haber=from_cents(c["71"])))
    elif c["71"] < 0:
        ap.append(Apunte("4700", debe=from_cents(-c["71"])))
    return crear_asiento(conn, entidad, m303["hasta"], f"Liquidación IVA {m303['periodo']} {m303['ejercicio']}",
                         ap, origen="impuesto")


def modelo_390(conn, entidad: Entidad, ejercicio: int) -> dict:
    if not entidad.presenta_303:
        return {"modelo": "390", "obligado": False, "motivo": "No presenta IVA"}
    total = defaultdict(int)
    trimestres = []
    for t in range(1, 5):
        m = modelo_303(conn, entidad, ejercicio, t)
        trimestres.append({"periodo": m["periodo"], "resultado": m["resultado"]})
        for k, v in m["casillas"].items():
            if k not in ("65", "67", "69", "71", "64", "66"):
                total[k] += v
    desde, hasta = f"{ejercicio}-01-01", f"{ejercicio}-12-31"
    exentas = _calcular(conn, entidad, desde, hasta)["casillas"].get("exentas", 0)
    volumen = sum(total[k] for k in ("01", "04", "07", "150", "59", "60", "120", "122")) + exentas
    return {
        "modelo": "390", "obligado": True, "entidad": entidad.codigo, "ejercicio": ejercicio,
        "casillas_acumuladas": dict(total), "trimestres": trimestres,
        "volumen_operaciones": volumen, "operaciones_exentas": exentas,
        "resultado_ultimo_periodo": trimestres[-1]["resultado"], "aviso": AVISO,
    }


def libro_registro(conn, entidad: Entidad, tipo: str, desde: str, hasta: str) -> list[dict]:
    """Libro registro de facturas expedidas ('emitida') o recibidas ('recibida')."""
    rows = conn.execute(
        "SELECT f.fecha, f.numero, t.nif, t.nombre, f.tipo_operacion, l.concepto, l.base, l.tipo_iva, l.cuota_iva,"
        " f.cuota_deducible, f.cuota_iva AS cuota_fra, f.retencion, f.total, f.id FROM facturas f"
        " JOIN terceros t ON t.id=f.tercero_id JOIN factura_lineas l ON l.factura_id=f.id"
        " WHERE f.entidad_id=? AND f.tipo=? AND f.fecha BETWEEN ? AND ? ORDER BY f.fecha, f.id, l.id",
        (entidad.id, tipo, desde, hasta),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if tipo == "recibida":
            d["cuota_deducible_linea"] = round(r["cuota_iva"] * r["cuota_deducible"] / r["cuota_fra"]) if r["cuota_fra"] else 0
        out.append(d)
    return out
