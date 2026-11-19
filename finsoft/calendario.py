"""Calendario fiscal y de reporting por entidad.

Genera, para un ejercicio, las obligaciones de cada entidad según su tipo y régimen (autoliquidaciones,
declaraciones informativas, Renta, Impuesto sobre Sociedades, cuentas anuales) con su fecha límite, la
condición que las activa y la información que hay que pasar a FinSoft para prepararlas. Incluye además los
hitos internos de envío de documentación y cierre.

Plazos generales de la AEAT (si el último día es sábado, domingo o festivo nacional, pasa al siguiente hábil):
  trimestrales 1T-3T: del 1 al 20 de abril, julio y octubre · 4T: hasta el 30 de enero
  con domiciliación bancaria terminan 5 días antes (15 / 25 de enero)
Revisa cada año el calendario oficial del contribuyente en la Sede Electrónica."""

from dataclasses import dataclass, field
from datetime import date, timedelta

from .entidades import Entidad, listar_entidades, participadas

# Festivos nacionales fijos (los móviles y autonómicos no se calculan)
FESTIVOS = {(1, 1), (1, 6), (5, 1), (8, 15), (10, 12), (11, 1), (12, 6), (12, 8), (12, 25)}


def habil(d: date) -> date:
    while d.weekday() >= 5 or (d.month, d.day) in FESTIVOS:
        d += timedelta(days=1)
    return d


@dataclass
class Obligacion:
    fecha: date
    entidad: str
    modelo: str
    periodo: str
    descripcion: str
    informacion: str
    condicion: str = ""
    domiciliacion: date | None = None
    tipo: str = "fiscal"           # fiscal | mercantil | interno
    notas: list = field(default_factory=list)

    def fila(self) -> dict:
        return {"fecha": self.fecha.isoformat(), "domiciliacion": self.domiciliacion.isoformat() if self.domiciliacion else "",
                "entidad": self.entidad, "modelo": self.modelo, "periodo": self.periodo,
                "descripcion": self.descripcion, "condicion": self.condicion, "informacion": self.informacion,
                "tipo": self.tipo}


INFO_TRIMESTRE = ("Facturas emitidas del trimestre (Excel/PDF), facturas y tiques de gastos, extractos del banco y "
                  "de la tarjeta (xls), cuota de autónomos")
INFO_TRIM_CB = ("Liquidaciones de las plataformas de reserva y cobros directos, facturas de gastos de la vivienda "
                "(limpieza, suministros, comunidad, IBI, seguro), extractos bancarios")
INFO_SOCIEDAD = "Facturas recibidas, extractos bancarios, movimientos de inversiones y préstamos, contratos con el socio"


def _trimestrales(ejercicio: int) -> list[tuple[str, date]]:
    return [("1T", date(ejercicio, 4, 20)), ("2T", date(ejercicio, 7, 20)), ("3T", date(ejercicio, 10, 20)),
            ("4T", date(ejercicio + 1, 1, 30))]


def _dom(d: date) -> date:
    return habil(d - timedelta(days=5))


