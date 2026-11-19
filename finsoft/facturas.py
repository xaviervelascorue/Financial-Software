"""Registro de facturas emitidas y recibidas con contabilización automática."""

import sqlite3
from dataclasses import dataclass, field
from decimal import Decimal

from . import pgc
from .entidades import Entidad, linea_exenta, linea_id, tercero
from .ledger import Apunte, crear_asiento
from .money import D, from_cents, r2, to_cents

TIPOS_OPERACION = ("interior", "intracomunitaria", "importacion", "exportacion", "isp", "exenta", "no_sujeta")
# Operaciones recibidas en las que el destinatario autoliquida el IVA
AUTOLIQUIDACION = ("intracomunitaria", "isp")
TIPOS_RETENCION = ("ninguna", "profesional", "alquiler", "actividad", "capital_mobiliario")


class FacturaDuplicada(ValueError):
    pass


@dataclass
class LineaFactura:
    base: Decimal
    tipo_iva: Decimal = Decimal("21")
    concepto: str = ""
    cuenta: str | None = None
    linea: str | None = None
    cuota_iva: Decimal | None = None
    # % de IVA deducible de esta línea (p. ej. 50 en vehículos de autónomos). None = el de la entidad
    pct_deducible: float | None = None


@dataclass
class FacturaInput:
    tipo: str
    numero: str
    fecha: str
    tercero_nif: str
    tercero_nombre: str
    lineas: list[LineaFactura]
    tercero_pais: str = "ES"
    tipo_operacion: str = "interior"
    retencion_tipo: str = "ninguna"
    retencion_pct: Decimal = Decimal("0")
    retencion_importe: Decimal | None = None
    total_declarado: Decimal | None = None
    fecha_operacion: str | None = None
    concepto: str = ""
    cuenta: str | None = None
    linea: str | None = None
    bien_inversion: bool = False
    vida_util: float | None = None
    archivo: str | None = None
    avisos: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "FacturaInput":
        d = dict(d)
        lineas = [LineaFactura(**{k: (D(v) if k in ("base", "tipo_iva", "cuota_iva") and v is not None else v)
                                  for k, v in ln.items()}) for ln in d.pop("lineas")]
        for k in ("retencion_pct", "retencion_importe", "total_declarado"):
            if d.get(k) is not None:
                d[k] = D(d[k])
        return cls(lineas=lineas, **d)


def _habito(conn, entidad, tercero_id, tipo):
    return conn.execute("SELECT cuenta, linea_id FROM habitos WHERE entidad_id=? AND tercero_id=? AND tipo=?",
                        (entidad.id, tercero_id, tipo)).fetchone()


