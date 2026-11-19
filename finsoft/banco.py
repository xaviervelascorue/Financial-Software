"""Movimientos bancarios: importación, conciliación de cobros con facturas y reparto de ingresos por línea.

Pensado para actividades que facturan un resumen mensual a particulares: los cobros del banco (TPV, Bizum,
transferencias) dicen qué servicios se cobraron y permiten repartir la factura entre líneas de ingreso.

Reglas de clasificación (configurables por línea en entidades.toml):
  palabras = ["terapia", "energética"]   → si el concepto contiene alguna, el cobro es de esa línea
                                           (primero se miran las líneas sin regla de múltiplo)
  multiplo_importe = 33                  → importes múltiplos de esa cifra van a esa línea
  por_defecto = true                     → resto de cobros
Los cargos agrupados de TPV ("VENTAS T.C. ... (OP. n)") se descomponen con los precios observados en
cobros individuales."""

import csv
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from itertools import combinations_with_replacement
from pathlib import Path

from .entidades import Entidad, lineas
from .money import D, r2


@dataclass
class Movimiento:
    fecha: str
    concepto: str
    importe: float
    tipo: str = ""            # tpv | bizum | transferencia | comision_tpv | devolucion | cargo | otro
    operaciones: int = 1
    reparto: dict = field(default_factory=dict)   # línea → importe
    nota: str = ""


