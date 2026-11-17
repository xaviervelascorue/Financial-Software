"""Operaciones sin factura: inversiones de la holding, dividendos, gastos/ingresos
directos (cuota de autónomos, comisiones bancarias, tributos) y amortizaciones."""

from datetime import date, timedelta

from .entidades import Entidad, get_entidad, linea_id, titulares
from .fechas import dias
from .ledger import Apunte, crear_asiento
from .money import D, from_cents, r2, to_cents
from .pgc import AMORT_ACUMULADA

# % de la renta exenta por art. 21 LIS (el 5 % restante tributa como gastos de gestión)
PCT_EXENCION_ART21 = D("95")


def add_ajuste_fiscal(conn, entidad: Entidad, fecha: str, concepto: str, importe, referencia=None, commit=True):
    """importe > 0 aumenta la base imponible del IS; < 0 la disminuye."""
    conn.execute("INSERT INTO ajustes_fiscales (entidad_id, fecha, concepto, importe, referencia) VALUES (?,?,?,?,?)",
                 (entidad.id, fecha, concepto, to_cents(importe), referencia))
    if commit:
        conn.commit()


def movimiento_directo(conn, entidad: Entidad, fecha: str, concepto: str, cuenta: str, importe,
                       linea: str | None = None, contrapartida: str = "5720") -> int:
    """Gasto (grupo 6) o ingreso (grupo 7) sin factura contra tesorería. Ej.: cuota RETA (6420),
    comisiones bancarias (6260), IBI (6310)."""
    imp = r2(importe)
    lid = linea_id(conn, entidad, linea)
    if cuenta.startswith("7"):
        ap = [Apunte(contrapartida, debe=imp), Apunte(cuenta, haber=imp, linea_id=lid)]
    else:
        ap = [Apunte(cuenta, debe=imp, linea_id=lid), Apunte(contrapartida, haber=imp)]
    return crear_asiento(conn, entidad, fecha, concepto, ap, origen="directo")


def compra_inversion(conn, entidad: Entidad, fecha: str, concepto: str, cuenta: str, importe,
                     linea: str | None = None, contrapartida: str = "5720") -> int:
    """Alta de una inversión financiera o inmobiliaria (2403, 2500, 5400, 2210...). Suma el importe al
    capital invertido de la línea para calcular su rentabilidad."""
    imp = r2(importe)
    lid = linea_id(conn, entidad, linea)
    aid = crear_asiento(conn, entidad, fecha, concepto,
                        [Apunte(cuenta, debe=imp, linea_id=lid), Apunte(contrapartida, haber=imp)],
                        origen="inversion", commit=False)
    if lid:
        conn.execute("UPDATE lineas SET capital_invertido = capital_invertido + ? WHERE id=?", (to_cents(imp), lid))
    conn.commit()
    return aid


def venta_inversion(conn, entidad: Entidad, fecha: str, concepto: str, cuenta: str, coste, precio,
                    linea: str | None = None, exento_art21: bool = False, retencion=0,
                    contrapartida: str = "5720") -> int:
    coste, precio, ret = r2(coste), r2(precio), r2(retencion)
    lid = linea_id(conn, entidad, linea)
    resultado = precio - coste
    ap = [Apunte(contrapartida, debe=precio - ret), Apunte(cuenta, haber=coste, linea_id=lid)]
    if ret:
        ap.append(Apunte("4730", debe=ret))
    if resultado > 0:
        ap.append(Apunte("7660", haber=resultado, linea_id=lid))
    elif resultado < 0:
        ap.append(Apunte("6660", debe=-resultado, linea_id=lid))
    aid = crear_asiento(conn, entidad, fecha, concepto, ap, origen="inversion", commit=False)
    if lid:
        conn.execute("UPDATE lineas SET capital_invertido = MAX(capital_invertido - ?, 0) WHERE id=?",
                     (to_cents(coste), lid))
    if exento_art21 and resultado > 0 and entidad.tipo == "sociedad":
        add_ajuste_fiscal(conn, entidad, fecha, f"Exención art. 21 LIS plusvalía: {concepto}",
                          -r2(resultado * PCT_EXENCION_ART21 / 100), commit=False)
    conn.commit()
    return aid


def dividendo_recibido(conn, entidad: Entidad, fecha: str, concepto: str, bruto, retencion_pct=19,
                       linea: str | None = None, grupo: bool = False, exento_art21: bool = False,
                       contrapartida: str = "5720") -> int:
    bruto = r2(bruto)
    ret = r2(bruto * D(retencion_pct) / 100)
    lid = linea_id(conn, entidad, linea)
    cuenta = "7600" if grupo else "7603"
    ap = [Apunte(contrapartida, debe=bruto - ret), Apunte(cuenta, haber=bruto, linea_id=lid)]
    if ret:
        ap.append(Apunte("4730", debe=ret))
    aid = crear_asiento(conn, entidad, fecha, concepto, ap, origen="inversion", commit=False)
    if exento_art21 and entidad.tipo == "sociedad":
        add_ajuste_fiscal(conn, entidad, fecha, f"Exención art. 21 LIS dividendo: {concepto}",
                          -r2(bruto * PCT_EXENCION_ART21 / 100), commit=False)
    conn.commit()
    return aid