def preparar(conn, entidad: Entidad, f: FacturaInput) -> dict:
    """Calcula importes, cuentas y el asiento propuesto, sin grabar nada."""
    if f.tipo not in ("emitida", "recibida"):
        raise ValueError("tipo debe ser 'emitida' o 'recibida'")
    if f.tipo_operacion not in TIPOS_OPERACION:
        raise ValueError(f"tipo_operacion no válido: {f.tipo_operacion}")
    if f.retencion_tipo not in TIPOS_RETENCION:
        raise ValueError(f"retencion_tipo no válido: {f.retencion_tipo}")
    if not f.lineas:
        raise ValueError("La factura no tiene líneas")

    tid = tercero(conn, f.tercero_nif, f.tercero_nombre, f.tercero_pais)
    habito = _habito(conn, entidad, tid, f.tipo)
    avisos = list(f.avisos)
    sin_iva_repercutido = f.tipo == "emitida" and f.tipo_operacion != "interior"
    autoliq = f.tipo == "recibida" and f.tipo_operacion in AUTOLIQUIDACION

    lineas_calc = []
    for ln in f.lineas:
        base = r2(ln.base)
        lid = linea_id(conn, entidad, ln.linea or f.linea) if (ln.linea or f.linea) else \
            (habito["linea_id"] if habito else None)
        exenta = linea_exenta(conn, lid) or (f.tipo == "emitida" and entidad.regimen_iva == "exento")
        tipo_iva = D(ln.tipo_iva)
        if sin_iva_repercutido or (f.tipo == "emitida" and exenta):
            if f.tipo == "emitida" and exenta and tipo_iva:
                avisos.append(f"La línea '{ln.concepto}' pertenece a una actividad exenta: se emite sin IVA")
            tipo_iva = Decimal("0")
        cuota = r2(ln.cuota_iva) if ln.cuota_iva is not None and tipo_iva else r2(base * tipo_iva / 100)
        if ln.cuota_iva is not None and tipo_iva and abs(r2(base * tipo_iva / 100) - cuota) > Decimal("0.05"):
            avisos.append(f"Cuota IVA de '{ln.concepto}' ({cuota}) no cuadra con {tipo_iva}% de {base}")
        if f.tipo == "recibida":
            cuenta = ln.cuenta or f.cuenta or (habito["cuenta"] if habito else None) or \
                ("2170" if f.bien_inversion else "6290")
        else:
            cuenta = ln.cuenta or f.cuenta or (habito["cuenta"] if habito else None) or "7050"
        if not pgc.cuenta_valida(cuenta):
            raise ValueError(f"Cuenta no válida: {cuenta}")
        # % de IVA deducible: explícito en la línea > sectores diferenciados por línea > régimen de la entidad
        en_prorrata = False
        if ln.pct_deducible is not None:
            pct = ln.pct_deducible
        elif entidad.regimen_iva == "prorrata" and entidad.sectores_diferenciados and lid is not None:
            pct = 0.0 if exenta else 100.0
        else:
            pct = entidad.pct_deduccion_iva
            en_prorrata = entidad.regimen_iva == "prorrata"
        deducible = r2(cuota * D(pct) / 100) if f.tipo == "recibida" else Decimal("0")
        lineas_calc.append(dict(concepto=ln.concepto, base=base, tipo_iva=tipo_iva, cuota=cuota, cuenta=cuenta,
                                linea_id=lid, deducible=deducible, prorrata=en_prorrata and f.tipo == "recibida",
                                exenta=exenta))

    base_total = sum((x["base"] for x in lineas_calc), Decimal("0"))
    cuota_total = sum((x["cuota"] for x in lineas_calc), Decimal("0"))
    deducible_total = sum((x["deducible"] for x in lineas_calc), Decimal("0"))
    retencion = r2(f.retencion_importe) if f.retencion_importe is not None else r2(base_total * D(f.retencion_pct) / 100)
    if f.retencion_tipo == "ninguna" and retencion:
        avisos.append("Hay importe de retención pero retencion_tipo='ninguna'")
    total_factura = base_total + (Decimal("0") if autoliq else cuota_total) - retencion
    if f.total_declarado is not None and abs(r2(f.total_declarado) - total_factura) > Decimal("0.02"):
        avisos.append(f"El total calculado ({total_factura}) difiere del total de la factura ({r2(f.total_declarado)})")

    apuntes: list[Apunte] = []
    if f.tipo == "recibida":
        for x in lineas_calc:
            apuntes.append(Apunte(x["cuenta"], debe=x["base"] + x["cuota"] - x["deducible"], linea_id=x["linea_id"]))
        if deducible_total:
            apuntes.append(Apunte("4720", debe=deducible_total))
        if autoliq and cuota_total:
            apuntes.append(Apunte("4770", haber=cuota_total))
        if retencion:
            apuntes.append(Apunte("4751", haber=retencion))
        primera = lineas_calc[0]["cuenta"]
        cuenta_tercero = "5230" if primera.startswith("2") else ("4000" if primera.startswith("60") else "4100")
        apuntes.append(Apunte(cuenta_tercero, haber=total_factura, tercero_id=tid))
    else:
        for x in lineas_calc:
            apuntes.append(Apunte(x["cuenta"], haber=x["base"], linea_id=x["linea_id"]))
        if cuota_total:
            apuntes.append(Apunte("4770", haber=cuota_total))
        if retencion:
            apuntes.append(Apunte("4730", debe=retencion))
        cuenta_tercero = "4300"
        apuntes.append(Apunte(cuenta_tercero, debe=total_factura, tercero_id=tid))

    if entidad.regimen_iva in ("no_sujeto", "exento") and f.tipo == "emitida" and cuota_total:
        avisos.append(f"{entidad.codigo} está configurada como '{entidad.regimen_iva}' y la factura emitida lleva IVA")

    tipo_operacion = f.tipo_operacion
    if f.tipo == "emitida" and tipo_operacion == "interior" and all(x["exenta"] for x in lineas_calc):
        tipo_operacion = "exenta"
    return dict(tercero_id=tid, lineas=lineas_calc, tipo_operacion=tipo_operacion, base=base_total, cuota=cuota_total, deducible=deducible_total,
                retencion=retencion, total=total_factura, apuntes=apuntes, cuenta_tercero=cuenta_tercero,
                avisos=avisos)


def buscar_emitida(conn, entidad: Entidad, numero: str):
    return conn.execute("SELECT * FROM facturas WHERE entidad_id=? AND tipo='emitida' AND numero=?",
                        (entidad.id, numero)).fetchone()


