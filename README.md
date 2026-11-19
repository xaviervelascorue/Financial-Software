# FinSoft · Contabilidad multientidad

Contabilidad por partida doble (PGC PYMES) para un grupo formado por **autónomos**, **comunidades de bienes**
y **sociedades** (holding y futuras filiales). Las facturas se contabilizan solas a partir del PDF o la foto,
y cada trimestre se generan estados financieros, informes de gestión y los borradores fiscales.

> Los modelos fiscales son **borradores de ayuda**. Revisa siempre las casillas en la Sede Electrónica de la
> AEAT antes de presentar.

## Qué hace

| Área | Contenido |
|---|---|
| Facturas | Lectura con IA (PDF, Excel, JPG/PNG), alta desde JSON, contabilización automática, control de duplicados, memoria de cuenta y línea por proveedor, cobros y pagos |
| Contabilidad | Diario, mayor, sumas y saldos, amortizaciones, regularización y cierre |
| Estados | Balance y PyG abreviados por trimestre, comparados con el trimestre anterior y con el año anterior |
| Gestión | KPIs, ratios, tesorería, pendientes de cobro y pago, alertas, top clientes y proveedores, gastos por naturaleza |
| Rentabilidad | Por línea de ingreso: margen, reparto de gastos de estructura y rentabilidad anualizada sobre el capital invertido |
| IVA | 303 (con compensaciones), 390 y libros registro de expedidas y recibidas; prorrata y holding no sujeta |
| IRPF | 130 (con la parte de la CB de cada comunero y gastos de difícil justificación), 111, 115, 123 y datos para la Renta |
| Sociedades | 202 (art. 40.2 y 40.3), estimación del IS con exención del art. 21 LIS y BINs, contabilización del gasto por impuesto |
| Informativas | 347 y 184 (atribución de rentas de la CB) |
| Grupo | Resumen agregado de todas las entidades y estructura de participaciones |

## Instalación

```bash
pip install -e ".[dev]"
cp config/entidades.example.toml config/entidades.toml   # edita con los datos reales
finsoft init --config config/entidades.toml
```

La base de datos se crea en `finsoft.db`; puedes cambiarla con `--db` o con la variable `FINSOFT_DB`.
Los datos reales (`*.db`, `config/entidades.toml`, `informes/`) están excluidos de git.

Para leer facturas con IA hace falta una clave de la API de Claude en `ANTHROPIC_API_KEY`.

## Uso habitual

```bash
# Facturas: una a una, o una carpeta entera (las contabilizadas pasan a procesadas/)
finsoft factura leer --entidad AUT1 factura.pdf
finsoft bandeja --entidad AUT1 bandeja/AUT1 --si      # --si: contabiliza sola si no hay avisos
finsoft factura alta --entidad AUT1 ejemplos/facturas.json
finsoft factura lista --entidad AUT1 --desde 2026-01-01
finsoft factura cobro-pago --entidad AUT1 12 --fecha 2026-02-15

# Saldos iniciales (CSV cuenta;saldo, deudor +, acreedor -) y asientos manuales en JSON
finsoft apertura --entidad AUT1 --fecha 2026-10-01 saldos.csv
finsoft asiento --entidad AUT1 asiento.json

# Extracto bancario: concilia cobros con facturas y reparte la factura resumen mensual por línea de ingreso
finsoft banco --entidad LRB extracto.xls --repartir

# Gastos sin factura (cuota de autónomos, comisiones, IBI...)
finsoft movimiento --entidad AUT1 --fecha 2026-01-31 --cuenta 6420 --importe 300 --concepto "Cuota RETA"

# Holding
finsoft inversion compra --entidad HOLD --fecha 2026-01-10 --cuenta 5400 --importe 10000 --concepto "ETF" --linea CARTERA
finsoft inversion dividendo --entidad HOLD --fecha 2026-03-15 --bruto 400 --concepto "Dividendo ETF" --linea CARTERA
finsoft inversion dividendo --entidad HOLD --fecha 2026-06-01 --bruto 5000 --retencion 0 --grupo --exento-art21 --concepto "Dividendo filial"
finsoft reparto-dividendo --entidad HOLD --fecha 2026-06-30 --importe 2000

# Cierre trimestral: un Excel por entidad + resumen de grupo en informes/AAAA/nT/
finsoft trimestre 2026 1
finsoft liquidar-iva --entidad AUT1 2026 1
finsoft pagar --entidad AUT1 --modelo 303 --importe 155.40 --fecha 2026-04-20

# Consultas rápidas
finsoft pyg --entidad AUT1 2026 1
finsoft balance --entidad HOLD --fecha 2026-03-31
finsoft rentabilidad --entidad HOLD 2026 1

# Anual: 390, 347, 184, IS / Renta. Con --cerrar contabiliza el IS y regulariza
finsoft anual 2026 --dias-alquilado 210 --dias-uso-propio 15

# Tu parte de una CB de vivienda turística para la Renta (ingresos, gastos por tipo, amortización, imputación)
finsoft renta-cb --entidad XVR 2026 --dias-alquilado 210 --dias-uso-propio 15
```

