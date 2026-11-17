"""Borradores de autoliquidaciones y declaraciones informativas.

IMPORTANTE: los resultados son borradores de ayuda a la presentación. Revisa siempre las casillas
en el formulario oficial de la AEAT antes de presentar."""

from ..entidades import Entidad
from ..ledger import Apunte, crear_asiento
from ..money import r2

AVISO = "Borrador generado automáticamente. Revisar antes de presentar en la Sede Electrónica de la AEAT."


def pagar(conn, entidad: Entidad, fecha: str, modelo: str, cuenta_deuda: str, importe,
          cuenta_tesoreria: str = "5720") -> int:
    """Pago de una liquidación: 4750 (IVA), 4751 (111/115/123), 4752 (IS), 4731 (pagos fraccionados 130/202)."""
    imp = r2(importe)
    return crear_asiento(conn, entidad, fecha, f"Pago modelo {modelo}",
                         [Apunte(cuenta_deuda, debe=imp), Apunte(cuenta_tesoreria, haber=imp)], origen="pago_impuesto")