def _plano(t: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFD", (t or "").lower()) if unicodedata.category(ch) != "Mn")


def _fecha(v) -> str:
    if isinstance(v, (date,)):
        return v.isoformat()[:10]
    s = str(v).strip()
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        return f"{m[3]}-{int(m[2]):02d}-{int(m[1]):02d}"
    return s[:10]


def _filas_fichero(path: Path) -> list[list]:
    ext = path.suffix.lower()
    if ext == ".xls":
        import xlrd  # formato Excel antiguo, habitual en las descargas de banca online
        sh = xlrd.open_workbook(str(path)).sheet_by_index(0)
        return [[sh.cell_value(r, c) for c in range(sh.ncols)] for r in range(sh.nrows)]
    if ext in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        ws = load_workbook(path, data_only=True, read_only=True).worksheets[0]
        return [list(r) for r in ws.iter_rows(values_only=True)]
    texto = path.read_text(encoding="utf-8-sig", errors="replace")
    sep = ";" if texto.count(";") > texto.count(",") else ","
    return [r for r in csv.reader(texto.splitlines(), delimiter=sep)]


def _num(v) -> float | None:
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("€", "").replace(" ", "")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def leer_movimientos(path: str | Path) -> list[Movimiento]:
    """Lee un extracto (xls, xlsx o csv) con columnas fecha, concepto y uno o dos importes."""
    filas = _filas_fichero(Path(path))
    cab = next((i for i, f in enumerate(filas) if f and _plano(str(f[0])) == "fecha"), None)
    if cab is None:
        raise ValueError("No se encuentra la fila de cabecera con 'fecha' en el extracto")
    cabecera = [_plano(str(x)) for x in filas[cab]]
    # columnas de importe: las que se llaman "importe..." y las columnas sin título tras la fecha valor
    # (algunos bancos ponen cargos y abonos en dos columnas); nunca el saldo ni columnas de notas de texto
    cols = [i for i, h in enumerate(cabecera) if i >= 2 and ("importe" in h or (h == "" and i >= 3))]
    movs = []
    for f in filas[cab + 1:]:
        if not f or not str(f[0]).strip() or not re.match(r"\d", str(f[0]).strip()):
            continue
        vals = [_num(f[i]) for i in cols if i < len(f)]
        imp = sum(v for v in vals if v is not None)
        movs.append(Movimiento(_fecha(f[0]), str(f[1]).strip(), round(imp, 2)))
    return [clasificar_tipo(m) for m in movs]


def clasificar_tipo(m: Movimiento) -> Movimiento:
    c = _plano(m.concepto)
    op = re.search(r"\(op\.\s*(\d+)\)", c)
    if c.startswith("ventas t.c"):
        m.tipo, m.operaciones = "tpv", int(op[1]) if op else 1
    elif c.startswith("comis"):
        m.tipo = "comision_tpv"
    elif "bizum" in c and re.search(r"devoluci|diferencia", c):
        m.tipo = "devolucion"
    elif "bizum" in c and m.importe > 0:
        m.tipo = "bizum"
    elif c.startswith("transf") and m.importe > 0:
        m.tipo = "transferencia"
    elif m.importe < 0:
        m.tipo = "cargo"
    else:
        m.tipo = "otro"
    return m


@dataclass
class Reglas:
    palabras: dict          # línea → [términos]
    multiplos: dict         # línea → múltiplo
    por_defecto: str | None
    excluir: list           # términos de cobros que no son ingresos (préstamos, ayudas...)

    @classmethod
    def de_entidad(cls, conn, entidad: Entidad) -> "Reglas":
        pal, mult, defecto = {}, {}, None
        filas = sorted(lineas(conn, entidad), key=lambda r: (r["multiplo_importe"] is not None, r["codigo"]))
        for r in filas:
            if r["palabras"]:
                pal[r["codigo"]] = [_plano(p) for p in r["palabras"].split("|") if p]
            if r["multiplo_importe"]:
                mult[r["codigo"]] = float(r["multiplo_importe"])
            if r["por_defecto"]:
                defecto = r["codigo"]
        if defecto is None and len(filas) == 1:
            defecto = filas[0]["codigo"]
        return cls(pal, mult, defecto, ["prestado", "comunidad de madri", "hacienda", "seguridad social", "tgss"])


def _es_multiplo(importe: float, m: float) -> bool:
    return importe > 0 and abs(importe / m - round(importe / m)) < 1e-6


def _linea_individual(m: Movimiento, reglas: Reglas) -> str | None:
    c = _plano(m.concepto)
    for linea, terms in reglas.palabras.items():
        if any(t in c for t in terms):
            return linea
    for linea, mult in reglas.multiplos.items():
        if _es_multiplo(abs(m.importe), mult):
            return linea
    return reglas.por_defecto


def _descomponer(importe: float, n: int, mult: float, precios: list[float]):
    """Busca k cobros de `mult` y n-k precios observados que sumen el importe (k mínimo)."""
    for k in range(0, n + 1):
        resto = round(importe - k * mult, 2)
        if resto < -0.005:
            break
        if n - k == 0:
            if abs(resto) < 0.01:
                return k, 0.0
            continue
        for combo in combinations_with_replacement(precios, n - k):
            if abs(sum(combo) - resto) < 0.01:
                return k, resto
    return None


def repartir_cobros(movs: list[Movimiento], reglas: Reglas) -> list[Movimiento]:
    """Asigna cada cobro (o parte de él) a una línea de ingreso."""
    linea_mult, mult = (next(iter(reglas.multiplos.items())) if reglas.multiplos else (None, None))
    precios = sorted({abs(m.importe) for m in movs if m.tipo in ("tpv", "bizum", "transferencia")
                      and m.operaciones == 1 and m.importe > 0 and not (mult and _es_multiplo(m.importe, mult))})
    for m in movs:
        if m.tipo not in ("tpv", "bizum", "transferencia", "devolucion"):
            continue
        if any(t in _plano(m.concepto) for t in reglas.excluir):
            m.tipo, m.nota = "otro", "No es un ingreso de la actividad"
            continue
        if m.tipo == "tpv" and m.operaciones > 1 and mult and not _es_multiplo(m.importe, mult):
            sol = _descomponer(m.importe, m.operaciones, mult, precios)
            if sol:
                k, resto = sol
                m.reparto = {linea_mult: k * mult, reglas.por_defecto: resto} if k else {reglas.por_defecto: m.importe}
                m.nota = f"TPV agrupado: {k} × {mult:g} € + {resto:g} €"
            else:
                m.reparto = {reglas.por_defecto: m.importe}
                m.nota = "TPV agrupado sin descomposición exacta: asignado a la línea por defecto"
            m.reparto = {k: v for k, v in m.reparto.items() if v}
            continue
        m.reparto = {_linea_individual(m, reglas): m.importe}
    return movs


def resumen_mensual(movs: list[Movimiento]) -> dict:
    """{mes: {linea: cobros, '_comisiones': x, '_total': y}}"""
    out = defaultdict(lambda: defaultdict(float))
    for m in movs:
        mes = m.fecha[:7]
        for linea, imp in m.reparto.items():
            out[mes][linea] += imp
            out[mes]["_total"] += imp
        if m.tipo == "comision_tpv":
            out[mes]["_comisiones"] += m.importe
    return {k: {kk: round(vv, 2) for kk, vv in v.items()} for k, v in sorted(out.items())}


def conciliar(conn, entidad: Entidad, movs: list[Movimiento]) -> list[dict]:
    """Cobros del banco frente a facturas emitidas, por mes."""
    res = resumen_mensual(movs)
    filas = []
    for mes, d in res.items():
        fact = conn.execute("SELECT COALESCE(SUM(total),0) FROM facturas WHERE entidad_id=? AND tipo='emitida'"
                            " AND substr(fecha,1,7)=?", (entidad.id, mes)).fetchone()[0] / 100
        filas.append({"mes": mes, "cobros": d.get("_total", 0), "facturado": fact,
                      "diferencia": round(d.get("_total", 0) - fact, 2),
                      **{k: v for k, v in d.items() if not k.startswith("_")}})
    return filas


def repartir_factura(conn, entidad: Entidad, factura_id: int, pesos: dict) -> int:
    """Divide una factura emitida (resumen mensual) en líneas proporcionales a `pesos` (línea → importe
    cobrado), manteniendo base, IVA y total. Devuelve el id de la factura sustituida."""
    from . import facturas as fx
    f = conn.execute("SELECT f.*, t.nif, t.nombre, t.pais FROM facturas f JOIN terceros t ON t.id=f.tercero_id"
                     " WHERE f.id=? AND f.entidad_id=?", (factura_id, entidad.id)).fetchone()
    lns = conn.execute("SELECT * FROM factura_lineas WHERE factura_id=?", (factura_id,)).fetchall()
    if f is None or f["tipo"] != "emitida" or len(lns) != 1:
        raise ValueError("Sólo se reparten facturas emitidas de una línea")
    total_pesos = sum(v for v in pesos.values() if v > 0)
    if total_pesos <= 0:
        raise ValueError("Sin cobros para repartir")
    base, cuota, tipo_iva = D(lns[0]["base"]) / 100, D(lns[0]["cuota_iva"]) / 100, D(str(lns[0]["tipo_iva"]))
    nuevas, acum_b, acum_c = [], D(0), D(0)
    items = [(k, v) for k, v in pesos.items() if v > 0]
    for i, (linea, peso) in enumerate(items):
        ultima = i == len(items) - 1
        b = base - acum_b if ultima else r2(base * D(str(peso)) / D(str(total_pesos)))
        c = cuota - acum_c if ultima else r2(b * tipo_iva / 100)
        acum_b += b
        acum_c += c
        nuevas.append(fx.LineaFactura(base=b, tipo_iva=tipo_iva, cuota_iva=c, cuenta=lns[0]["cuenta"],
                                      concepto=f"{lns[0]['concepto'] or ''} · {linea}".strip(" ·"), linea=linea))
    fi = fx.FacturaInput("emitida", f["numero"], f["fecha"], "" if f["nif"].startswith("SIN-NIF") else f["nif"],
                         f["nombre"], nuevas, tercero_pais=f["pais"], tipo_operacion=f["tipo_operacion"],
                         retencion_tipo=f["retencion_tipo"], retencion_pct=D(str(f["retencion_pct"])),
                         retencion_importe=D(f["retencion"]) / 100 if f["retencion"] else None,
                         fecha_operacion=f["fecha_operacion"], concepto=f["concepto"] or "", archivo=f["archivo"])
    return fx.registrar(conn, entidad, fi, sustituir=True)
