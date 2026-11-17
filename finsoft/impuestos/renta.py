"""Datos para la declaración de la Renta (modelo 100) de los comuneros de una comunidad de bienes cuyos
rendimientos son del capital inmobiliario (p. ej. vivienda de uso turístico sin servicios de hostelería).

En Renta Web se declaran en el apartado de entidades en régimen de atribución de rentas, rendimientos
del capital inmobiliario: NIF de la entidad, % de participación, ingresos íntegros, gastos deducibles y
rendimiento neto atribuido. Los datos deben coincidir con el modelo 184 que presenta la comunidad."""

import calendar

from ..entidades import Entidad, get_entidad, participadas, titulares
from ..fechas import ejercicio as rango_ejercicio
from ..ledger import saldos
from . import AVISO

# (clave, descripción, prefijos de cuenta, se prorratea por días alquilados)
CATEGORIAS = [
    ("intereses", "Intereses y gastos de financiación", ["662", "669"], True),
    ("conservacion", "Reparación y conservación", ["622"], True),
    ("tributos", "Tributos y tasas (IBI, basuras)", ["631"], True),
    ("comunidad", "Gastos de comunidad de propietarios", ["6295"], True),
    ("seguros", "Primas de seguros", ["625"], True),
    ("suministros", "Suministros (luz, agua, gas, internet)", ["628"], True),
    ("servicios", "Servicios de terceros (limpieza, gestión, comisiones de plataforma)",
     ["607", "621", "623", "626", "627", "629"], False),
    ("amortizacion_inmueble", "Amortización del inmueble (3 %)", [], True),
    ("amortizacion_muebles", "Amortización de mobiliario y enseres", [], True),
    ("otros", "Otros gastos deducibles", ["6"], True),
]
PCT_IMPUTACION = {True: 1.1, False: 2.0}  # valor catastral revisado desde 1994 / no revisado


def _categoria(cuenta: str) -> str | None:
    if cuenta.startswith(("630", "68")):
        return None
    mejor, largo = None, -1
    for clave, _, prefijos, _ in CATEGORIAS:
        for p in prefijos:
            if cuenta.startswith(p) and len(p) > largo:
                mejor, largo = clave, len(p)
    return mejor


def _amortizaciones(conn, cb, desde, hasta) -> tuple[int, int]:
    """Amortización del año separando inmueble (2811/2821) de mobiliario (resto), según la cuenta acumulada."""
    inmueble = muebles = 0
    for cuenta, (d, h) in saldos(conn, cb, desde, hasta, prefijo="28").items():
        if cuenta.startswith(("2811", "2821")):
            inmueble += h - d
        else:
            muebles += h - d
    return inmueble, muebles


def ficha_comunidad(conn, cb: Entidad, ejercicio: int, dias_alquilado: int | None = None,
                    dias_uso_propio: int = 0) -> dict:
    """Ficha de la comunidad al 100 %. dias_alquilado: días efectivamente alquilados; dias_uso_propio:
    días a disposición de los comuneros (genera imputación de rentas)."""
    desde, hasta = rango_ejercicio(ejercicio)
    dias_anio = 366 if calendar.isleap(ejercicio) else 365
    s = saldos(conn, cb, desde, hasta, excluir_origen=("regularizacion",))
    ingresos = sum(h - d for c, (d, h) in s.items() if c.startswith("7"))
    gastos = {k: 0 for k, *_ in CATEGORIAS}
    for c, (d, h) in s.items():
        cat = _categoria(c) if c.startswith("6") else None
        if cat:
            gastos[cat] += d - h
    gastos["amortizacion_inmueble"], gastos["amortizacion_muebles"] = _amortizaciones(conn, cb, desde, hasta)
    avisos = []
    # Amortización fiscal del inmueble: 3 % sobre el mayor entre coste y valor catastral de la construcción
    if cb.valor_catastral_construccion:
        minimo = int(round(cb.valor_catastral_construccion * 0.03))
        if minimo > gastos["amortizacion_inmueble"]:
            avisos.append("Amortización calculada sobre el valor catastral de la construcción (mayor que el coste).")
            gastos["amortizacion_inmueble"] = minimo
    # Prorrateo por días alquilados de los gastos generales del inmueble
    factor = 1.0
    if dias_alquilado is not None:
        factor = min(dias_alquilado, dias_anio) / dias_anio
        disponibles = dias_anio - dias_uso_propio
        if dias_alquilado < disponibles:
            avisos.append("Los días sin alquilar pero ofertados en plataforma se tratan como alquilados a efectos "
                          "de gastos según el criterio que apliques; revisa el prorrateo con tu asesor.")
        for k, _, _, prorratea in CATEGORIAS:
            if prorratea:
                gastos[k] = int(round(gastos[k] * factor))
    else:
        avisos.append("Indica los días alquilados y de uso propio para prorratear gastos e imputar rentas.")
    limite = ingresos
    int_cons = gastos["intereses"] + gastos["conservacion"]
    exceso = max(int_cons - limite, 0)
    if exceso:
        avisos.append("Intereses y conservación superan los ingresos: el exceso se deduce en los 4 años siguientes.")
    total_gastos = sum(gastos.values()) - exceso
    rendimiento = ingresos - total_gastos
    imputacion = 0
    if dias_uso_propio and cb.valor_catastral:
        imputacion = int(round(cb.valor_catastral * PCT_IMPUTACION[bool(cb.catastral_revisado)] / 100
                               * dias_uso_propio / dias_anio))
    elif dias_uso_propio:
        avisos.append("Hay días de uso propio pero falta el valor catastral para calcular la imputación de rentas.")
    return {
        "entidad": cb.nombre, "nif": cb.nif, "ejercicio": ejercicio, "tipo_renta": cb.renta_atribuida,
        "referencia_catastral": cb.referencia_catastral, "dias_anio": dias_anio, "dias_alquilado": dias_alquilado,
        "dias_uso_propio": dias_uso_propio, "factor_prorrateo": round(factor, 4),
        "ingresos_integros": ingresos, "gastos": gastos, "exceso_intereses_conservacion": exceso,
        "total_gastos_deducibles": total_gastos, "rendimiento_neto": rendimiento,
        "reduccion_aplicable": 0, "imputacion_rentas_100": imputacion,
        "avisos": avisos + ["Vivienda de uso turístico: no aplica la reducción del 50-90 % por alquiler de vivienda habitual."],
    }