def interes_recibido(conn, entidad: Entidad, fecha: str, concepto: str, bruto, retencion_pct=19,
                     linea: str | None = None, cuenta: str = "7690", contrapartida: str = "5720") -> int:
    bruto = r2(bruto)
    ret = r2(bruto * D(retencion_pct) / 100)
    lid = linea_id(conn, entidad, linea)
    ap = [Apunte(contrapartida, debe=bruto - ret), Apunte(cuenta, haber=bruto, linea_id=lid)]
    if ret:
        ap.append(Apunte("4730", debe=ret))
    return crear_asiento(conn, entidad, fecha, concepto, ap, origen="inversion")


def distribuir_dividendo(conn, entidad: Entidad, fecha: str, importe, cuenta_cargo: str = "1130",
                         retencion_pct=19, registrar_en_socios: bool = True) -> int:
    """Reparto de dividendos a los socios según participaciones. Retiene el 19 % a personas físicas
    (modelo 123). Las sociedades socias con ≥5 % no soportan retención (art. 61 RIS). Si el socio es
    una sociedad registrada, registra el dividendo recibido en sus libros (exento art. 21 si ≥5 %)."""
    if entidad.tipo != "sociedad":
        raise ValueError("Solo las sociedades distribuyen dividendos")
    socios = titulares(conn, entidad)
    if not socios:
        raise ValueError(f"{entidad.codigo} no tiene socios registrados")
    total = r2(importe)
    ap = [Apunte(cuenta_cargo, debe=total)]
    total_ret = D("0")
    pendientes_socio = []
    for s in socios:
        parte = r2(total * D(s["porcentaje"]) / 100)
        socio_ent = get_entidad(conn, s["titular_entidad_id"]) if s["titular_entidad_id"] else None
        es_sociedad = socio_ent is not None and socio_ent.tipo == "sociedad"
        ret = D("0") if (es_sociedad and s["porcentaje"] >= 5) else r2(parte * D(retencion_pct) / 100)
        total_ret += ret
        if ret:
            conn.execute(
                "INSERT INTO retenciones (entidad_id, fecha, modelo, clave, perceptor_nif, perceptor_nombre, base,"
                " retencion) VALUES (?,?,?,?,?,?,?,?)",
                (entidad.id, fecha, "123", "dividendos", s["titular_nif"], s["titular_nombre"], to_cents(parte),
                 to_cents(ret)),
            )
        if socio_ent is not None and es_sociedad and registrar_en_socios:
            pendientes_socio.append((socio_ent, parte, s["porcentaje"]))
    ap.append(Apunte("5720", haber=total - total_ret))
    if total_ret:
        ap.append(Apunte("4751", haber=total_ret))
    aid = crear_asiento(conn, entidad, fecha, f"Distribución de dividendos {entidad.nombre}", ap, origen="dividendo")
    for socio_ent, parte, pct in pendientes_socio:
        dividendo_recibido(conn, socio_ent, fecha, f"Dividendo de {entidad.nombre}", parte,
                           retencion_pct=0 if pct >= 5 else retencion_pct, linea=None, grupo=pct >= 5,
                           exento_art21=pct >= 5)
    return aid


# ---------------------------------------------------------------- amortizaciones

def dotar_amortizaciones(conn, entidad: Entidad, hasta: str) -> int | None:
    """Amortización lineal diaria de los activos hasta la fecha indicada. Un asiento por ejecución."""
    activos = conn.execute("SELECT * FROM activos WHERE entidad_id=? AND fecha_alta<=?", (entidad.id, hasta)).fetchall()
    ap, updates = [], []
    for a in activos:
        inicio = a["fecha_alta"] if not a["amortizado_hasta"] else \
            (date.fromisoformat(a["amortizado_hasta"]) + timedelta(days=1)).isoformat()
        if inicio > hasta:
            continue
        amortizable = a["coste"] - a["valor_residual"]
        restante = amortizable - a["amortizado"]
        if restante <= 0:
            continue
        cuota = min(round(amortizable / a["vida_util_anios"] * dias(inicio, hasta) / 365), restante)
        if cuota <= 0:
            continue
        g = a["cuenta_activo"][:2]
        dotacion = {"20": "6800", "21": "6810", "22": "6820"}[g]
        acum = AMORT_ACUMULADA.get(a["cuenta_activo"][:3], {"20": "2800", "21": "2819", "22": "2821"}[g])
        ap += [Apunte(dotacion, debe=from_cents(cuota), linea_id=a["linea_id"]), Apunte(acum, haber=from_cents(cuota))]
        updates.append((cuota, hasta, a["id"]))
    if not ap:
        return None
    aid = crear_asiento(conn, entidad, hasta, f"Amortización del inmovilizado hasta {hasta}", ap,
                        origen="amortizacion", commit=False)
    conn.executemany("UPDATE activos SET amortizado = amortizado + ?, amortizado_hasta=? WHERE id=?", updates)
    conn.commit()
    return aid
