"""Emisión de facturas simplificadas (tiques) y rectificativas a partir de los cobros del banco.

Pensado para servicios a particulares (art. 4 del Reglamento de facturación, RD 1619/2012): un tique por
cobro, emitido el mismo día (o el siguiente) en que se recibe el pago. FinSoft no fecha tiques hacia atrás:
la fecha de expedición es siempre la del día en que se generan; la fecha de la operación es la del cobro.

Contenido de cada tique (art. 7): serie y número correlativos, fecha de expedición, fecha de operación si es
distinta, NIF, nombre y domicilio del emisor, descripción del servicio, tipo de IVA con la mención
"IVA incluido" y el importe total. Las devoluciones generan una rectificativa (R5) que identifica el tique
rectificado.

Hasta que Verifactu sea obligatorio este sistema es válido como cualquier programa de facturación; antes de
esa fecha habrá que usar un sistema homologado o adaptar FinSoft (registro encadenado, QR y envío a la AEAT)."""

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from . import banco, facturas
from .entidades import Entidad
from .money import D, from_cents, r2

LIMITE_SIMPLIFICADA = Decimal("400")       # € IVA incluido (art. 4.1.a RD 1619/2012)
TERCERO = "Cliente particular (factura simplificada)"


def referencia(m: banco.Movimiento) -> str:
    """Identificador estable de un cobro bancario, para no emitir dos tiques por el mismo pago."""
    return hashlib.sha1(f"{m.fecha}|{m.concepto}|{m.importe:.2f}".encode()).hexdigest()[:16]


def _siguiente_numero(conn, entidad: Entidad, serie: str, anio: int) -> str:
    pref = f"{serie}{anio}-"
    r = conn.execute("SELECT numero FROM facturas WHERE entidad_id=? AND tipo='emitida' AND numero LIKE ?"
                     " ORDER BY numero DESC LIMIT 1", (entidad.id, pref + "%")).fetchone()
    n = int(r["numero"].split("-")[-1]) + 1 if r else 1
    return f"{pref}{n:05d}"


def emitir(conn, entidad: Entidad, fecha_operacion: str, total, descripcion: str, linea: str | None = None,
           tipo_iva=Decimal("21"), referencia_cobro: str | None = None, fecha_expedicion: str | None = None,
           rectifica_id: int | None = None) -> dict:
    """Emite un tique (o una rectificativa si total < 0). Devuelve sus datos, con avisos."""
    total = r2(total)
    hoy = fecha_expedicion or date.today().isoformat()
    if hoy < fecha_operacion:
        raise ValueError("La fecha de expedición no puede ser anterior a la de la operación")
    avisos = []
    if abs(total) > LIMITE_SIMPLIFICADA:
        avisos.append(f"Importe {total} € superior a 400 €: corresponde factura completa con los datos del cliente")
    if (date.fromisoformat(hoy) - date.fromisoformat(fecha_operacion)).days > 1:
        avisos.append("Tique emitido más de un día después del cobro: con particulares debe emitirse al cobrar")
    rect = total < 0
    serie = "R" if rect else "T"
    numero = _siguiente_numero(conn, entidad, serie, int(hoy[:4]))
    base = r2(total / (1 + D(tipo_iva) / 100))
    cuota = total - base
    fi = facturas.FacturaInput(
        "emitida", numero, hoy, "", TERCERO,
        [facturas.LineaFactura(base=base, tipo_iva=D(tipo_iva), cuota_iva=cuota, concepto=descripcion, linea=linea)],
        fecha_operacion=fecha_operacion if fecha_operacion != hoy else None, total_declarado=total,
        concepto=descripcion)
    fid = facturas.registrar(conn, entidad, fi)
    conn.execute("UPDATE facturas SET tipo_factura=?, rectifica_id=?, referencia_cobro=?, fecha_hora_expedicion=?"
                 " WHERE id=?", ("R5" if rect else "F2", rectifica_id, referencia_cobro,
                                datetime.now().isoformat(timespec="seconds"), fid))
    conn.commit()
    return {"id": fid, "numero": numero, "fecha_expedicion": hoy, "fecha_operacion": fecha_operacion,
            "total": total, "base": base, "cuota": cuota, "linea": linea, "descripcion": descripcion,
            "rectifica_id": rectifica_id, "avisos": avisos}


def _buscar_original(conn, entidad: Entidad, importe: Decimal, antes_de: str) -> int | None:
    """Tique más reciente del mismo importe, para enlazar una devolución."""
    r = conn.execute("SELECT id FROM facturas WHERE entidad_id=? AND tipo_factura='F2' AND total=? AND fecha<=?"
                     " AND id NOT IN (SELECT rectifica_id FROM facturas WHERE rectifica_id IS NOT NULL)"
                     " ORDER BY fecha DESC, id DESC LIMIT 1", (entidad.id, int(abs(importe) * 100), antes_de)).fetchone()
    return r["id"] if r else None


