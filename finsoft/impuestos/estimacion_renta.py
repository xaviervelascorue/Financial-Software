"""Estimación de la cuota del IRPF (Renta) de un autónomo y de lo que debería tener provisionado.

Cálculo simplificado sobre los datos contabilizados hasta la fecha:
  rendimiento neto de la actividad (estimación directa simplificada, con el 5 % de gastos de difícil
  justificación) − reducción del 20 % por inicio de actividad (art. 32.3 LIRPF, primer año con
  rendimiento positivo y el siguiente) + parte atribuida de CB de capital inmobiliario
  = base liquidable general → escala estatal + escala autonómica − mínimo personal
  − retenciones soportadas − pagos fraccionados (130) = a pagar en la Renta.

No incluye otras rentas (trabajo, ahorro), mínimos familiares, aportaciones a planes de pensiones ni
deducciones autonómicas. Las escalas son las vigentes conocidas y deben revisarse cada año."""

from ..entidades import Entidad
from ..fechas import trimestre
from .irpf import modelo_130
from .renta import ficha_comunero
from . import AVISO

# (hasta, tipo %) en euros. Revisar cada ejercicio.
ESCALA_ESTATAL = [(12450, 9.5), (20200, 12.0), (35200, 15.0), (60000, 18.5), (300000, 22.5), (None, 24.5)]
ESCALAS_AUTONOMICAS = {
    "madrid": [(13362.22, 8.5), (19004.63, 10.7), (35425.68, 12.8), (57320.40, 17.4), (None, 20.5)],
}
MINIMO_PERSONAL = 5550
PCT_REDUCCION_INICIO = 20


def escala(base: float, tramos) -> float:
    cuota, desde = 0.0, 0.0
    for hasta, tipo in tramos:
        tope = base if hasta is None else min(base, hasta)
        if tope > desde:
            cuota += (tope - desde) * tipo / 100
        if hasta is None or base <= hasta:
            break
        desde = hasta
    return cuota


def _cuota(base_eur: float, tramos) -> float:
    return max(escala(base_eur, tramos) - escala(min(MINIMO_PERSONAL, base_eur), tramos), 0)


