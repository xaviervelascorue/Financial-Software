"""Lectura directa (sin IA) de facturas emitidas con la plantilla Excel propia.

Busca las celdas por su etiqueta, no por posición, para tolerar filas o columnas movidas:
  FECHA: <fecha> · Nº DE FACTURA: <número> · Facturar a: <cliente>
  DESCRIPCIÓN ... BASE IMPONIBLE  → líneas hasta la fila del IVA
  IVA (21%) <cuota> · TOTAL <total>
Si el Excel no sigue la plantilla devuelve None y se usa la lectura con IA."""

import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from .facturas import FacturaInput, LineaFactura
from .money import D, r2


class PlantillaError(ValueError):
    pass


def _norm(v) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip().upper()


def _celdas(ws):
    return {(c.row, c.column): c.value for fila in ws.iter_rows() for c in fila if c.value not in (None, "")}


def _buscar(celdas, patron):
    for (r, c), v in sorted(celdas.items()):
        if isinstance(v, str) and re.search(patron, _norm(v)):
            return r, c
    return None


def _a_la_derecha(celdas, pos):
    r, c = pos
    cols = sorted(cc for (rr, cc) in celdas if rr == r and cc > c)
    return celdas[(r, cols[0])] if cols else None


def _fecha(v) -> str:
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    m = re.match(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", str(v or ""))
    if m:
        return date(int(m[3]), int(m[2]), int(m[1])).isoformat()
    raise PlantillaError(f"Fecha no reconocida: {v!r}")


def _fecha_operacion(celdas, pos, fecha_exp: str, avisos: list) -> str | None:
    """Fecha de la operación si la plantilla la indica (celda "FECHA OPERACIÓN:"). Avisa si la factura se
    emite después del día 16 del mes siguiente (plazo con clientes empresarios, art. 11 RD 1619/2012)."""
    if not pos:
        return None
    valor = _a_la_derecha(celdas, pos)
    if valor in (None, ""):
        return None
    fo = _fecha(valor)
    if fo == fecha_exp:
        return None
    y, m = int(fo[:4]), int(fo[5:7])
    limite = date(y + (m == 12), m % 12 + 1, 16).isoformat()
    if fecha_exp > limite:
        avisos.append(f"Factura emitida después del {limite[8:10]}/{limite[5:7]}: el plazo con empresas es el día 16 "
                      "del mes siguiente a la operación")
    return fo


def leer_plantilla(path: str | Path, linea: str | None = None) -> FacturaInput | None:
    from openpyxl import load_workbook
    path = Path(path)
    wb = load_workbook(path, data_only=True, read_only=True)
    ws = next((w for w in wb.worksheets if w.sheet_state == "visible"), wb.worksheets[0])
    celdas = _celdas(ws)
    p_fecha, p_num = _buscar(celdas, r"^FECHA:?$"), _buscar(celdas, r"^N[ºO°]\.? ?DE FACTURA:?$")
    p_oper = _buscar(celdas, r"^FECHA (DE (LA )?)?OPERACI[OÓ]N:?$")
    p_cli, p_desc = _buscar(celdas, r"^FACTURAR A:?$"), _buscar(celdas, r"^DESCRIPCI[OÓ]N$")
    p_iva, p_total = _buscar(celdas, r"^IVA\b"), _buscar(celdas, r"^TOTAL$")
    if not all((p_fecha, p_num, p_cli, p_desc, p_total)):
        return None
    p_base = _buscar(celdas, r"^BASE IMPONIBLE$")
    col_base = p_base[1] if p_base else max(c for (r, c) in celdas if r == p_desc[0])
    fecha = _fecha(_a_la_derecha(celdas, p_fecha))
    numero = _a_la_derecha(celdas, p_num)
    if numero in (None, ""):
        raise PlantillaError("La factura no tiene número")
    numero = str(int(numero)) if isinstance(numero, float) and numero.is_integer() else str(numero).strip()
    if numero.isdigit():  # numeración que reinicia cada año: se antepone el ejercicio para que sea única
        numero = f"{fecha[:4]}-{int(numero):03d}"
    cliente = next((celdas[(r, p_cli[1])] for r in range(p_cli[0] + 1, p_desc[0])
                    if (r, p_cli[1]) in celdas), None)
    if not cliente:
        raise PlantillaError("Falta el cliente en 'Facturar a'")
    fin = (p_iva or p_total)[0]
    tipo_iva = Decimal("21")
    if p_iva:
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*%", str(celdas[p_iva]))
        if m:
            tipo_iva = D(m[1].replace(",", "."))
    lineas, retencion_pct, retencion, exenta = [], Decimal("0"), Decimal("0"), False
    for r in range(p_desc[0] + 1, fin):
        texto = str(celdas.get((r, p_desc[1]), "")).strip()
        valor = celdas.get((r, col_base))
        fila = " ".join(_norm(v) for (rr, cc), v in celdas.items() if rr == r)
        if re.search(r"EXENCI[OÓ]N|EXENT[OA]", fila):
            exenta = True
        if re.match(r"^(IRPF|RETENCI)", _norm(texto)):
            m = re.search(r"(\d+(?:[.,]\d+)?)\s*%", texto)
            retencion_pct = D(m[1].replace(",", ".")) if m else Decimal("0")
            retencion += r2(abs(D(valor or 0)))
        elif isinstance(valor, (int, float)) and valor:
            lineas.append(dict(concepto=texto, base=D(valor)))
    if not lineas:
        raise PlantillaError("La factura no tiene líneas con base imponible")
    total = D(_a_la_derecha(celdas, p_total) or 0)
    cuota_doc = D(_a_la_derecha(celdas, p_iva) or 0) if p_iva else Decimal("0")
    avisos = []
    if exenta:
        tipo_iva = Decimal("0")
    # La plantilla puede calcular la base hacia atrás desde el total (con muchos decimales): se redondea y
    # la cuota se ajusta para que base + IVA - retención = total exacto de la factura.
    bases = [r2(ln["base"]) for ln in lineas]
    cuota_total = r2(total) - sum(bases) + retencion if total else r2(cuota_doc)
    if abs(cuota_total - r2(sum(bases) * tipo_iva / 100)) > Decimal("0.05"):
        avisos.append(f"El IVA de la factura ({cuota_total}) no es el {tipo_iva} % de la base ({sum(bases)})")
    if retencion and abs(retencion - r2(sum(bases) * retencion_pct / 100)) > Decimal("0.05"):
        avisos.append(f"La retención ({retencion}) no es el {retencion_pct} % de la base")
    cuotas, acumulado = [], Decimal("0")
    for i, b in enumerate(bases):
        c = cuota_total - acumulado if i == len(bases) - 1 else r2(b * tipo_iva / 100)
        cuotas.append(c)
        acumulado += c
    nif_re = r"\b([0-9XYZ]\d{7}[A-Z]|[A-Z]\d{7}[0-9A-J])\b"
    cabecera = " ".join(_norm(v) for (r, c), v in celdas.items() if r < p_cli[0])
    if not re.search(nif_re, cabecera):
        avisos.append("La plantilla no muestra el NIF del emisor (dato obligatorio en la factura)")
    # NIF del cliente: si aparece en el bloque "Facturar a"; sin él (clientes varios) no va al 347
    nif_cliente = ""
    for (r, c), v in celdas.items():
        if p_cli[0] < r < p_desc[0]:
            m = re.search(nif_re, _norm(v))
            if m:
                nif_cliente = m[1]
    nombre = re.sub(r"\s*\(?(NIF|CIF)?:?\s*" + nif_re + r"\)?", "", str(cliente), flags=re.I).strip() \
        if nif_cliente else str(cliente).strip()
    varios = re.search(r"VARIOS|PARTICULARES", _norm(nombre)) is not None
    if not nif_cliente and not varios:
        avisos.append(f"Falta el NIF de '{nombre}' en la factura (obligatorio si el cliente es empresa o profesional)")
    return FacturaInput(
        tipo="emitida", numero=numero, fecha=fecha, tercero_nif=nif_cliente, tercero_nombre=nombre,
        fecha_operacion=_fecha_operacion(celdas, p_oper, fecha, avisos),
        lineas=[LineaFactura(base=b, tipo_iva=tipo_iva, concepto=ln["concepto"], cuota_iva=c, linea=linea)
                for ln, b, c in zip(lineas, bases, cuotas)],
        tipo_operacion="exenta" if exenta else "interior",
        retencion_tipo="profesional" if retencion else "ninguna", retencion_pct=retencion_pct,
        retencion_importe=retencion if retencion else None,
        total_declarado=r2(total) if total else None, archivo=str(path), avisos=avisos,
    )
