from datetime import datetime
from decimal import Decimal

from openpyxl import Workbook

from finsoft import facturas
from finsoft.extraccion import leer_factura
from finsoft.impuestos import informativas, iva
from finsoft.plantillas import leer_plantilla


def plantilla(ruta, total=3112.07, nif_emisor="00000001R", cliente="CLIENTES VARIOS", extra=None):
    wb = Workbook()
    ws = wb.active
    ws.title = "FACTURA"
    ws["B2"], ws["C2"] = "Profesional de ejemplo", "FACTURA"
    ws["B4"], ws["C4"], ws["D4"] = f"NIF {nif_emisor}" if nif_emisor else "NIF", "FECHA:", datetime(2026, 7, 31)
    ws["C6"], ws["D6"] = "Nº DE FACTURA:", 12
    ws["B10"], ws["B12"] = "Facturar a:", cliente
    if extra:
        ws["B13"] = extra
    ws["B14"], ws["D14"] = "DESCRIPCIÓN", "BASE IMPONIBLE"
    ws["B16"], ws["C16"], ws["D16"] = "Prestación servicios a clientes varios", datetime(2026, 7, 31), total / 1.21
    ws["C24"], ws["D24"] = "IVA (21%)", total - total / 1.21
    ws["C25"], ws["D25"] = "TOTAL", total
    wb.save(ruta)
    return ruta


def test_lee_plantilla_y_cuadra_total(tmp_path):
    fi = leer_plantilla(plantilla(tmp_path / "f.xlsx"), linea="SERV")
    assert fi.tipo == "emitida" and fi.numero == "2026-012" and fi.fecha == "2026-07-31"
    ln = fi.lineas[0]
    assert ln.base == Decimal("2571.96") and ln.cuota_iva == Decimal("540.11") and ln.tipo_iva == 21
    assert ln.base + ln.cuota_iva == Decimal("3112.07") and not fi.avisos


def test_aviso_sin_nif_emisor_y_nif_cliente(tmp_path):
    fi = leer_plantilla(plantilla(tmp_path / "f.xlsx", nif_emisor="", cliente="Ana Pérez", extra="NIF 12345678Z"))
    assert any("NIF del emisor" in a for a in fi.avisos)
    assert fi.tercero_nif == "12345678Z"


def test_plantilla_sin_ia_y_fuera_del_347(conn, aut1, tmp_path):
    fi = leer_factura(conn, plantilla(tmp_path / "f.xlsx", total=4000), aut1, client=object())  # no llama a la API
    facturas.registrar(conn, aut1, fi)
    assert iva.modelo_303(conn, aut1, 2026, 3)["casillas"]["07"] == 330579
    assert informativas.modelo_347(conn, aut1, 2026)["declarados"] == []