def estimar_renta(conn, autonomo: Entidad, ejercicio: int, hasta_trimestre: int = 4) -> dict:
    desde, hasta = f"{ejercicio}-01-01", trimestre(ejercicio, hasta_trimestre)[1]
    # Mismos acumulados que el 130 (incluye lo presentado antes de usar FinSoft y la parte de CB de actividad)
    m130 = modelo_130(conn, autonomo, ejercicio, hasta_trimestre)
    c = m130["casillas"]
    djust = m130["gastos_dificil_justificacion"]
    act = {"ingresos": c["01"], "gastos": c["02"] - djust, "retenciones": c["06"]}
    rend_neto = c["03"]
    avisos = []
    reduccion = 0
    ini = autonomo.anio_inicio_actividad
    if ini and ejercicio in (ini, ini + 1) and rend_neto > 0:
        reduccion = int(round(rend_neto * PCT_REDUCCION_INICIO / 100))
    elif ini is None:
        avisos.append("Indica el año de inicio de actividad (anio_inicio_actividad) para aplicar, si procede, la "
                      "reducción del 20 % de los dos primeros años.")
    inmob = 0
    for f in ficha_comunero(conn, autonomo, ejercicio):
        inmob += f["atribuido"]["rendimiento_neto"] + f["atribuido"]["imputacion_rentas"]
    # Otras rentas de la base general del año (p. ej. rendimiento neto de una nómina) indicadas en la configuración
    otras = autonomo.otras_rentas
    base = rend_neto - reduccion + inmob + otras
    tramos_auto = ESCALAS_AUTONOMICAS.get(autonomo.comunidad_autonoma)
    if tramos_auto is None:
        avisos.append(f"Escala autonómica de '{autonomo.comunidad_autonoma}' no cargada: se usa la estatal.")
        tramos_auto = ESCALA_ESTATAL
    base_eur = max(base, 0) / 100
    cuota_estatal = int(round(_cuota(base_eur, ESCALA_ESTATAL) * 100))
    cuota_autonomica = int(round(_cuota(base_eur, tramos_auto) * 100))
    cuota = cuota_estatal + cuota_autonomica
    from . import presentadas
    pagos_130 = 0
    for t in range(1, hasta_trimestre + 1):
        pres = presentadas.obtener(conn, autonomo, "130", ejercicio, f"{t}T")
        pagos_130 += pres["resultado"] if pres else modelo_130(conn, autonomo, ejercicio, t)["resultado"]
    retenciones = act["retenciones"] + autonomo.otras_retenciones
    a_pagar = cuota - retenciones - pagos_130
    tipo_medio = round(cuota / base * 100, 1) if base > 0 else 0
    return {
        "entidad": autonomo.codigo, "nombre": autonomo.nombre, "ejercicio": ejercicio,
        "datos_hasta": hasta, "comunidad_autonoma": autonomo.comunidad_autonoma,
        "ingresos_actividad": act["ingresos"], "gastos_actividad": act["gastos"],
        "gastos_dificil_justificacion": djust, "rendimiento_neto_actividad": rend_neto,
        "reduccion_inicio_actividad": reduccion, "rendimiento_capital_inmobiliario_cb": inmob,
        "otras_rentas": otras, "base_liquidable_general": base, "cuota_estatal": cuota_estatal, "cuota_autonomica": cuota_autonomica,
        "cuota_estimada": cuota, "tipo_medio_pct": tipo_medio,
        "retenciones_soportadas": retenciones, "pagos_fraccionados_130": pagos_130,
        "provision_necesaria": max(a_pagar, 0), "resultado_estimado": a_pagar,
        "tipo_resultado": "A pagar en la Renta" if a_pagar > 0 else "A devolver en la Renta",
        "avisos": avisos + [
            "Estimación con los datos contabilizados hasta la fecha: si siguen entrando ingresos, la provisión crecerá.",
            "Otras rentas sólo si se indican en la configuración (otras_rentas, otras_retenciones). No incluye "
            "rentas del ahorro, mínimos familiares, planes de pensiones ni deducciones; la cuota real puede variar.",
        ],
        "aviso": AVISO,
    }


CONCEPTOS = [
    ("ingresos_actividad", "Ingresos de la actividad"),
    ("gastos_actividad", "(–) Gastos deducibles"),
    ("gastos_dificil_justificacion", "(–) Gastos de difícil justificación (5 %, máx. 2.000 €)"),
    ("rendimiento_neto_actividad", "= Rendimiento neto de la actividad"),
    ("reduccion_inicio_actividad", "(–) Reducción 20 % por inicio de actividad"),
    ("rendimiento_capital_inmobiliario_cb", "(+) Rendimiento atribuido de la CB (capital inmobiliario)"),
    ("otras_rentas", "(+) Otras rentas del año (nómina, etc.)"),
    ("base_liquidable_general", "= Base liquidable general"),
    ("cuota_estatal", "Cuota estatal"),
    ("cuota_autonomica", "Cuota autonómica"),
    ("cuota_estimada", "= Cuota estimada de la Renta"),
    ("retenciones_soportadas", "(–) Retenciones que te han practicado (actividad y otras rentas)"),
    ("pagos_fraccionados_130", "(–) Pagos fraccionados (130) presentados"),
    ("resultado_estimado", "= Resultado estimado de la Renta"),
    ("provision_necesaria", "PROVISIÓN NECESARIA A FECHA DE HOY"),
]


def filas(est: dict) -> list[dict]:
    return [{"concepto": n, "importe": est[k], "nivel": 0 if n.startswith(("=", "PROVISIÓN")) else 1}
            for k, n in CONCEPTOS]