## Contenido del Excel trimestral

Resumen fiscal · 303 · 130 / 202 · 111 / 115 / 123 (si hay retenciones) · Atribución CB · Libros registro de IVA ·
Balance · PyG · Sumas y saldos · Diario · Informe de gestión · Rentabilidad por línea · Gastos por naturaleza ·
Top clientes y proveedores.

## Criterios contables y fiscales aplicados

- **Holding pura (`regimen_iva = "no_sujeto"`)**: no presenta 303 y el IVA soportado se suma al gasto. Si factura
  servicios a sus filiales, pasa a `general` o `prorrata`.
- **Dividendos y plusvalías exentas (art. 21 LIS)**: se registra un ajuste negativo del 95 % (el 5 % restante
  tributa como gastos de gestión).
- **Holding**: los dividendos de empresas del grupo (7600) y los intereses de créditos (7620) forman parte de la
  cifra de negocios (consulta ICAC, BOICAC 79).
- **Prorrata y sectores diferenciados**: las líneas de ingreso pueden marcarse como exentas de IVA (p. ej.
  docencia). Con `sectores_diferenciados`, el IVA de los gastos asignados a líneas sujetas se deduce al 100 %,
  el de líneas exentas al 0 % y el de gastos comunes a la prorrata provisional; en el 4T se regulariza con la
  prorrata definitiva (casilla 44, asiento a 6390/6340).
- **Vivienda de uso turístico**: sin servicios de hostelería es una CB exenta de IVA cuya renta es capital
  inmobiliario (`renta_atribuida = "capital_inmobiliario"`, no entra en el 130); con ellos, IVA del 10 % y actividad.
- **Comunidad de bienes**: presenta 184 (y 303 si está sujeta). Sus rendimientos y retenciones se atribuyen a cada comunero según
  su porcentaje y se suman a su 130.
- **Autónomos**: la contabilidad se lleva por partida doble con cuenta del titular (5500); los libros registro
  de IVA se generan a partir de ella.
- **Bienes de inversión**: alta automática en el registro de activos y amortización lineal diaria con las vidas
  útiles de `pgc.VIDA_UTIL`.
- **Verifactu**: FinSoft registra facturas recibidas y emitidas por otros programas, pero no emite facturas, por lo
  que no es un sistema de facturación sujeto a Verifactu.

## Estructura

```
finsoft/
  db.py            esquema SQLite (importes en céntimos)
  pgc.py           cuentas PGC y mapeo a balance / PyG
  entidades.py     entidades, participaciones, terceros, líneas, carga de configuración
  ledger.py        asientos, saldos, diario, mayor
  facturas.py      registro y contabilización de facturas, cobros y pagos
  operaciones.py   inversiones, dividendos, gastos directos, amortizaciones
  extraccion.py    lectura de facturas con la API de Claude
  estados.py       balance, PyG y regularización
  informes.py      informe de gestión, rentabilidad por línea, vista de grupo
  impuestos/       iva.py (303, 390), irpf.py (130, 111, 115, 123, 184), sociedades.py (202, IS), informativas.py (347)
  cierre.py        paquetes trimestral, anual y de grupo en Excel
  cli.py           línea de comandos
tests/             pruebas de asientos, impuestos, estados y extracción
```

## Pruebas

```bash
pytest
```
