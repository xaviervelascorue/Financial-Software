from decimal import Decimal

from finsoft import facturas, operaciones
from finsoft.entidades import crear_entidad
from finsoft.facturas import FacturaInput, LineaFactura
from finsoft.impuestos.estimacion_renta import ESCALA_ESTATAL, ESCALAS_AUTONOMICAS, escala, estimar_renta


def test_escala_estatal():
    assert escala(12450, ESCALA_ESTATAL) == 12450 * 0.095
    assert round(escala(20000, ESCALA_ESTATAL), 2) == round(12450 * 0.095 + 7550 * 0.12, 2)


def test_provision_profesional_inicio_actividad(conn):
    x = crear_entidad(conn, "NV", "Nuevo profesional", "99999998T", "autonomo", anio_inicio_actividad=2026)
    facturas.registrar(conn, x, FacturaInput("emitida", "1", "2026-07-02", "B1", "Empresa",
                                             [LineaFactura(base=Decimal("20000"))], retencion_tipo="profesional",
                                             retencion_pct=Decimal("7")))
    operaciones.movimiento_directo(conn, x, "2026-07-31", "RETA", "6420", 1000)
    e = estimar_renta(conn, x, 2026, 3)
    assert e["rendimiento_neto_actividad"] == 1900000 - 95000          # 5 % difícil justificación
    assert e["reduccion_inicio_actividad"] == round(1805000 * 0.20)
    base = (1805000 - 361000) / 100
    esperado = (escala(base, ESCALA_ESTATAL) - escala(5550, ESCALA_ESTATAL)
                + escala(base, ESCALAS_AUTONOMICAS["madrid"]) - escala(5550, ESCALAS_AUTONOMICAS["madrid"]))
    assert abs(e["cuota_estimada"] - esperado * 100) <= 2
    assert e["retenciones_soportadas"] == 140000 and e["pagos_fraccionados_130"] == 0
    assert e["provision_necesaria"] == e["cuota_estimada"] - 140000


def test_config_actualiza_entidad_existente(conn, tmp_path):
    from finsoft.entidades import cargar_config, get_entidad
    cfg = tmp_path / "c.toml"
    cfg.write_text('[[entidad]]\ncodigo = "AUT1"\nnombre = "Autónomo 1"\nnif = "00000001R"\ntipo = "autonomo"\n'
                   'anio_inicio_actividad = 2026\n', encoding="utf-8")
    cargar_config(conn, cfg)
    assert get_entidad(conn, "AUT1").anio_inicio_actividad == 2026


def test_provisiones_grupo(conn):
    from finsoft import informes
    from finsoft.entidades import get_entidad
    a = get_entidad(conn, "AUT1")
    facturas.registrar(conn, a, FacturaInput("emitida", "1", "2026-07-02", "", "Clientes varios",
                                             [LineaFactura(base=Decimal("3000"))]))
    pv = informes.provisiones_grupo(conn, 2026, 3)
    f = {x["codigo"]: x for x in pv["entidades"]}
    assert f["AUT1"]["trimestral"] >= 63000  # 303: 21 % de 3.000 + 130
    assert f["CB"]["anual"] == 0
    assert pv["total"]["total"] == sum(x["total"] for x in pv["entidades"])
