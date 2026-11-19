"""Base de datos SQLite. Todos los importes se guardan en céntimos (INTEGER)."""

import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS entidades (
    id INTEGER PRIMARY KEY,
    codigo TEXT NOT NULL UNIQUE,
    nombre TEXT NOT NULL,
    nif TEXT NOT NULL UNIQUE,
    tipo TEXT NOT NULL CHECK (tipo IN ('autonomo','comunidad_bienes','sociedad')),
    -- general | prorrata | no_sujeto (holding pura) | exento
    regimen_iva TEXT NOT NULL DEFAULT 'general',
    prorrata_pct REAL NOT NULL DEFAULT 100,
    -- autónomos: eds (directa simplificada) | edn (directa normal)
    regimen_irpf TEXT,
    -- sociedades
    tipo_is REAL NOT NULL DEFAULT 25,
    es_holding INTEGER NOT NULL DEFAULT 0,
    es_patrimonial INTEGER NOT NULL DEFAULT 0,
    modalidad_202 TEXT NOT NULL DEFAULT '40.2',
    cuota_base_202 INTEGER NOT NULL DEFAULT 0,
    cifra_negocios_anterior INTEGER NOT NULL DEFAULT 0,
    bins_pendientes INTEGER NOT NULL DEFAULT 0,
    -- autónomos: rendimiento neto del ejercicio anterior (minoración art. 110.3.c RIRPF)
    rend_neto_anterior INTEGER,
    exento_130 INTEGER NOT NULL DEFAULT 0,
    -- CB: 'actividad' (se atribuye al 130 de los comuneros) | 'capital_inmobiliario' (sólo Renta)
    renta_atribuida TEXT NOT NULL DEFAULT 'actividad',
    -- Prorrata: 1 = sectores diferenciados (gastos de líneas sujetas 100 %, exentas 0 %, comunes a prorrata)
    sectores_diferenciados INTEGER NOT NULL DEFAULT 0,
    fecha_alta TEXT,
    epigrafe_iae TEXT,
    notas TEXT
);

-- Titularidad: socios de sociedades y comuneros de CB. El titular puede ser
-- otra entidad registrada (p. ej. un autónomo o la holding) o una persona externa.
CREATE TABLE IF NOT EXISTS participaciones (
    id INTEGER PRIMARY KEY,
    participada_id INTEGER NOT NULL REFERENCES entidades(id),
    titular_entidad_id INTEGER REFERENCES entidades(id),
    titular_nombre TEXT,
    titular_nif TEXT,
    porcentaje REAL NOT NULL,
    coste INTEGER NOT NULL DEFAULT 0,
    fecha_adquisicion TEXT
);

CREATE TABLE IF NOT EXISTS terceros (
    id INTEGER PRIMARY KEY,
    nif TEXT NOT NULL UNIQUE,
    nombre TEXT NOT NULL,
    pais TEXT NOT NULL DEFAULT 'ES'
);

-- Memoria de contabilización por entidad y tercero (aprende de lo ya registrado)
CREATE TABLE IF NOT EXISTS habitos (
    entidad_id INTEGER NOT NULL REFERENCES entidades(id),
    tercero_id INTEGER NOT NULL REFERENCES terceros(id),
    tipo TEXT NOT NULL,
    cuenta TEXT,
    linea_id INTEGER,
    PRIMARY KEY (entidad_id, tercero_id, tipo)
);

-- Líneas de ingreso / negocio / inversión (base de la analítica de rentabilidad)
CREATE TABLE IF NOT EXISTS lineas (
    id INTEGER PRIMARY KEY,
    entidad_id INTEGER NOT NULL REFERENCES entidades(id),
    codigo TEXT NOT NULL,
    nombre TEXT NOT NULL,
    tipo TEXT NOT NULL DEFAULT 'actividad',
    capital_invertido INTEGER NOT NULL DEFAULT 0,
    iva_exento INTEGER NOT NULL DEFAULT 0,
    UNIQUE (entidad_id, codigo)
);

CREATE TABLE IF NOT EXISTS facturas (
    id INTEGER PRIMARY KEY,
    entidad_id INTEGER NOT NULL REFERENCES entidades(id),
    tipo TEXT NOT NULL CHECK (tipo IN ('emitida','recibida')),
    numero TEXT NOT NULL,
    fecha TEXT NOT NULL,
    fecha_operacion TEXT,
    tercero_id INTEGER NOT NULL REFERENCES terceros(id),
    tipo_operacion TEXT NOT NULL DEFAULT 'interior',
    base INTEGER NOT NULL,
    cuota_iva INTEGER NOT NULL,
    cuota_deducible INTEGER NOT NULL DEFAULT 0,
    retencion_tipo TEXT NOT NULL DEFAULT 'ninguna',
    retencion_pct REAL NOT NULL DEFAULT 0,
    retencion INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL,
    bien_inversion INTEGER NOT NULL DEFAULT 0,
    cuenta_tercero TEXT NOT NULL,
    concepto TEXT,
    archivo TEXT,
    asiento_id INTEGER REFERENCES asientos(id),
    creado_en TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (entidad_id, tipo, tercero_id, numero)
);

CREATE TABLE IF NOT EXISTS factura_lineas (
    id INTEGER PRIMARY KEY,
    factura_id INTEGER NOT NULL REFERENCES facturas(id) ON DELETE CASCADE,
    concepto TEXT,
    base INTEGER NOT NULL,
    tipo_iva REAL NOT NULL,
    cuota_iva INTEGER NOT NULL,
    cuenta TEXT NOT NULL,
    linea_id INTEGER REFERENCES lineas(id),
    deducible INTEGER NOT NULL DEFAULT 0,
    prorrata INTEGER NOT NULL DEFAULT 0  -- 1 = cuota común sujeta a prorrata (regularización anual)
);