DESCRIPCION = {"TERAPIA": "Sesión de terapia energética", "ASTROMED": "Servicio de astromedicina"}


def desde_banco(conn, entidad: Entidad, movs: list[banco.Movimiento], fecha_expedicion: str | None = None,
                simular: bool = False) -> dict:
    """Genera los tiques de los cobros del extracto que aún no tienen tique. Los cobros con tarjeta agrupados
    (varias operaciones en un apunte) sólo se emiten si se pueden descomponer; si no, quedan pendientes."""
    movs = banco.repartir_cobros(movs, banco.Reglas.de_entidad(conn, entidad))
    hechos = {r[0] for r in conn.execute("SELECT referencia_cobro FROM facturas WHERE entidad_id=? AND"
                                         " referencia_cobro IS NOT NULL", (entidad.id,))}
    emitidos, pendientes = [], []
    for m in movs:
        if m.tipo not in ("tpv", "bizum", "transferencia", "devolucion") or not m.reparto:
            continue
        ref = referencia(m)
        if ref in hechos:
            continue
        partes = []
        if m.tipo == "tpv" and m.operaciones > 1:
            mult = next(iter(banco.Reglas.de_entidad(conn, entidad).multiplos.values()), None)
            terapia = m.reparto.get("TERAPIA", 0)
            resto_ops = m.operaciones - (int(round(terapia / mult)) if mult and terapia else 0)
            partes = [("TERAPIA", mult)] * (int(round(terapia / mult)) if mult and terapia else 0)
            resto = {k: v for k, v in m.reparto.items() if k != "TERAPIA"}
            if resto_ops == 1 and len(resto) == 1:
                partes += list(resto.items())
            elif resto_ops > 0:
                pendientes.append({"fecha": m.fecha, "concepto": m.concepto, "importe": m.importe,
                                   "motivo": "TPV agrupado: falta el detalle de cada operación"})
                continue
        else:
            partes = list(m.reparto.items())
        for i, (linea, imp) in enumerate(partes):
            datos = {"fecha_operacion": m.fecha, "total": r2(imp), "linea": linea,
                     "descripcion": DESCRIPCION.get(linea, "Servicio profesional"), "referencia": ref,
                     "medio": m.tipo}
            if simular:
                emitidos.append(datos | {"numero": "(simulación)", "avisos": []})
                continue
            rect = None
            if imp < 0:
                rect = _buscar_original(conn, entidad, D(imp), m.fecha)
                datos["descripcion"] = "Devolución " + datos["descripcion"].lower()
            t = emitir(conn, entidad, m.fecha, imp, datos["descripcion"], linea,
                       referencia_cobro=ref if i == 0 else f"{ref}-{i}", fecha_expedicion=fecha_expedicion,
                       rectifica_id=rect)
            if imp < 0 and rect is None:
                t["avisos"].append("Devolución sin tique original localizado: revisar")
            emitidos.append(t | {"medio": m.tipo})
    return {"emitidos": emitidos, "pendientes": pendientes}


def texto_tique(conn, entidad: Entidad, factura_id: int) -> str:
    """Tique en texto plano con el contenido obligatorio (para imprimir, PDF o enviar por mensaje)."""
    f = conn.execute("SELECT * FROM facturas WHERE id=?", (factura_id,)).fetchone()
    ln = conn.execute("SELECT * FROM factura_lineas WHERE factura_id=?", (factura_id,)).fetchone()
    lineas = [
        "FACTURA SIMPLIFICADA" + (" RECTIFICATIVA" if f["tipo_factura"] == "R5" else ""),
        f"{entidad.nombre} · NIF {entidad.nif}",
        entidad.domicilio or "(domicilio pendiente de configurar)",
        f"Nº {f['numero']} · Fecha de expedición {f['fecha'][8:10]}/{f['fecha'][5:7]}/{f['fecha'][:4]}",
    ]
    if f["fecha_operacion"]:
        fo = f["fecha_operacion"]
        lineas.append(f"Fecha de la operación {fo[8:10]}/{fo[5:7]}/{fo[:4]}")
    if f["rectifica_id"]:
        o = conn.execute("SELECT numero, fecha FROM facturas WHERE id=?", (f["rectifica_id"],)).fetchone()
        lineas.append(f"Rectifica la factura simplificada {o['numero']} de {o['fecha']}")
    lineas += [
        f"{ln['concepto']}",
        f"Total {from_cents(f['total'])} € (IVA {ln['tipo_iva']:g} % incluido: {from_cents(f['cuota_iva'])} €)",
    ]
    return "\n".join(lineas)


def guardar_registro(emitidos: list[dict], ruta: str | Path):
    Path(ruta).parent.mkdir(parents=True, exist_ok=True)
    Path(ruta).write_text(json.dumps(emitidos, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
