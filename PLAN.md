# Plan de trabajo — Software de contabilidad multientidad

> Estado: **fase 1 implementada** (17/11/2026): núcleo contable, lectura de facturas con IA, estados
> trimestrales, informes de gestión y borradores fiscales. Ver README.md. Pendiente: cargar los datos reales
> de las entidades (apartado final).

## Alcance inicial (fase 1)

Cubrir las cuatro entidades actuales y dejar la estructura preparada para crecer:

| Entidad | Tipo | Fiscalidad principal |
|---|---|---|
| Autónomo 1 | Persona física, estimación directa | IVA (303/390), IRPF pago fraccionado (130), retenciones (111/115), 347 |
| Autónomo 2 | Persona física, estimación directa | Ídem |
| Comunidad de bienes | Atribución de rentas | IVA (303/390), 184 informativa; reparto a comuneros para su 130 |
| Holding | Sociedad sin actividad económica, con inversiones | IS (202/200), exención art. 21 LIS dividendos/plusvalías, 123/111/115, IVA según actividad (holding pura = no sujeta, IVA soportado como gasto) |

Futuras filiales colgarán de la holding (tabla de participaciones con %, coste y fecha).

## Funcionalidades

1. **Alta de facturas automatizada**: PDF/imagen → extracción con la API de Claude (salida estructurada) → propuesta de asiento → revisión → contabilización. Memoria por proveedor (cuenta y línea de ingreso habituales). Importación masiva por JSON/CSV.
2. **Contabilidad por partida doble** según PGC PYMES (también para autónomos, con libros registro de ingresos, gastos y bienes de inversión), cobros/pagos y amortizaciones.
3. **Estados financieros trimestrales**: balance y cuenta de PyG abreviados, sumas y saldos, diario y mayor. En la holding, los dividendos del grupo cuentan como cifra de negocios (criterio ICAC).
4. **Rentabilidad por tipo de ingreso** (línea de negocio / inversión): ingresos, costes directos, margen, reparto de gastos generales y ROI sobre el capital invertido.
5. **Informe de gestión**: KPIs del trimestre frente al año anterior, tesorería, cobros y pagos pendientes, ratios y previsión de impuestos.
6. **Reporting a Hacienda** (borradores para revisar antes de presentar): 303, 390, 130 (incluida la parte atribuida de la CB), 111, 115, 123, 202, 347, 184 y estimación del IS al cierre.
7. **Vista de grupo**: resumen agregado de todas las entidades.

## Decisiones técnicas propuestas

- Python 3 + SQLite (importes en céntimos enteros), CLI con `click` y salida en Excel (`openpyxl`).
- Extracción de facturas con el SDK `anthropic` (`client.beta.messages.parse` + modelo Pydantic, `fallbacks="default"`), opcional mediante `ANTHROPIC_API_KEY`.
- Tests con `pytest` sobre los cálculos contables y fiscales.
- Más adelante: interfaz web, importación de extractos bancarios (Norma 43), conciliación bancaria,
  consolidación formal del grupo (eliminaciones intragrupo), modelo 349, nóminas y afectación parcial
  de gastos de autónomos en IRPF (vehículo, suministros de vivienda).

## Pendiente de confirmar el lunes

- Datos de cada entidad: NIF, epígrafe IAE, régimen de IVA, % de la CB por comunero y si la holding presta servicios a filiales (lo que cambia el IVA).
- Formato en que se entregarán las facturas (PDF, fotos, carpeta compartida…).
- Si se prefiere CLI + Excel o una interfaz web desde el principio.
