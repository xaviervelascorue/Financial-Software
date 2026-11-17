"""Alta y consulta de entidades, estructura de grupo, terceros y líneas de ingreso."""

import sqlite3
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .money import to_cents

TIPOS = ("autonomo", "comunidad_bienes", "sociedad")
REGIMENES_IVA = ("general", "prorrata", "no_sujeto", "exento")


def normalizar_nif(nif: str) -> str:
    nif = (nif or "").upper().replace("-", "").replace(" ", "").replace(".", "")
    return nif[2:] if nif.startswith("ES") and len(nif) == 11 else nif


@dataclass
class Entidad:
    id: int
    codigo: str
    nombre: str
    nif: str
    tipo: str
    regimen_iva: str
    prorrata_pct: float
    regimen_irpf: str | None
    tipo_is: float
    es_holding: bool
    es_patrimonial: bool
    modalidad_202: str
    cuota_base_202: int
    cifra_negocios_anterior: int
    bins_pendientes: int
    rend_neto_anterior: int | None
    exento_130: bool

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> "Entidad":
        return cls(
            id=r["id"], codigo=r["codigo"], nombre=r["nombre"], nif=r["nif"], tipo=r["tipo"],
            regimen_iva=r["regimen_iva"], prorrata_pct=r["prorrata_pct"], regimen_irpf=r["regimen_irpf"],
            tipo_is=r["tipo_is"], es_holding=bool(r["es_holding"]), es_patrimonial=bool(r["es_patrimonial"]),
            modalidad_202=r["modalidad_202"], cuota_base_202=r["cuota_base_202"],
            cifra_negocios_anterior=r["cifra_negocios_anterior"], bins_pendientes=r["bins_pendientes"],
            rend_neto_anterior=r["rend_neto_anterior"], exento_130=bool(r["exento_130"]),
        )

    @property
    def pct_deduccion_iva(self) -> float:
        return {"general": 100.0, "prorrata": self.prorrata_pct}.get(self.regimen_iva, 0.0)

    @property
    def presenta_303(self) -> bool:
        return self.regimen_iva in ("general", "prorrata")

    @property
    def es_persona_fisica(self) -> bool:
        return self.tipo == "autonomo"


def crear_entidad(conn, codigo, nombre, nif, tipo, regimen_iva="general", **kw) -> Entidad:
    if tipo not in TIPOS:
        raise ValueError(f"Tipo de entidad no válido: {tipo}. Opciones: {TIPOS}")
    if regimen_iva not in REGIMENES_IVA:
        raise ValueError(f"Régimen de IVA no válido: {regimen_iva}. Opciones: {REGIMENES_IVA}")
    campos = {
        "codigo": codigo.upper(), "nombre": nombre, "nif": normalizar_nif(nif), "tipo": tipo,
        "regimen_iva": regimen_iva,
    }
    if tipo == "autonomo":
        campos["regimen_irpf"] = kw.pop("regimen_irpf", "eds")
    for k in ("cuota_base_202", "cifra_negocios_anterior", "bins_pendientes", "rend_neto_anterior"):
        if k in kw and kw[k] is not None:
            kw[k] = to_cents(kw[k])
    campos.update({k: v for k, v in kw.items() if v is not None})
    cols = ", ".join(campos)
    marks = ", ".join("?" for _ in campos)
    conn.execute(f"INSERT INTO entidades ({cols}) VALUES ({marks})", list(campos.values()))
    conn.commit()
    return get_entidad(conn, codigo)


def get_entidad(conn, codigo_o_id) -> Entidad:
    if isinstance(codigo_o_id, int):
        r = conn.execute("SELECT * FROM entidades WHERE id=?", (codigo_o_id,)).fetchone()
    else:
        r = conn.execute("SELECT * FROM entidades WHERE codigo=?", (str(codigo_o_id).upper(),)).fetchone()
    if r is None:
        raise KeyError(f"Entidad no encontrada: {codigo_o_id}")
    return Entidad.from_row(r)


def listar_entidades(conn) -> list[Entidad]:
    return [Entidad.from_row(r) for r in conn.execute("SELECT * FROM entidades ORDER BY id")]


def entidad_por_nif(conn, nif: str) -> Entidad | None:
    r = conn.execute("SELECT * FROM entidades WHERE nif=?", (normalizar_nif(nif),)).fetchone()
    return Entidad.from_row(r) if r else None


