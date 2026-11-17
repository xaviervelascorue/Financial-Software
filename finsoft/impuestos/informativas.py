"""Declaración anual de operaciones con terceras personas (modelo 347)."""

from collections import defaultdict

from ..entidades import Entidad
from ..fechas import trimestre_de
from . import AVISO

UMBRAL_347 = 300506  # 3.005,06 € en céntimos


def modelo_347(conn, entidad: Entidad, ejercicio: int) -> dict:
    """Operaciones interiores con un mismo tercero que superen 3.005,06 € (IVA incluido) en el año.
    Excluye operaciones con retención (se declaran en 190/180), intracomunitarias (349) y de comercio exterior."""
    rows = conn.execute(
        "SELECT f.tipo, f.fecha, f.base, f.cuota_iva, f.tipo_operacion, f.retencion, t.nif, t.nombre, t.pais"
        " FROM facturas f JOIN terceros t ON t.id=f.tercero_id WHERE f.entidad_id=? AND f.fecha BETWEEN ? AND ?",
        (entidad.id, f"{ejercicio}-01-01", f"{ejercicio}-12-31"),
    ).fetchall()
    acum = defaultdict(lambda: {"total": 0, "T1": 0, "T2": 0, "T3": 0, "T4": 0})
    nombres = {}
    for r in rows:
        # sin NIF = consumidores finales agrupados en una factura resumen: no se declaran
        if r["tipo_operacion"] not in ("interior", "isp") or r["retencion"] or r["pais"] != "ES" \
                or r["nif"].startswith("SIN-NIF"):
            continue
        clave = "B" if r["tipo"] == "emitida" else "A"
        imp = r["base"] + (r["cuota_iva"] if r["tipo_operacion"] == "interior" else 0)
        k = (r["nif"], clave)
        acum[k]["total"] += imp
        acum[k][f"T{trimestre_de(r['fecha'])}"] += imp
        nombres[r["nif"]] = r["nombre"]
    declarados = [
        {"nif": nif, "nombre": nombres[nif], "clave": clave,
         "descripcion": "Ventas / prestaciones (B)" if clave == "B" else "Compras / adquisiciones (A)", **v}
        for (nif, clave), v in sorted(acum.items()) if v["total"] > UMBRAL_347
    ]
    return {"modelo": "347", "entidad": entidad.codigo, "ejercicio": ejercicio, "declarados": declarados,
            "presentar": bool(declarados), "aviso": AVISO}
