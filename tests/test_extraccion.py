from types import SimpleNamespace

import pytest

from finsoft import facturas
from finsoft.extraccion import ExtraccionError, FacturaExtraida, LineaExtraida, a_factura_input, leer_factura


def extraida(**kw):
    base = dict(numero="F-1", fecha_expedicion="2026-03-02", emisor_nombre="Proveedor SL", emisor_nif="ESB12345678",
                emisor_pais="ES", receptor_nombre="Autónomo 1", receptor_nif="00000001R",
                lineas=[LineaExtraida(concepto="Asesoría", base_imponible=100, tipo_iva=21, cuota_iva=21, cuenta_pgc="6230")],
                tipo_operacion="interior", retencion_irpf_pct=15, retencion_irpf_importe=15, tipo_retencion="profesional",
                total_factura=106, moneda="EUR", es_bien_inversion=False, linea_negocio=None, es_rectificativa=False,
                observaciones="")
    base.update(kw)
    return FacturaExtraida(**base)


class FakeClient:
    def __init__(self, parsed, stop_reason="end_turn"):
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))
        self.parsed, self.stop_reason, self.kwargs = parsed, stop_reason, None

    def _parse(self, **kw):
        self.kwargs = kw
        return SimpleNamespace(parsed_output=self.parsed, stop_reason=self.stop_reason)


def test_recibida_por_nif(aut1):
    fi = a_factura_input(extraida(), aut1)
    assert fi.tipo == "recibida" and fi.tercero_nif == "ESB12345678"
    assert fi.lineas[0].cuenta == "6230" and fi.retencion_tipo == "profesional"
    assert not fi.avisos


def test_emitida_por_nif_y_cuenta_incoherente(aut1):
    fx = extraida(emisor_nif="00000001R", emisor_nombre="Autónomo 1", receptor_nif="B999", receptor_nombre="Cliente",
                  lineas=[LineaExtraida(concepto="Servicio", base_imponible=100, tipo_iva=21, cuota_iva=21,
                                        cuenta_pgc="6230")])
    fi = a_factura_input(fx, aut1)
    assert fi.tipo == "emitida" and fi.tercero_nombre == "Cliente"
    assert fi.lineas[0].cuenta is None  # una cuenta de gasto no vale para un ingreso


def test_aviso_receptor_distinto(aut1):
    fi = a_factura_input(extraida(receptor_nif="B00000000"), aut1)
    assert any("no coincide" in a for a in fi.avisos)


def test_leer_factura_y_registrar(conn, aut1, tmp_path):
    pdf = tmp_path / "f.pdf"
    pdf.write_bytes(b"%PDF-1.4 prueba")
    client = FakeClient(extraida())
    fi = leer_factura(conn, pdf, aut1, client=client)
    assert client.kwargs["model"] == "claude-opus-5-5"
    assert client.kwargs["messages"][0]["content"][0]["type"] == "document"
    p = facturas.preparar(conn, aut1, fi)
    assert not p["avisos"]
    assert facturas.registrar(conn, aut1, fi)


def test_rechazo(conn, aut1, tmp_path):
    img = tmp_path / "f.jpg"
    img.write_bytes(b"\xff\xd8\xff")
    with pytest.raises(ExtraccionError):
        leer_factura(conn, img, aut1, client=FakeClient(None, stop_reason="refusal"))


def test_formato_no_soportado(conn, aut1, tmp_path):
    f = tmp_path / "f.docx"
    f.write_bytes(b"x")
    with pytest.raises(ExtraccionError):
        leer_factura(conn, f, aut1, client=FakeClient(extraida()))


def test_factura_excel(conn, aut1, tmp_path):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws["A1"], ws["B3"], ws["C10"] = "FACTURA", "Nº 2026/015", 1210
    ruta = tmp_path / "fra.xlsx"
    wb.save(ruta)
    client = FakeClient(extraida())
    leer_factura(conn, ruta, aut1, client=client)
    bloque = client.kwargs["messages"][0]["content"][0]
    assert bloque["source"]["type"] == "text"
    assert "B3: Nº 2026/015" in bloque["source"]["data"] and "C10: 1210" in bloque["source"]["data"]