def ficha_comunero(conn, comunero: Entidad, ejercicio: int, dias_alquilado: int | None = None,
                   dias_uso_propio: int = 0) -> list[dict]:
    """Datos que el comunero lleva a su Renta por cada CB de capital inmobiliario en la que participa."""
    out = []
    for p in participadas(conn, comunero):
        if p["participada_tipo"] != "comunidad_bienes":
            continue
        cb = get_entidad(conn, p["participada_id"])
        if cb.renta_atribuida != "capital_inmobiliario":
            continue
        f = ficha_comunidad(conn, cb, ejercicio, dias_alquilado, dias_uso_propio)
        pct = p["porcentaje"] / 100
        parte = lambda v: int(round(v * pct))
        out.append({
            **f, "comunero": comunero.nombre, "nif_comunero": comunero.nif, "porcentaje": p["porcentaje"],
            "atribuido": {
                "ingresos_integros": parte(f["ingresos_integros"]),
                "gastos": {k: parte(v) for k, v in f["gastos"].items()},
                "exceso_intereses_conservacion": parte(f["exceso_intereses_conservacion"]),
                "total_gastos_deducibles": parte(f["total_gastos_deducibles"]),
                "rendimiento_neto": parte(f["rendimiento_neto"]),
                "imputacion_rentas": parte(f["imputacion_rentas_100"]),
                "retenciones": 0,
            },
            "comuneros": [{"nombre": t["titular_nombre"], "nif": t["titular_nif"], "porcentaje": t["porcentaje"]}
                          for t in titulares(conn, cb)],
            "aviso": AVISO,
        })
    return out


def filas_ficha(f: dict, clave: str = "atribuido") -> list[dict]:
    """Filas para Excel: concepto, importe al 100 % y parte atribuida."""
    a = f.get(clave, {})
    filas = [{"concepto": "Ingresos íntegros", "total": f["ingresos_integros"], "parte": a.get("ingresos_integros")}]
    for k, desc, *_ in CATEGORIAS:
        if f["gastos"][k]:
            filas.append({"concepto": f"  {desc}", "total": f["gastos"][k], "parte": a.get("gastos", {}).get(k)})
    if f["exceso_intereses_conservacion"]:
        filas.append({"concepto": "  (–) Exceso intereses y conservación para años siguientes",
                      "total": -f["exceso_intereses_conservacion"], "parte": -a.get("exceso_intereses_conservacion", 0)})
    filas += [
        {"concepto": "Total gastos deducibles", "total": f["total_gastos_deducibles"], "parte": a.get("total_gastos_deducibles")},
        {"concepto": "Rendimiento neto", "total": f["rendimiento_neto"], "parte": a.get("rendimiento_neto")},
        {"concepto": "Imputación de rentas por días de uso propio", "total": f["imputacion_rentas_100"],
         "parte": a.get("imputacion_rentas")},
    ]
    return filas