def add_participacion(conn, participada: Entidad, porcentaje, titular: Entidad | None = None,
                      titular_nombre=None, titular_nif=None, coste=0, fecha_adquisicion=None):
    if titular is None and not titular_nombre:
        raise ValueError("Indica un titular (entidad registrada o nombre de persona)")
    conn.execute(
        "INSERT INTO participaciones (participada_id, titular_entidad_id, titular_nombre, titular_nif,"
        " porcentaje, coste, fecha_adquisicion) VALUES (?,?,?,?,?,?,?)",
        (participada.id, titular.id if titular else None,
         titular.nombre if titular else titular_nombre,
         titular.nif if titular else normalizar_nif(titular_nif or ""),
         float(porcentaje), to_cents(coste), fecha_adquisicion),
    )
    conn.commit()


def titulares(conn, participada: Entidad) -> list[sqlite3.Row]:
    """Socios / comuneros de una entidad."""
    return conn.execute(
        "SELECT * FROM participaciones WHERE participada_id=? ORDER BY porcentaje DESC", (participada.id,)
    ).fetchall()


def participadas(conn, titular: Entidad) -> list[sqlite3.Row]:
    """Entidades en las que participa (filiales de la holding, CB de un comunero...)."""
    return conn.execute(
        "SELECT p.*, e.codigo, e.nombre AS participada_nombre, e.tipo AS participada_tipo"
        " FROM participaciones p JOIN entidades e ON e.id = p.participada_id"
        " WHERE p.titular_entidad_id=? ORDER BY e.id", (titular.id,)
    ).fetchall()


# ---------------------------------------------------------------- terceros

def tercero(conn, nif: str, nombre: str, pais: str = "ES") -> int:
    nif_n = normalizar_nif(nif) or f"SIN-NIF-{nombre.upper()[:40]}"
    r = conn.execute("SELECT id FROM terceros WHERE nif=?", (nif_n,)).fetchone()
    if r:
        return r["id"]
    cur = conn.execute("INSERT INTO terceros (nif, nombre, pais) VALUES (?,?,?)", (nif_n, nombre, pais or "ES"))
    return cur.lastrowid


# ---------------------------------------------------------------- líneas

def crear_linea(conn, entidad: Entidad, codigo: str, nombre: str, tipo="actividad", capital_invertido=0) -> int:
    cur = conn.execute(
        "INSERT INTO lineas (entidad_id, codigo, nombre, tipo, capital_invertido) VALUES (?,?,?,?,?)"
        " ON CONFLICT(entidad_id, codigo) DO UPDATE SET nombre=excluded.nombre, tipo=excluded.tipo,"
        " capital_invertido=excluded.capital_invertido",
        (entidad.id, codigo.upper(), nombre, tipo, to_cents(capital_invertido)),
    )
    conn.commit()
    return linea_id(conn, entidad, codigo) or cur.lastrowid


def linea_id(conn, entidad: Entidad, codigo: str | None) -> int | None:
    if not codigo:
        return None
    r = conn.execute("SELECT id FROM lineas WHERE entidad_id=? AND codigo=?", (entidad.id, codigo.upper())).fetchone()
    if r is None:
        raise KeyError(f"Línea '{codigo}' no existe en {entidad.codigo}")
    return r["id"]


def lineas(conn, entidad: Entidad) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM lineas WHERE entidad_id=? ORDER BY codigo", (entidad.id,)).fetchall()


# ---------------------------------------------------------------- configuración

def cargar_config(conn, path: str | Path) -> list[Entidad]:
    """Carga entidades, líneas y participaciones desde un TOML (ver config/entidades.example.toml).
    Es idempotente: las entidades existentes (mismo código) se omiten."""
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    creadas = []
    for e in cfg.get("entidad", []):
        e = dict(e)
        lineas_cfg = e.pop("linea", [])
        try:
            ent = get_entidad(conn, e["codigo"])
        except KeyError:
            ent = crear_entidad(conn, **e)
            creadas.append(ent)
        for ln in lineas_cfg:
            crear_linea(conn, ent, ln["codigo"], ln["nombre"], ln.get("tipo", "actividad"),
                        ln.get("capital_invertido", 0))
    for p in cfg.get("participacion", []):
        part = get_entidad(conn, p["participada"])
        tit = None
        if p.get("titular"):
            tit = get_entidad(conn, p["titular"])
        existe = conn.execute(
            "SELECT 1 FROM participaciones WHERE participada_id=? AND (titular_entidad_id IS ? AND titular_nif IS ?)",
            (part.id, tit.id if tit else None, tit.nif if tit else normalizar_nif(p.get("titular_nif", ""))),
        ).fetchone()
        if not existe:
            add_participacion(conn, part, p["porcentaje"], tit, p.get("titular_nombre"), p.get("titular_nif"),
                              p.get("coste", 0), p.get("fecha_adquisicion"))
    return creadas
