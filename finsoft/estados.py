"""Estados financieros: balance de situación y cuenta de pérdidas y ganancias (modelos abreviados PGC PYMES)."""

from collections import OrderedDict

from . import pgc
from .entidades import Entidad
from .fechas import acumulado, ejercicio as rango_ejercicio, trimestre
from .ledger import saldos
from .money import from_cents


def pyg(conn, entidad: Entidad, desde: str, hasta: str) -> dict:
    """Importes en céntimos con signo de resultado (ingreso +, gasto -)."""
    s = saldos(conn, entidad, desde, hasta, excluir_origen=("regularizacion",))
    partidas = OrderedDict((n, 0) for n, _ in pgc.PYG)
    detalle: dict[str, dict[str, int]] = {n: {} for n, _ in pgc.PYG}
    for cuenta, (d, h) in s.items():
        if cuenta[0] not in "67":
            continue
        partida = pgc.clasificar_pyg(cuenta, entidad.es_holding)
        if partida is None:
            continue
        partidas[partida] += h - d
        detalle[partida][cuenta] = detalle[partida].get(cuenta, 0) + h - d
    explot = sum(partidas[n] for n, _ in pgc.PYG_EXPLOTACION)
    financ = sum(partidas[n] for n, _ in pgc.PYG_FINANCIERO)
    rai = explot + financ
    impuesto = partidas["18. Impuestos sobre beneficios"]
    return {
        "partidas": partidas,
        "detalle": detalle,
        "A) RESULTADO DE EXPLOTACIÓN": explot,
        "B) RESULTADO FINANCIERO": financ,
        "C) RESULTADO ANTES DE IMPUESTOS": rai,
        "D) RESULTADO DEL EJERCICIO": rai + impuesto,
        "ingresos": sum(h - d for c, (d, h) in s.items() if c[0] == "7"),
        "gastos": sum(d - h for c, (d, h) in s.items() if c[0] == "6" and not c.startswith("630")),
    }


def pyg_filas(conn, entidad: Entidad, ejercicio: int, hasta_trimestre: int = 4) -> list[dict]:
    """PyG con una columna por trimestre, acumulado y acumulado del año anterior."""
    columnas = OrderedDict()
    for t in range(1, hasta_trimestre + 1):
        columnas[f"T{t}"] = pyg(conn, entidad, *trimestre(ejercicio, t))
    ea, ta = (ejercicio, hasta_trimestre - 1) if hasta_trimestre > 1 else (ejercicio - 1, 4)
    columnas[f"Trim. anterior ({ta}T {ea})"] = pyg(conn, entidad, *trimestre(ea, ta))
    columnas["Acumulado"] = pyg(conn, entidad, *acumulado(ejercicio, hasta_trimestre))
    columnas[f"Acum. {ejercicio - 1}"] = pyg(conn, entidad, *acumulado(ejercicio - 1, hasta_trimestre))
    filas = []

    def fila(nombre, getter, nivel):
        filas.append({"concepto": nombre, "nivel": nivel, **{k: getter(v) for k, v in columnas.items()}})

    for n, _ in pgc.PYG_EXPLOTACION:
        fila(n, lambda v, n=n: v["partidas"][n], 1)
    fila("A) RESULTADO DE EXPLOTACIÓN", lambda v: v["A) RESULTADO DE EXPLOTACIÓN"], 0)
    for n, _ in pgc.PYG_FINANCIERO:
        fila(n, lambda v, n=n: v["partidas"][n], 1)
    fila("B) RESULTADO FINANCIERO", lambda v: v["B) RESULTADO FINANCIERO"], 0)
    fila("C) RESULTADO ANTES DE IMPUESTOS", lambda v: v["C) RESULTADO ANTES DE IMPUESTOS"], 0)
    fila("18. Impuestos sobre beneficios", lambda v: v["partidas"]["18. Impuestos sobre beneficios"], 1)
    fila("D) RESULTADO DEL EJERCICIO", lambda v: v["D) RESULTADO DEL EJERCICIO"], 0)
    return filas