def eliminar(conn, entidad: Entidad, factura_id: int, commit: bool = True):
    """Borra una factura con su asiento, retenciones y activos. No se permite si tiene cobros o pagos."""
    f = conn.execute("SELECT * FROM facturas WHERE id=? AND entidad_id=?", (factura_id, entidad.id)).fetchone()
    if f is None:
        raise KeyError(f"Factura {factura_id} no encontrada en {entidad.codigo}")
    if conn.execute("SELECT 1 FROM cobros_pagos WHERE factura_id=?", (factura_id,)).fetchone():
        raise ValueError(f"La factura {f['numero']} tiene cobros o pagos registrados: bórralos antes")
    if conn.execute("SELECT 1 FROM activos WHERE factura_id=? AND amortizado > 0", (factura_id,)).fetchone():
        raise ValueError(f"La factura {f['numero']} tiene activos ya amortizados")
    conn.execute("DELETE FROM retenciones WHERE factura_id=?", (factura_id,))
    conn.execute("DELETE FROM activos WHERE factura_id=?", (factura_id,))
    conn.execute("DELETE FROM factura_lineas WHERE factura_id=?", (factura_id,))
    conn.execute("DELETE FROM facturas WHERE id=?", (factura_id,))
    conn.execute("DELETE FROM apuntes WHERE asiento_id=?", (f["asiento_id"],))
    conn.execute("DELETE FROM asientos WHERE id=?", (f["asiento_id"],))
    if commit:
        conn.commit()


def registrar(conn, entidad: Entidad, f: FacturaInput, sustituir: bool = False) -> int:
    """Graba la factura y su asiento. Devuelve el id de la factura. Con sustituir=True, una factura emitida
    con el mismo número (p. ej. una versión corregida) reemplaza a la anterior."""
    if f.tipo == "emitida":
        previa = buscar_emitida(conn, entidad, f.numero)
        if previa is not None:
            if not sustituir:
                raise FacturaDuplicada(f"Ya existe la factura emitida {f.numero} (fecha {previa['fecha']}). "
                                       "Si es una versión corregida, usa la opción de sustituir.")
            eliminar(conn, entidad, previa["id"], commit=False)
    p = preparar(conn, entidad, f)
    concepto = f"Fra. {'emitida' if f.tipo == 'emitida' else 'recibida'} {f.numero} - {f.tercero_nombre}"
    try:
        aid = crear_asiento(conn, entidad, f.fecha, concepto, p["apuntes"], origen="factura", commit=False)
        cur = conn.execute(
            "INSERT INTO facturas (entidad_id, tipo, numero, fecha, fecha_operacion, tercero_id, tipo_operacion,"
            " base, cuota_iva, cuota_deducible, retencion_tipo, retencion_pct, retencion, total, bien_inversion,"
            " cuenta_tercero, concepto, archivo, asiento_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (entidad.id, f.tipo, f.numero, f.fecha, f.fecha_operacion, p["tercero_id"], p["tipo_operacion"],
             to_cents(p["base"]), to_cents(p["cuota"]), to_cents(p["deducible"]), f.retencion_tipo,
             float(f.retencion_pct), to_cents(p["retencion"]), to_cents(p["total"]), int(f.bien_inversion),
             p["cuenta_tercero"], f.concepto or (f.lineas[0].concepto if f.lineas else ""), f.archivo, aid),
        )
    except sqlite3.IntegrityError as e:
        conn.rollback()
        if "UNIQUE" in str(e):
            raise FacturaDuplicada(f"La factura {f.numero} de {f.tercero_nombre} ya está registrada") from e
        raise
    fid = cur.lastrowid
    conn.executemany(
        "INSERT INTO factura_lineas (factura_id, concepto, base, tipo_iva, cuota_iva, cuenta, linea_id, deducible,"
        " prorrata) VALUES (?,?,?,?,?,?,?,?,?)",
        [(fid, x["concepto"], to_cents(x["base"]), float(x["tipo_iva"]), to_cents(x["cuota"]), x["cuenta"],
          x["linea_id"], to_cents(x["deducible"]), int(x["prorrata"])) for x in p["lineas"]],
    )
    # Retenciones practicadas por la entidad (111 / 115)
    if f.tipo == "recibida" and p["retencion"]:
        modelo, clave = {"profesional": ("111", "G"), "actividad": ("111", "H"),
                         "alquiler": ("115", "A")}.get(f.retencion_tipo, ("111", "G"))
        conn.execute(
            "INSERT INTO retenciones (entidad_id, fecha, modelo, clave, perceptor_nif, perceptor_nombre, base,"
            " retencion, factura_id) VALUES (?,?,?,?,?,?,?,?,?)",
            (entidad.id, f.fecha, modelo, clave, f.tercero_nif, f.tercero_nombre, to_cents(p["base"]),
             to_cents(p["retencion"]), fid),
        )
    # Bienes de inversión → registro de activos para amortizar
    if f.bien_inversion:
        for x in p["lineas"]:
            if x["cuenta"][:2] in ("20", "21", "22"):
                vida = f.vida_util or pgc.VIDA_UTIL.get(x["cuenta"][:3], 10)
                conn.execute(
                    "INSERT INTO activos (entidad_id, nombre, cuenta_activo, fecha_alta, coste, vida_util_anios,"
                    " linea_id, factura_id) VALUES (?,?,?,?,?,?,?,?)",
                    (entidad.id, x["concepto"] or f.concepto or f.numero, x["cuenta"], f.fecha_operacion or f.fecha,
                     to_cents(x["base"] + x["cuota"] - x["deducible"]), vida, x["linea_id"], fid),
                )
    # Aprender cuenta y línea habituales del tercero
    conn.execute(
        "INSERT INTO habitos (entidad_id, tercero_id, tipo, cuenta, linea_id) VALUES (?,?,?,?,?)"
        " ON CONFLICT(entidad_id, tercero_id, tipo) DO UPDATE SET cuenta=excluded.cuenta, linea_id=excluded.linea_id",
        (entidad.id, p["tercero_id"], f.tipo, p["lineas"][0]["cuenta"], p["lineas"][0]["linea_id"]),
    )
    conn.commit()
    return fid