CREATE TABLE IF NOT EXISTS cobros_pagos (
    id INTEGER PRIMARY KEY,
    factura_id INTEGER NOT NULL REFERENCES facturas(id),
    fecha TEXT NOT NULL,
    importe INTEGER NOT NULL,
    cuenta_tesoreria TEXT NOT NULL DEFAULT '5720',
    asiento_id INTEGER REFERENCES asientos(id)
);

CREATE TABLE IF NOT EXISTS asientos (
    id INTEGER PRIMARY KEY,
    entidad_id INTEGER NOT NULL REFERENCES entidades(id),
    ejercicio INTEGER NOT NULL,
    numero INTEGER NOT NULL,
    fecha TEXT NOT NULL,
    concepto TEXT NOT NULL,
    origen TEXT NOT NULL DEFAULT 'manual',
    UNIQUE (entidad_id, ejercicio, numero)
);

CREATE TABLE IF NOT EXISTS apuntes (
    id INTEGER PRIMARY KEY,
    asiento_id INTEGER NOT NULL REFERENCES asientos(id) ON DELETE CASCADE,
    cuenta TEXT NOT NULL,
    debe INTEGER NOT NULL DEFAULT 0,
    haber INTEGER NOT NULL DEFAULT 0,
    linea_id INTEGER REFERENCES lineas(id),
    tercero_id INTEGER REFERENCES terceros(id)
);
CREATE INDEX IF NOT EXISTS ix_apuntes_cuenta ON apuntes(cuenta);

-- Retenciones practicadas por la entidad (modelos 111, 115, 123)
CREATE TABLE IF NOT EXISTS retenciones (
    id INTEGER PRIMARY KEY,
    entidad_id INTEGER NOT NULL REFERENCES entidades(id),
    fecha TEXT NOT NULL,
    modelo TEXT NOT NULL CHECK (modelo IN ('111','115','123')),
    clave TEXT NOT NULL,
    perceptor_nif TEXT,
    perceptor_nombre TEXT,
    base INTEGER NOT NULL,
    retencion INTEGER NOT NULL,
    factura_id INTEGER REFERENCES facturas(id)
);

-- Ajustes extracontables al resultado para el Impuesto sobre Sociedades
CREATE TABLE IF NOT EXISTS ajustes_fiscales (
    id INTEGER PRIMARY KEY,
    entidad_id INTEGER NOT NULL REFERENCES entidades(id),
    fecha TEXT NOT NULL,
    concepto TEXT NOT NULL,
    importe INTEGER NOT NULL,  -- positivo = aumento de base; negativo = disminución
    referencia TEXT
);

CREATE TABLE IF NOT EXISTS activos (
    id INTEGER PRIMARY KEY,
    entidad_id INTEGER NOT NULL REFERENCES entidades(id),
    nombre TEXT NOT NULL,
    cuenta_activo TEXT NOT NULL,
    fecha_alta TEXT NOT NULL,
    coste INTEGER NOT NULL,
    vida_util_anios REAL NOT NULL,
    valor_residual INTEGER NOT NULL DEFAULT 0,
    amortizado_hasta TEXT,
    amortizado INTEGER NOT NULL DEFAULT 0,
    linea_id INTEGER REFERENCES lineas(id),
    factura_id INTEGER REFERENCES facturas(id)
);
"""


# Columnas añadidas después de la primera versión: se crean en bases de datos existentes.
MIGRACIONES = [
    ("entidades", "renta_atribuida", "TEXT NOT NULL DEFAULT 'actividad'"),
    ("entidades", "sectores_diferenciados", "INTEGER NOT NULL DEFAULT 0"),
    ("lineas", "iva_exento", "INTEGER NOT NULL DEFAULT 0"),
    ("factura_lineas", "deducible", "INTEGER NOT NULL DEFAULT 0"),
    ("factura_lineas", "prorrata", "INTEGER NOT NULL DEFAULT 0"),
    ("entidades", "referencia_catastral", "TEXT"),
    ("entidades", "valor_catastral", "INTEGER"),
    ("entidades", "valor_catastral_construccion", "INTEGER"),
    ("entidades", "catastral_revisado", "INTEGER NOT NULL DEFAULT 1"),
    ("lineas", "palabras", "TEXT"),
    ("entidades", "anio_inicio_actividad", "INTEGER"),
    ("entidades", "comunidad_autonoma", "TEXT NOT NULL DEFAULT 'madrid'"),
    ("entidades", "otras_rentas", "INTEGER NOT NULL DEFAULT 0"),
    ("entidades", "otras_retenciones", "INTEGER NOT NULL DEFAULT 0"),
    ("entidades", "pct_superficie_afecta", "REAL NOT NULL DEFAULT 0"),
    ("entidades", "asegurados_salud", "INTEGER NOT NULL DEFAULT 1"),
    ("lineas", "multiplo_importe", "REAL"),
    ("lineas", "por_defecto", "INTEGER NOT NULL DEFAULT 0"),
]


def connect(path: str | Path = "finsoft.db") -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for tabla, columna, ddl in MIGRACIONES:
        if columna not in {r["name"] for r in conn.execute(f"PRAGMA table_info({tabla})")}:
            conn.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna} {ddl}")
    conn.commit()
    return conn
