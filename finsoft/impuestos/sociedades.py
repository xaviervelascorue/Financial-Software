"""Impuesto sobre Sociedades: pagos fraccionados (modelo 202) y estimación anual (modelo 200)."""

from decimal import ROUND_FLOOR, Decimal

from ..entidades import Entidad
from ..estados import pyg
from ..fechas import ejercicio as rango_ejercicio, fin_mes
from ..ledger import Apunte, crear_asiento, saldo
from ..money import from_cents
from . import AVISO

MESES_202 = {1: 3, 2: 9, 3: 11}  # 1P (abril): 3 primeros meses; 2P (octubre): 9; 3P (diciembre): 11


def _ajustes(conn, entidad, desde, hasta) -> tuple[int, list]:
    rows = conn.execute("SELECT fecha, concepto, importe FROM ajustes_fiscales WHERE entidad_id=? AND fecha BETWEEN ? AND ?",
                        (entidad.id, desde, hasta)).fetchall()
    return sum(r["importe"] for r in rows), [dict(r) for r in rows]


def _compensar_bins(entidad: Entidad, base_previa: int) -> int:
    """Compensación de bases imponibles negativas: 70 % de la base previa, con mínimo de 1 M€ (art. 26 LIS)."""
    if base_previa <= 0 or entidad.bins_pendientes <= 0:
        return 0
    limite = max(int(base_previa * 0.70), 100_000_000)
    return min(entidad.bins_pendientes, base_previa, limite)


def tipo_202_base(tipo_is: float) -> int:
    """Modalidad art. 40.3: 5/7 del tipo de gravamen redondeado por defecto."""
    return int((Decimal(str(tipo_is)) * 5 / 7).to_integral_value(rounding=ROUND_FLOOR))


def modelo_202(conn, entidad: Entidad, ejercicio: int, periodo: int, modalidad: str | None = None) -> dict:
    if entidad.tipo != "sociedad":
        raise ValueError("El modelo 202 sólo aplica a sociedades")
    if periodo not in MESES_202:
        raise ValueError("Periodo 202: 1 (abril), 2 (octubre) o 3 (diciembre)")
    obligatoria_403 = entidad.cifra_negocios_anterior > 600_000_000
    modalidad = "40.3" if obligatoria_403 else (modalidad or entidad.modalidad_202)
    desde, hasta = f"{ejercicio}-01-01", fin_mes(ejercicio, MESES_202[periodo])
    avisos = []
    if obligatoria_403 and entidad.modalidad_202 != "40.3":
        avisos.append("Cifra de negocios del año anterior > 6 M€: la modalidad art. 40.3 es obligatoria.")
    if modalidad == "40.2":
        base = max(entidad.cuota_base_202, 0)
        pago = int(round(base * 0.18))
        detalle = {"base_cuota_ultimo_ejercicio": base, "porcentaje": 18, "a_ingresar": pago}
        if base == 0:
            avisos.append("Sin cuota positiva en el último IS declarado: el 202 (art. 40.2) sale a cero. "
                          "Si es la primera vez, no hay obligación de presentarlo.")
    else:
        p = pyg(conn, entidad, desde, hasta)
        resultado = p["C) RESULTADO ANTES DE IMPUESTOS"]
        ajustes, _ = _ajustes(conn, entidad, desde, hasta)
        base_previa = resultado + ajustes
        bins = _compensar_bins(entidad, base_previa)
        base = max(base_previa - bins, 0)
        pct = tipo_202_base(entidad.tipo_is)
        cuota = int(round(base * pct / 100))
        retenciones = saldo(conn, entidad, "4730", desde, hasta)
        previos = sum(modelo_202(conn, entidad, ejercicio, pp, modalidad)["resultado"] for pp in range(1, periodo))
        pago = max(cuota - retenciones - previos, 0)
        detalle = {"resultado_contable": resultado, "ajustes_fiscales": ajustes, "bins_compensadas": bins,
                   "base_imponible_periodo": base, "porcentaje": pct, "cuota": cuota,
                   "retenciones_periodo": retenciones, "pagos_fraccionados_previos": previos, "a_ingresar": pago}
    return {"modelo": "202", "entidad": entidad.codigo, "nif": entidad.nif, "ejercicio": ejercicio,
            "periodo": f"{periodo}P", "modalidad": f"art. {modalidad} LIS", "detalle": detalle, "resultado": pago,
            "avisos": avisos, "aviso": AVISO}


def estimacion_is(conn, entidad: Entidad, ejercicio: int) -> dict:
    """Estimación del Impuesto sobre Sociedades del ejercicio (borrador del modelo 200)."""
    if entidad.tipo != "sociedad":
        raise ValueError("Sólo para sociedades")
    desde, hasta = rango_ejercicio(ejercicio)
    p = pyg(conn, entidad, desde, hasta)
    resultado = p["C) RESULTADO ANTES DE IMPUESTOS"]
    ajustes, lista_ajustes = _ajustes(conn, entidad, desde, hasta)
    base_previa = resultado + ajustes
    bins = _compensar_bins(entidad, base_previa)
    base = base_previa - bins
    cuota_integra = int(round(max(base, 0) * entidad.tipo_is / 100))
    retenciones = saldo(conn, entidad, "4730", desde, hasta, excluir_origen=("impuesto",))
    pagos = saldo(conn, entidad, "4731", desde, hasta, excluir_origen=("impuesto",))
    diferencial = cuota_integra - retenciones - pagos
    avisos = []
    if entidad.es_patrimonial:
        avisos.append("Entidad patrimonial: no puede aplicar el tipo reducido de entidades de nueva creación "
                      "ni la reserva de capitalización/nivelación; las BINs tienen restricciones (art. 26.4 LIS).")
    if base < 0:
        avisos.append(f"Base imponible negativa de {from_cents(-base)} € a compensar en ejercicios futuros.")
    return {"modelo": "200", "entidad": entidad.codigo, "ejercicio": ejercicio,
            "resultado_contable_antes_impuestos": resultado, "ajustes": lista_ajustes, "total_ajustes": ajustes,
            "base_previa": base_previa, "bins_compensadas": bins, "base_imponible": base,
            "tipo_gravamen": entidad.tipo_is, "cuota_integra": cuota_integra, "retenciones": retenciones,
            "pagos_fraccionados": pagos, "cuota_diferencial": diferencial,
            "tipo_resultado": "A ingresar" if diferencial > 0 else "A devolver", "avisos": avisos, "aviso": AVISO}


def contabilizar_is(conn, entidad: Entidad, est: dict) -> int:
    """Gasto por impuesto corriente: 6300 contra retenciones, pagos fraccionados y 4752 / 4709."""
    ap = [Apunte("6300", debe=from_cents(est["cuota_integra"]))]
    if est["retenciones"]:
        ap.append(Apunte("4730", haber=from_cents(est["retenciones"])))
    if est["pagos_fraccionados"]:
        ap.append(Apunte("4731", haber=from_cents(est["pagos_fraccionados"])))
    dif = est["cuota_diferencial"]
    if dif > 0:
        ap.append(Apunte("4752", haber=from_cents(dif)))
    elif dif < 0:
        ap.append(Apunte("4709", debe=from_cents(-dif)))
    return crear_asiento(conn, entidad, f"{est['ejercicio']}-12-31", f"Impuesto sobre Sociedades {est['ejercicio']}",
                         ap, origen="impuesto")