def obligaciones(conn, ejercicio: int) -> list[Obligacion]:
    """Obligaciones derivadas del ejercicio indicado (incluye las de enero-julio del año siguiente)."""
    from .impuestos.irpf import pct_ingresos_con_retencion
    out: list[Obligacion] = []
    ents = listar_entidades(conn)
    for e in ents:
        primer_anio = e.anio_inicio_actividad == ejercicio
        ret = conn.execute("SELECT DISTINCT modelo FROM retenciones WHERE entidad_id=? AND substr(fecha,1,4) IN (?,?)",
                           (e.id, str(ejercicio), str(ejercicio - 1))).fetchall()
        modelos_ret = sorted(r[0] for r in ret)

        # ---- IVA
        if e.presenta_303:
            for per, f in _trimestrales(ejercicio):
                out.append(Obligacion(habil(f), e.codigo, "303", per, "IVA trimestral",
                                      INFO_TRIM_CB if e.tipo == "comunidad_bienes" else
                                      (INFO_TRIMESTRE if e.tipo == "autonomo" else INFO_SOCIEDAD),
                                      domiciliacion=_dom(habil(f)),
                                      condicion="En el 4T, regularización de la prorrata" if e.regimen_iva == "prorrata"
                                      else ""))
            out.append(Obligacion(habil(date(ejercicio + 1, 1, 30)), e.codigo, "390", str(ejercicio),
                                  "Resumen anual de IVA", "Se genera con los cuatro 303 del año"))
        elif e.tipo != "sociedad" or not e.es_holding:
            for per, f in _trimestrales(ejercicio):
                out.append(Obligacion(habil(f), e.codigo, "303", per, "IVA por inversión del sujeto pasivo",
                                      "Facturas de proveedores extranjeros sin IVA (p. ej. comisiones de plataformas)",
                                      condicion="Sólo si hay compras de servicios a proveedores de fuera de España "
                                                "sin IVA (autoliquidación); revisar si las plataformas cobran IVA español",
                                      domiciliacion=_dom(habil(f))))
        # ---- IRPF pagos fraccionados
        if e.tipo == "autonomo":
            pct = pct_ingresos_con_retencion(conn, e, f"{ejercicio - 1}-01-01", f"{ejercicio - 1}-12-31")
            if pct is None and e.pct_retencion_anterior is not None:
                pct = e.pct_retencion_anterior
            if e.exento_130:
                cond = "Exento por configuración"
            elif pct is None:
                cond = "Se decide cada trimestre: no se presenta si ≥70 % de los ingresos del trimestre llevan retención"
            elif pct >= 70:
                cond = f"No obligado: {pct} % de los ingresos de {ejercicio - 1} con retención (art. 109.1 RIRPF)"
            else:
                cond = f"Obligatorio: {pct} % de los ingresos de {ejercicio - 1} con retención (<70 %)"
            if not (pct is not None and pct >= 70) and not e.exento_130:
                for per, f in _trimestrales(ejercicio):
                    out.append(Obligacion(habil(f), e.codigo, "130", per, "Pago fraccionado IRPF", INFO_TRIMESTRE,
                                          condicion=cond, domiciliacion=_dom(habil(f))))
            else:
                out.append(Obligacion(habil(date(ejercicio + 1, 1, 30)), e.codigo, "130", str(ejercicio),
                                      "Pago fraccionado IRPF: no se presenta", "—", condicion=cond))
        # ---- retenciones practicadas
        for mod in modelos_ret:
            for per, f in _trimestrales(ejercicio):
                out.append(Obligacion(habil(f), e.codigo, mod, per, f"Retenciones practicadas (modelo {mod})",
                                      "Facturas de profesionales / alquileres con retención, repartos de dividendos",
                                      domiciliacion=_dom(habil(f))))
            anual = {"111": "190", "115": "180", "123": "193"}[mod]
            out.append(Obligacion(habil(date(ejercicio + 1, 1, 31)), e.codigo, anual, str(ejercicio),
                                  f"Resumen anual de retenciones (modelo {anual})", "Se genera con los trimestrales"))
        # ---- informativas
        if e.tipo in ("autonomo", "comunidad_bienes") or (e.tipo == "sociedad" and e.presenta_303):
            out.append(Obligacion(habil(date(ejercicio + 1, 2, 28)), e.codigo, "347", str(ejercicio),
                                  "Operaciones con terceros > 3.005,06 €", "Se genera con las facturas registradas",
                                  condicion="Sólo si algún cliente o proveedor supera 3.005,06 € en el año (sin contar "
                                            "operaciones con retención ni particulares sin NIF)"))
        if e.tipo == "comunidad_bienes":
            out.append(Obligacion(habil(date(ejercicio + 1, 1, 31)), e.codigo, "184", str(ejercicio),
                                  "Entidades en atribución de rentas",
                                  "Datos de los comuneros (NIF, %), referencia y valor catastral, días alquilados y de uso "
                                  "propio, precio de adquisición del inmueble y del mobiliario"))
        # ---- Renta
        if e.tipo == "autonomo":
            cbs = [p["codigo"] for p in participadas(conn, e) if p["participada_tipo"] == "comunidad_bienes"]
            out.append(Obligacion(habil(date(ejercicio + 1, 6, 30)), e.codigo, "100", str(ejercicio),
                                  "Declaración de la Renta (campaña de abril a junio)",
                                  "Datos fiscales de la AEAT, otras rentas (nómina, cuentas, inversiones), planes de "
                                  "pensiones, situación familiar" + (f"; parte atribuida de {', '.join(cbs)}" if cbs else ""),
                                  domiciliacion=habil(date(ejercicio + 1, 6, 25))))
        # ---- Sociedades
        if e.tipo == "sociedad":
            out.append(Obligacion(habil(date(ejercicio + 1, 7, 25)), e.codigo, "200", str(ejercicio),
                                  "Impuesto sobre Sociedades", "Cierre contable del ejercicio, ajustes fiscales, "
                                  "dividendos y plusvalías de participadas"))
            for per, f in (("1P", date(ejercicio, 4, 20)), ("2P", date(ejercicio, 10, 20)), ("3P", date(ejercicio, 12, 20))):
                if primer_anio and e.modalidad_202 != "40.3":
                    cond = "No aplica: primer ejercicio, sin Impuesto sobre Sociedades anterior"
                elif e.modalidad_202 == "40.2" and e.cuota_base_202 <= 0:
                    cond = "Sólo si el último IS presentado tuvo cuota positiva"
                else:
                    cond = f"Modalidad art. {e.modalidad_202} LIS"
                out.append(Obligacion(habil(f), e.codigo, "202", per, "Pago fraccionado IS",
                                      INFO_SOCIEDAD, condicion=cond, domiciliacion=_dom(habil(f))))
            out += [
                Obligacion(date(ejercicio + 1, 3, 31), e.codigo, "CCAA", str(ejercicio), "Formulación de cuentas anuales",
                           "Cierre contable, inventario de inversiones", tipo="mercantil"),
                Obligacion(date(ejercicio + 1, 4, 30), e.codigo, "Libros", str(ejercicio),
                           "Legalización telemática de libros (diario, inventarios y cuentas anuales, actas, socios, "
                           "contratos con el socio único)", "Libros generados por FinSoft y libros de actas", tipo="mercantil"),
                Obligacion(date(ejercicio + 1, 6, 30), e.codigo, "Junta", str(ejercicio),
                           "Aprobación de cuentas (decisión del socio único) y aplicación del resultado",
                           "Acta de decisiones del socio único", tipo="mercantil"),
                Obligacion(date(ejercicio + 1, 7, 30), e.codigo, "Depósito", str(ejercicio),
                           "Depósito de cuentas en el Registro Mercantil (con declaración de titularidad real)",
                           "Cuentas aprobadas, certificado del acuerdo", tipo="mercantil"),
            ]
    # ---- hitos internos FinSoft
    for mes in range(1, 13):
        anio, m = (ejercicio, mes + 1) if mes < 12 else (ejercicio + 1, 1)
        out.append(Obligacion(date(anio, m, 5), "GRUPO", "Envío", f"{mes:02d}/{ejercicio}",
                              "Enviar a FinSoft la documentación del mes", INFO_TRIMESTRE + "; liquidaciones de la vivienda "
                              "turística; movimientos de ARBOREA", tipo="interno"))
    for per, f in _trimestrales(ejercicio):
        cierre = f.replace(day=10)
        out.append(Obligacion(cierre, "GRUPO", "Cierre", per, "Cierre trimestral en FinSoft: borradores de modelos, "
                              "estados financieros, informes de gestión y provisiones",
                              "Todo lo del trimestre enviado y revisado", tipo="interno"))
    return sorted(out, key=lambda o: (o.fecha, o.tipo != "interno", o.entidad, o.modelo))
