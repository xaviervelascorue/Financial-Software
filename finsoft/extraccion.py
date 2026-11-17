"""Lectura automática de facturas (PDF o imagen) con la API de Claude.

Requiere el paquete `anthropic` y credenciales (variable ANTHROPIC_API_KEY o `ant auth login`).
El resultado es una propuesta que se revisa antes de contabilizar."""

import base64
import mimetypes
from datetime import date
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

from . import pgc
from .entidades import Entidad, lineas, normalizar_nif
from .facturas import FacturaInput, LineaFactura
from .money import D

MODELO = "claude-opus-5-5"
FORMATOS = {".xlsx": "text/plain", ".xlsm": "text/plain", ".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".webp": "image/webp", ".gif": "image/gif"}


class ExtraccionError(RuntimeError):
    pass


class LineaExtraida(BaseModel):
    concepto: str
    base_imponible: float = Field(description="Base imponible de la línea o del tipo de IVA, sin IVA")
    tipo_iva: float = Field(description="Porcentaje de IVA: 21, 10, 4, 0...")
    cuota_iva: float
    cuenta_pgc: str = Field(description="Cuenta PGC de 4 dígitos sugerida para esta línea")


class FacturaExtraida(BaseModel):
    numero: str
    fecha_expedicion: str = Field(description="Formato AAAA-MM-DD")
    fecha_operacion: Optional[str] = Field(default=None, description="AAAA-MM-DD si es distinta de la de expedición")
    emisor_nombre: str
    emisor_nif: str
    emisor_pais: str = Field(description="Código ISO de 2 letras")
    receptor_nombre: str
    receptor_nif: str
    lineas: list[LineaExtraida] = Field(description="Una línea por concepto o, si la factura lo agrupa, por tipo de IVA")
    tipo_operacion: Literal["interior", "intracomunitaria", "importacion", "exportacion", "isp", "exenta", "no_sujeta"]
    retencion_irpf_pct: float = Field(description="0 si no hay retención")
    retencion_irpf_importe: float
    tipo_retencion: Literal["ninguna", "profesional", "alquiler", "actividad"]
    total_factura: float = Field(description="Importe total a pagar que figura en la factura")
    moneda: str
    es_bien_inversion: bool = Field(description="True si es un activo duradero (ordenador, mobiliario, vehículo...)")
    linea_negocio: Optional[str] = Field(default=None, description="Código de la línea de ingreso/negocio más adecuada")
    es_rectificativa: bool
    observaciones: str = Field(description="Dudas o datos ilegibles; cadena vacía si no hay")


def _cuentas_catalogo() -> str:
    return "\n".join(f"{c} {n}" for c, n in pgc.CUENTAS.items() if c[0] in "2" + "67")


def _system(entidad: Entidad, lineas_txt: str) -> str:
    return f"""Eres un contable español experto en el PGC de PYMES y en normativa de IVA e IRPF.
Extraes los datos de una factura para contabilizarla en los libros de la entidad:
- Entidad: {entidad.nombre} (NIF {entidad.nif}), tipo {entidad.tipo}, régimen de IVA {entidad.regimen_iva}.

Reglas:
- Si el emisor es la entidad, es una factura emitida (ingreso): usa cuentas del grupo 7.
  Si no, es recibida (gasto o inversión): usa cuentas del grupo 6, o del grupo 2 si es bien de inversión.
- Importes con punto decimal, sin símbolo de moneda. Rectificativas con importes negativos.
- tipo_operacion: 'intracomunitaria' si el proveedor está en otro país de la UE y no repercute IVA;
  'isp' si la factura indica inversión del sujeto pasivo; 'importacion' para DUA; 'interior' en el resto.
- La retención 'profesional' aplica a servicios de profesionales; 'alquiler' a arrendamientos de inmuebles.
- No inventes datos: si algo no se lee, indícalo en observaciones.

Cuentas disponibles:
{_cuentas_catalogo()}

Líneas de negocio de la entidad (código: nombre):
{lineas_txt or '(ninguna definida)'}
"""


def excel_a_texto(path: Path) -> str:
    """Vuelca las celdas con contenido de un Excel (facturas hechas con plantilla) a texto."""
    from openpyxl import load_workbook
    try:
        wb = load_workbook(path, data_only=True, read_only=True)
    except Exception as e:  # fichero dañado o protegido
        raise ExtraccionError(f"No se puede abrir el Excel {path.name}: {e}") from e
    partes = []
    for ws in wb.worksheets:
        if ws.sheet_state != "visible":
            continue
        lineas_hoja = []
        for fila in ws.iter_rows():
            celdas = [f"{c.coordinate}: {c.value}" for c in fila if c.value not in (None, "")]
            if celdas:
                lineas_hoja.append(" | ".join(celdas))
        if lineas_hoja:
            partes.append(f"## Hoja: {ws.title}\n" + "\n".join(lineas_hoja))
    if not partes:
        raise ExtraccionError(f"El Excel {path.name} está vacío")
    return "\n\n".join(partes)


def _bloque_documento(path: Path) -> dict:
    ext = path.suffix.lower()
    if ext not in FORMATOS:
        raise ExtraccionError(f"Formato no soportado: {ext}. Usa PDF, Excel (xlsx), JPG, PNG, WEBP o GIF.")
    if ext in (".xlsx", ".xlsm"):
        return {"type": "document", "title": path.name,
                "source": {"type": "text", "media_type": "text/plain", "data": excel_a_texto(path)}}
    data = base64.standard_b64encode(path.read_bytes()).decode()
    media = FORMATOS[ext] or mimetypes.guess_type(path.name)[0]
    tipo = "document" if ext == ".pdf" else "image"
    return {"type": tipo, "source": {"type": "base64", "media_type": media, "data": data}}


def extraer(path: str | Path, entidad: Entidad, lineas_txt: str = "", client=None) -> FacturaExtraida:
    import anthropic

    path = Path(path)
    client = client or anthropic.Anthropic()
    try:
        resp = client.beta.messages.parse(
            model=MODELO,
            max_tokens=16000,
            system=_system(entidad, lineas_txt),
            messages=[{"role": "user", "content": [
                _bloque_documento(path),
                {"type": "text", "text": "Extrae los datos de esta factura."},
            ]}],
            output_format=FacturaExtraida,
            output_config={"effort": "medium"},
            # Si el modelo rechaza la petición, la API la reintenta con un modelo alternativo.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.AuthenticationError as e:
        raise ExtraccionError("Credenciales de Anthropic no válidas o ausentes (ANTHROPIC_API_KEY).") from e
    except anthropic.RateLimitError as e:
        raise ExtraccionError("Límite de peticiones de la API alcanzado; vuelve a intentarlo en unos minutos.") from e
    except anthropic.APIStatusError as e:
        raise ExtraccionError(f"Error de la API de Claude ({e.status_code}): {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise ExtraccionError("No se pudo conectar con la API de Claude.") from e
    if resp.stop_reason == "refusal":
        raise ExtraccionError("El modelo no ha procesado el documento. Regístrala manualmente.")
    if resp.stop_reason == "max_tokens" or resp.parsed_output is None:
        raise ExtraccionError("La respuesta del modelo está incompleta. Revisa el documento.")
    return resp.parsed_output


def a_factura_input(fx: FacturaExtraida, entidad: Entidad, archivo: str | None = None,
                    codigos_linea: set[str] | None = None) -> FacturaInput:
    """Convierte la extracción en un FacturaInput, decidiendo si es emitida o recibida por el NIF."""
    nif_ent = normalizar_nif(entidad.nif)
    emitida = normalizar_nif(fx.emisor_nif) == nif_ent
    if not emitida and normalizar_nif(fx.receptor_nif) != nif_ent:
        avisos = [f"El NIF del receptor ({fx.receptor_nif}) no coincide con el de {entidad.codigo} ({entidad.nif})"]
    else:
        avisos = []
    if fx.moneda.upper() not in ("EUR", "€"):
        avisos.append(f"Factura en {fx.moneda}: convierte los importes a euros antes de contabilizar")
    if fx.observaciones:
        avisos.append(f"Observaciones de la lectura: {fx.observaciones}")
    try:
        date.fromisoformat(fx.fecha_expedicion)
    except ValueError:
        raise ExtraccionError(f"Fecha no válida en la factura: {fx.fecha_expedicion}")
    linea = fx.linea_negocio.upper() if fx.linea_negocio else None
    if linea and codigos_linea is not None and linea not in codigos_linea:
        avisos.append(f"Línea de negocio sugerida '{linea}' no existe; se deja sin asignar")
        linea = None
    lineas_in = []
    for ln in fx.lineas:
        cuenta = ln.cuenta_pgc if pgc.cuenta_valida(ln.cuenta_pgc) else None
        if cuenta and emitida and not cuenta.startswith("7"):
            cuenta = None
        if cuenta and not emitida and cuenta[0] not in "26":
            cuenta = None
        lineas_in.append(LineaFactura(base=D(ln.base_imponible), tipo_iva=D(ln.tipo_iva), concepto=ln.concepto,
                                      cuenta=cuenta, linea=linea, cuota_iva=D(ln.cuota_iva)))
    return FacturaInput(
        tipo="emitida" if emitida else "recibida",
        numero=fx.numero,
        fecha=fx.fecha_expedicion,
        fecha_operacion=fx.fecha_operacion,
        tercero_nif=fx.receptor_nif if emitida else fx.emisor_nif,
        tercero_nombre=fx.receptor_nombre if emitida else fx.emisor_nombre,
        tercero_pais="ES" if emitida else (fx.emisor_pais or "ES").upper(),
        lineas=lineas_in,
        tipo_operacion=fx.tipo_operacion,
        retencion_tipo=fx.tipo_retencion if fx.retencion_irpf_importe else "ninguna",
        retencion_pct=D(fx.retencion_irpf_pct),
        retencion_importe=D(fx.retencion_irpf_importe),
        total_declarado=D(fx.total_factura),
        bien_inversion=fx.es_bien_inversion and not emitida,
        archivo=archivo,
        avisos=avisos,
    )


def leer_factura(conn, path: str | Path, entidad: Entidad, client=None) -> FacturaInput:
    ls = lineas(conn, entidad)
    txt = "\n".join(f"{r['codigo']}: {r['nombre']} ({r['tipo']})" for r in ls)
    fx = extraer(path, entidad, txt, client=client)
    return a_factura_input(fx, entidad, archivo=str(path), codigos_linea={r["codigo"] for r in ls})
