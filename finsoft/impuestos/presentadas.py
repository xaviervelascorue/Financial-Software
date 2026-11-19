"""Autoliquidaciones presentadas: registro, comparación con el cálculo de FinSoft y uso como dato de partida
(pagos fraccionados de trimestres anteriores, acumulados del 130 de antes de empezar a usar FinSoft)."""

import json

from ..entidades import Entidad
from ..money import to_cents


def registrar(conn, entidad: Entidad, modelo: str, ejercicio: int, periodo: str, casillas: dict, resultado,
              fecha_presentacion: str | None = None, justificante: str | None = None):
    """casillas en euros: {"01": 39313.74, ...}. Reemplaza la presentada del mismo periodo."""
    cas = {str(k).zfill(2) if str(k).isdigit() and len(str(k)) < 2 else str(k): to_cents(v) for k, v in casillas.items()}
    conn.execute(
        "INSERT INTO liquidaciones (entidad_id, modelo, ejercicio, periodo, casillas, resultado, fecha_presentacion,"
        " justificante) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(entidad_id, modelo, ejercicio, periodo) DO UPDATE SET"
        " casillas=excluded.casillas, resultado=excluded.resultado, fecha_presentacion=excluded.fecha_presentacion,"
        " justificante=excluded.justificante",
        (entidad.id, modelo, ejercicio, periodo.upper(), json.dumps(cas), to_cents(resultado), fecha_presentacion,
         justificante))
    conn.commit()


def obtener(conn, entidad: Entidad, modelo: str, ejercicio: int, periodo: str) -> dict | None:
    r = conn.execute("SELECT * FROM liquidaciones WHERE entidad_id=? AND modelo=? AND ejercicio=? AND periodo=?",
                     (entidad.id, modelo, ejercicio, periodo.upper())).fetchone()
    if r is None:
        return None
    return {**dict(r), "casillas": json.loads(r["casillas"])}


def comparar(calculado: dict, presentada: dict, nombres: dict | None = None) -> list[dict]:
    """Filas casilla a casilla: calculado por FinSoft frente a lo presentado (céntimos)."""
    cas_c, cas_p = calculado.get("casillas", {}), presentada["casillas"]
    claves = sorted(set(cas_c) | set(cas_p), key=lambda k: (not k.isdigit(), int(k) if k.isdigit() else 0, k))
    filas = []
    for k in claves:
        if k == "65":  # porcentaje, no importe
            continue
        c, p = cas_c.get(k, 0), cas_p.get(k, 0)
        if c or p:
            filas.append({"casilla": k, "descripcion": (nombres or {}).get(k, ""), "finsoft": c, "presentado": p,
                          "diferencia": c - p, "cuadra": abs(c - p) <= 1})
    return filas