def pendiente(conn, factura_id: int) -> int:
    f = conn.execute("SELECT total FROM facturas WHERE id=?", (factura_id,)).fetchone()
    pagado = conn.execute("SELECT COALESCE(SUM(importe),0) FROM cobros_pagos WHERE factura_id=?",
                          (factura_id,)).fetchone()[0]
    return f["total"] - pagado


def registrar_cobro_pago(conn, entidad: Entidad, factura_id: int, fecha: str, importe=None,
                         cuenta_tesoreria: str = "5720") -> int:
    f = conn.execute("SELECT * FROM facturas WHERE id=? AND entidad_id=?", (factura_id, entidad.id)).fetchone()
    if f is None:
        raise KeyError(f"Factura {factura_id} no encontrada en {entidad.codigo}")
    pend = pendiente(conn, factura_id)
    imp = to_cents(importe) if importe is not None else pend
    if imp <= 0 or imp > pend:
        raise ValueError(f"Importe no válido. Pendiente: {from_cents(pend)}")
    imp_d = from_cents(imp)
    if f["tipo"] == "emitida":
        apuntes = [Apunte(cuenta_tesoreria, debe=imp_d), Apunte(f["cuenta_tercero"], haber=imp_d, tercero_id=f["tercero_id"])]
        concepto = f"Cobro fra. {f['numero']}"
    else:
        apuntes = [Apunte(f["cuenta_tercero"], debe=imp_d, tercero_id=f["tercero_id"]), Apunte(cuenta_tesoreria, haber=imp_d)]
        concepto = f"Pago fra. {f['numero']}"
    aid = crear_asiento(conn, entidad, fecha, concepto, apuntes, origen="cobro_pago", commit=False)
    cur = conn.execute("INSERT INTO cobros_pagos (factura_id, fecha, importe, cuenta_tesoreria, asiento_id)"
                       " VALUES (?,?,?,?,?)", (factura_id, fecha, imp, cuenta_tesoreria, aid))
    conn.commit()
    return cur.lastrowid


def listar(conn, entidad: Entidad, desde=None, hasta=None, tipo=None) -> list[sqlite3.Row]:
    sql = ("SELECT f.*, t.nif AS tercero_nif, t.nombre AS tercero_nombre, t.pais AS tercero_pais FROM facturas f"
           " JOIN terceros t ON t.id = f.tercero_id WHERE f.entidad_id=?")
    params: list = [entidad.id]
    if desde:
        sql += " AND f.fecha >= ?"
        params.append(desde)
    if hasta:
        sql += " AND f.fecha <= ?"
        params.append(hasta)
    if tipo:
        sql += " AND f.tipo = ?"
        params.append(tipo)
    return conn.execute(sql + " ORDER BY f.fecha, f.id", params).fetchall()


def pendientes(conn, entidad: Entidad, tipo: str, a_fecha: str) -> list[dict]:
    from datetime import date
    out = []
    for f in listar(conn, entidad, hasta=a_fecha, tipo=tipo):
        pagado = conn.execute("SELECT COALESCE(SUM(importe),0) FROM cobros_pagos WHERE factura_id=? AND fecha<=?",
                              (f["id"], a_fecha)).fetchone()[0]
        p = f["total"] - pagado
        if p > 0:
            out.append(dict(f) | {"pendiente": p,
                                  "dias": (date.fromisoformat(a_fecha) - date.fromisoformat(f["fecha"])).days})
    return out