def balance(conn, entidad: Entidad, fecha: str) -> dict:
    """Balance a una fecha. El resultado del ejercicio en curso (grupos 6 y 7 no regularizados)
    se presenta en 'VII. Resultado del ejercicio'."""
    s = saldos(conn, entidad, None, fecha)
    estructura: dict = OrderedDict()
    for masa, grupo, epigrafe, _ in pgc.BALANCE:
        estructura.setdefault(masa, OrderedDict()).setdefault(grupo, OrderedDict())[epigrafe] = 0
    sin_clasificar = {}
    resultado_pendiente = 0
    inicio_ejercicio = f"{fecha[:4]}-01-01"
    for cuenta, (d, h) in s.items():
        saldo_d = d - h
        if cuenta[0] in "67":
            continue
        cl = pgc.clasificar_balance(cuenta, saldo_d)
        if cl is None:
            sin_clasificar[cuenta] = saldo_d
            continue
        masa, grupo, epigrafe = cl
        estructura[masa][grupo][epigrafe] += saldo_d if masa == "ACTIVO" else -saldo_d
    # Grupos 6 y 7 sin regularizar: los del ejercicio en curso van a resultado del ejercicio y los de
    # ejercicios anteriores a resultados de ejercicios anteriores.
    actual = saldos(conn, entidad, inicio_ejercicio, fecha)
    for cuenta, (d, h) in s.items():
        if cuenta[0] in "67":
            da, ha = actual.get(cuenta, (0, 0))
            resultado_pendiente += ha - da
            estructura["PN_PASIVO"][pgc.PN]["V. Resultados de ejercicios anteriores"] += (h - d) - (ha - da)
    estructura["PN_PASIVO"][pgc.PN]["VII. Resultado del ejercicio"] += resultado_pendiente
    totales = {g: sum(e.values()) for m in estructura.values() for g, e in m.items()}
    total_activo = sum(sum(e.values()) for e in estructura["ACTIVO"].values())
    total_pn_pasivo = sum(sum(e.values()) for e in estructura["PN_PASIVO"].values())
    return {
        "fecha": fecha,
        "estructura": estructura,
        "totales": totales,
        "total_activo": total_activo,
        "total_pn_pasivo": total_pn_pasivo,
        "cuadra": total_activo == total_pn_pasivo,
        "sin_clasificar": sin_clasificar,
    }


def balance_filas(conn, entidad: Entidad, fecha: str, fecha_comparativa: str | None = None) -> list[dict]:
    b = balance(conn, entidad, fecha)
    bc = balance(conn, entidad, fecha_comparativa) if fecha_comparativa else None
    filas = []
    titulos = {"ACTIVO": "TOTAL ACTIVO", "PN_PASIVO": "TOTAL PATRIMONIO NETO Y PASIVO"}
    for masa, grupos in b["estructura"].items():
        filas.append({"concepto": "ACTIVO" if masa == "ACTIVO" else "PATRIMONIO NETO Y PASIVO", "nivel": -1,
                      "actual": None, "comparativo": None})
        for grupo, epigrafes in grupos.items():
            filas.append({"concepto": grupo, "nivel": 0, "actual": b["totales"][grupo],
                          "comparativo": bc["totales"][grupo] if bc else None})
            for ep, imp in epigrafes.items():
                comp = bc["estructura"][masa][grupo][ep] if bc else 0
                if imp or comp:
                    filas.append({"concepto": ep, "nivel": 1, "actual": imp, "comparativo": comp if bc else None})
        tot = b["total_activo"] if masa == "ACTIVO" else b["total_pn_pasivo"]
        totc = (bc["total_activo"] if masa == "ACTIVO" else bc["total_pn_pasivo"]) if bc else None
        filas.append({"concepto": titulos[masa], "nivel": 0, "actual": tot, "comparativo": totc})
    return filas


def resumen_trimestral(conn, entidad: Entidad, ejercicio: int, t: int) -> dict:
    fin = trimestre(ejercicio, t)[1]
    return {
        "pyg_trimestre": pyg(conn, entidad, *trimestre(ejercicio, t)),
        "pyg_acumulado": pyg(conn, entidad, *acumulado(ejercicio, t)),
        "balance": balance(conn, entidad, fin),
    }


def cierre_regularizacion(conn, entidad: Entidad, ejercicio: int) -> int | None:
    """Asiento de regularización: salda los grupos 6 y 7 contra la 1290."""
    from .ledger import Apunte, crear_asiento
    desde, hasta = rango_ejercicio(ejercicio)
    ya = conn.execute("SELECT 1 FROM asientos WHERE entidad_id=? AND ejercicio=? AND origen='regularizacion'",
                      (entidad.id, ejercicio)).fetchone()
    if ya:
        raise ValueError(f"El ejercicio {ejercicio} de {entidad.codigo} ya está regularizado")
    s = saldos(conn, entidad, desde, hasta)
    ap, resultado = [], 0
    for cuenta, (d, h) in sorted(s.items()):
        if cuenta[0] in "67" and d != h:
            neto = d - h
            ap.append(Apunte(cuenta, haber=from_cents(neto)) if neto > 0 else Apunte(cuenta, debe=from_cents(-neto)))
            resultado += -neto
    if not ap:
        return None
    ap.append(Apunte("1290", haber=from_cents(resultado)) if resultado >= 0 else Apunte("1290", debe=from_cents(-resultado)))
    return crear_asiento(conn, entidad, hasta, f"Regularización del ejercicio {ejercicio}", ap, origen="regularizacion")
