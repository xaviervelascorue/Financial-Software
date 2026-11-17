"""Libro diario por partida doble: asientos, saldos, mayor y sumas y saldos."""

from collections import defaultdict
from dataclasses import dataclass

from .entidades import Entidad
from .money import to_cents
from .pgc import cuenta_valida, nombre_cuenta


class AsientoDescuadrado(ValueError):
    pass


@dataclass
class Apunte:
    cuenta: str
    debe: object = 0
    haber: object = 0
    linea_id: int | None = None
    tercero_id: int | None = None


def crear_asiento(conn, entidad: Entidad, fecha: str, concepto: str, apuntes: list[Apunte],
                  origen: str = "manual", commit: bool = True) -> int:
    filas = []
    for a in apuntes:
        if not cuenta_valida(a.cuenta):
            raise ValueError(f"Cuenta no válida: {a.cuenta}")
        d, h = to_cents(a.debe), to_cents(a.haber)
        # importes negativos: se pasan al lado contrario
        if d < 0:
            d, h = 0, h - d
        if h < 0:
            d, h = d - h, 0
        if d == 0 and h == 0:
            continue
        filas.append((a.cuenta, d, h, a.linea_id, a.tercero_id))
    total_d = sum(f[1] for f in filas)
    total_h = sum(f[2] for f in filas)
    if not filas:
        raise ValueError("Asiento sin importes")
    if total_d != total_h:
        raise AsientoDescuadrado(f"Asiento descuadrado: debe {total_d/100:.2f} ≠ haber {total_h/100:.2f}")
    ejercicio = int(fecha[:4])
    num = conn.execute(
        "SELECT COALESCE(MAX(numero),0)+1 FROM asientos WHERE entidad_id=? AND ejercicio=?",
        (entidad.id, ejercicio),
    ).fetchone()[0]
    cur = conn.execute(
        "INSERT INTO asientos (entidad_id, ejercicio, numero, fecha, concepto, origen) VALUES (?,?,?,?,?,?)",
        (entidad.id, ejercicio, num, fecha, concepto, origen),
    )
    aid = cur.lastrowid
    conn.executemany(
        "INSERT INTO apuntes (asiento_id, cuenta, debe, haber, linea_id, tercero_id) VALUES (?,?,?,?,?,?)",
        [(aid, *f) for f in filas],
    )
    if commit:
        conn.commit()
    return aid


def _filtro(entidad, desde, hasta, prefijo, excluir_origen):
    sql = " FROM apuntes ap JOIN asientos a ON a.id = ap.asiento_id WHERE a.entidad_id=?"
    params: list = [entidad.id]
    if desde:
        sql += " AND a.fecha >= ?"
        params.append(desde)
    if hasta:
        sql += " AND a.fecha <= ?"
        params.append(hasta)
    if prefijo:
        sql += " AND ap.cuenta LIKE ?"
        params.append(prefijo + "%")
    for o in excluir_origen or ():
        sql += " AND a.origen != ?"
        params.append(o)
    return sql, params


def saldos(conn, entidad: Entidad, desde=None, hasta=None, prefijo=None, excluir_origen=None) -> dict[str, tuple[int, int]]:
    """{cuenta: (debe, haber)} en céntimos."""
    sql, params = _filtro(entidad, desde, hasta, prefijo, excluir_origen)
    rows = conn.execute("SELECT ap.cuenta, SUM(ap.debe), SUM(ap.haber)" + sql + " GROUP BY ap.cuenta", params)
    return {r[0]: (r[1], r[2]) for r in rows}


def saldo(conn, entidad, prefijo, desde=None, hasta=None, excluir_origen=None) -> int:
    """Saldo deudor (debe - haber) de las cuentas que empiezan por `prefijo`."""
    s = saldos(conn, entidad, desde, hasta, prefijo, excluir_origen)
    return sum(d - h for d, h in s.values())


def saldos_por_linea(conn, entidad, desde, hasta, excluir_origen=("regularizacion",)):
    """{(cuenta, linea_id): debe-haber} para grupos 6 y 7."""
    sql, params = _filtro(entidad, desde, hasta, None, excluir_origen)
    sql += " AND (ap.cuenta LIKE '6%' OR ap.cuenta LIKE '7%')"
    rows = conn.execute("SELECT ap.cuenta, ap.linea_id, SUM(ap.debe - ap.haber)" + sql +
                        " GROUP BY ap.cuenta, ap.linea_id", params)
    return {(r[0], r[1]): r[2] for r in rows}


def sumas_y_saldos(conn, entidad, desde, hasta):
    filas = []
    for cuenta, (d, h) in sorted(saldos(conn, entidad, desde, hasta).items()):
        filas.append({"cuenta": cuenta, "nombre": nombre_cuenta(cuenta), "debe": d, "haber": h,
                      "saldo_deudor": max(d - h, 0), "saldo_acreedor": max(h - d, 0)})
    return filas


def diario(conn, entidad, desde, hasta):
    rows = conn.execute(
        "SELECT a.numero, a.fecha, a.concepto, ap.cuenta, ap.debe, ap.haber, t.nombre AS tercero"
        " FROM asientos a JOIN apuntes ap ON ap.asiento_id = a.id LEFT JOIN terceros t ON t.id = ap.tercero_id"
        " WHERE a.entidad_id=? AND a.fecha BETWEEN ? AND ? ORDER BY a.fecha, a.numero, ap.id",
        (entidad.id, desde, hasta),
    ).fetchall()
    return [dict(r) | {"nombre_cuenta": nombre_cuenta(r["cuenta"])} for r in rows]


def mayor(conn, entidad, cuenta, desde, hasta):
    inicial = saldo(conn, entidad, cuenta, None, _dia_anterior(desde))
    rows = conn.execute(
        "SELECT a.fecha, a.numero, a.concepto, ap.cuenta, ap.debe, ap.haber FROM asientos a"
        " JOIN apuntes ap ON ap.asiento_id=a.id WHERE a.entidad_id=? AND ap.cuenta LIKE ?"
        " AND a.fecha BETWEEN ? AND ? ORDER BY a.fecha, a.numero",
        (entidad.id, cuenta + "%", desde, hasta),
    ).fetchall()
    acumulado = inicial
    out = [{"fecha": desde, "numero": None, "concepto": "Saldo inicial", "cuenta": cuenta, "debe": 0,
            "haber": 0, "saldo": inicial}]
    for r in rows:
        acumulado += r["debe"] - r["haber"]
        out.append(dict(r) | {"saldo": acumulado})
    return out


def _dia_anterior(fecha: str) -> str:
    from datetime import date, timedelta
    return (date.fromisoformat(fecha) - timedelta(days=1)).isoformat()


def comprobar_cuadre(conn, entidad) -> bool:
    d, h = conn.execute(
        "SELECT COALESCE(SUM(debe),0), COALESCE(SUM(haber),0) FROM apuntes ap JOIN asientos a"
        " ON a.id=ap.asiento_id WHERE a.entidad_id=?", (entidad.id,)
    ).fetchone()
    return d == h


def agrupar(saldos_dict: dict, digitos: int = 3) -> dict:
    out = defaultdict(int)
    for cuenta, (d, h) in saldos_dict.items():
        out[cuenta[:digitos]] += d - h
    return dict(out)
