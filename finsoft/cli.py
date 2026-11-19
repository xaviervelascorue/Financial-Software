"""Interfaz de línea de comandos: `finsoft --help`."""

import json
import shutil
from datetime import date
from pathlib import Path

import click

from . import cierre, db, entidades as ent, facturas, informes, operaciones
from .estados import balance_filas, pyg_filas
from .extraccion import FORMATOS, ExtraccionError, leer_factura
from .fechas import trimestre
from .impuestos import iva, pagar
from .money import fmt, from_cents


def _tabla(filas: list[dict], columnas: list[tuple[str, str]], moneda=()):
    if not filas:
        click.echo("  (sin datos)")
        return
    cel = [[(fmt(from_cents(f.get(k))) if k in moneda and f.get(k) is not None else str(f.get(k, "") if f.get(k) is not None else ""))
            for k, _ in columnas] for f in filas]
    anchos = [max(len(t), *(len(c[i]) for c in cel)) for i, (_, t) in enumerate(columnas)]
    click.echo("  ".join(t.ljust(a) for (_, t), a in zip(columnas, anchos)))
    click.echo("  ".join("-" * a for a in anchos))
    for c in cel:
        click.echo("  ".join(v.rjust(a) if columnas[i][0] in moneda else v.ljust(a) for i, (v, a) in enumerate(zip(c, anchos))))


@click.group()
@click.option("--db", "db_path", envvar="FINSOFT_DB", default="finsoft.db", show_default=True,
              help="Fichero de base de datos (o variable FINSOFT_DB)")
@click.pass_context
def main(ctx, db_path):
    """FinSoft · Contabilidad multientidad para autónomos, comunidades de bienes y sociedades."""
    ctx.obj = db.connect(db_path)


def _entidad(ctx, codigo):
    try:
        return ent.get_entidad(ctx.obj, codigo)
    except KeyError as e:
        raise click.ClickException(str(e))


# ------------------------------------------------------------------ entidades
@main.command()
@click.option("--config", type=click.Path(exists=True), required=True, help="TOML con entidades, líneas y participaciones")
@click.pass_context
def init(ctx, config):
    """Crea la base de datos y carga las entidades desde el fichero de configuración."""
    creadas = ent.cargar_config(ctx.obj, config)
    click.echo(f"Entidades creadas: {', '.join(e.codigo for e in creadas) or 'ninguna (ya existían)'}")


@main.command("entidades")
@click.pass_context
def listar_entidades(ctx):
    """Lista las entidades, sus líneas de ingreso y su estructura de participación."""
    conn = ctx.obj
    for e in ent.listar_entidades(conn):
        click.echo(f"\n[{e.codigo}] {e.nombre} · NIF {e.nif} · {e.tipo} · IVA {e.regimen_iva}"
                   + (f" · IRPF {e.regimen_irpf}" if e.regimen_irpf else "") + (" · holding" if e.es_holding else ""))
        for ln in ent.lineas(conn, e):
            click.echo(f"    línea {ln['codigo']}: {ln['nombre']} ({ln['tipo']})")
        for t in ent.titulares(conn, e):
            click.echo(f"    titular: {t['titular_nombre']} {t['porcentaje']} %")


@main.command("linea")
@click.option("--entidad", required=True)
@click.argument("codigo")
@click.argument("nombre")
@click.option("--tipo", default="actividad", type=click.Choice(["actividad", "alquiler", "inversion_financiera",
                                                                 "participacion", "otro"]))
@click.option("--capital", default=0.0, help="Capital invertido (para rentabilidad)")
@click.pass_context
def nueva_linea(ctx, entidad, codigo, nombre, tipo, capital):
    """Crea o actualiza una línea de ingreso / negocio / inversión."""
    ent.crear_linea(ctx.obj, _entidad(ctx, entidad), codigo, nombre, tipo, capital)
    click.echo(f"Línea {codigo.upper()} guardada")


# ------------------------------------------------------------------ facturas
def _mostrar_propuesta(conn, e, fi):
    p = facturas.preparar(conn, e, fi)
    conn.rollback()  # preparar puede dar de alta el tercero: no se guarda hasta confirmar
    click.echo(f"\n{fi.tipo.upper()} {fi.numero} · {fi.fecha} · {fi.tercero_nombre} ({fi.tercero_nif}) · {fi.tipo_operacion}")
    click.echo(f"  Base {fmt(p['base'])} · IVA {fmt(p['cuota'])} (deducible {fmt(p['deducible'])}) · "
               f"Retención {fmt(p['retencion'])} · Total {fmt(p['total'])}")
    click.echo("  Asiento propuesto:")
    for a in p["apuntes"]:
        from .pgc import nombre_cuenta
        click.echo(f"    {a.cuenta} {nombre_cuenta(a.cuenta)[:45]:45} {fmt(a.debe):>12} {fmt(a.haber):>12}")
    for av in p["avisos"]:
        click.secho(f"  ⚠ {av}", fg="yellow")
    return p


@main.group()
def factura():
    """Alta, lectura automática y cobro/pago de facturas."""


@factura.command("alta")
@click.option("--entidad", required=True)
@click.argument("fichero", type=click.Path(exists=True))
@click.option("--si", is_flag=True, help="No pedir confirmación")
@click.pass_context
def factura_alta(ctx, entidad, fichero, si):
    """Registra facturas desde un JSON (un objeto o una lista). Ver ejemplos/facturas.json."""
    conn, e = ctx.obj, _entidad(ctx, entidad)
    datos = json.loads(Path(fichero).read_text(encoding="utf-8"))
    for d in datos if isinstance(datos, list) else [datos]:
        fi = facturas.FacturaInput.from_dict(d)
        _mostrar_propuesta(conn, e, fi)
        if si or click.confirm("  ¿Contabilizar?", default=True):
            try:
                fid = facturas.registrar(conn, e, fi)
                click.secho(f"  ✔ Registrada (id {fid})", fg="green")
            except facturas.FacturaDuplicada as ex:
                click.secho(f"  ✖ {ex}", fg="red")


def _leer_y_registrar(conn, e, path: Path, si: bool, sustituir: bool = False) -> bool:
    try:
        fi = leer_factura(conn, path, e)
    except ExtraccionError as ex:
        click.secho(f"  ✖ {path.name}: {ex}", fg="red")
        return False
    try:
        p = _mostrar_propuesta(conn, e, fi)
    except (ValueError, KeyError) as ex:
        click.secho(f"  ✖ {path.name}: {ex}", fg="red")
        return False
    if si and p["avisos"]:
        click.secho("  → Tiene avisos: se deja para revisión manual", fg="yellow")
        return False
    if si or click.confirm("  ¿Contabilizar?", default=True):
        try:
            fid = facturas.registrar(conn, e, fi, sustituir=sustituir)
            click.secho(f"  ✔ Registrada (id {fid})", fg="green")
            return True
        except facturas.FacturaDuplicada as ex:
            click.secho(f"  ✖ {ex}", fg="red")
    return False


@factura.command("leer")
@click.option("--entidad", required=True)
@click.argument("fichero", type=click.Path(exists=True))
@click.option("--si", is_flag=True, help="Contabilizar sin confirmar si no hay avisos")
@click.option("--sustituir", is_flag=True, help="Reemplaza la factura emitida con el mismo número (versión corregida)")
@click.pass_context
def factura_leer(ctx, entidad, fichero, si, sustituir):
    """Lee una factura (Excel de plantilla, PDF o imagen) y propone el asiento."""
    _leer_y_registrar(ctx.obj, _entidad(ctx, entidad), Path(fichero), si, sustituir)


@factura.command("borrar")
@click.option("--entidad", required=True)
@click.argument("factura_id", type=int)
@click.pass_context
def factura_borrar(ctx, entidad, factura_id):
    """Borra una factura y su asiento (sólo si no tiene cobros ni pagos)."""
    try:
        facturas.eliminar(ctx.obj, _entidad(ctx, entidad), factura_id)
    except (KeyError, ValueError) as ex:
        raise click.ClickException(str(ex))
    click.echo("Factura borrada")


@main.command()
@click.option("--entidad", required=True)
@click.argument("carpeta", type=click.Path(exists=True, file_okay=False))
@click.option("--si", is_flag=True, help="Contabilizar automáticamente las facturas sin avisos")
@click.pass_context
def bandeja(ctx, entidad, carpeta, si):
    """Procesa todas las facturas de una carpeta. Las contabilizadas pasan a 'procesadas/'."""
    conn, e = ctx.obj, _entidad(ctx, entidad)
    carpeta = Path(carpeta)
    hechas = carpeta / "procesadas"
    ficheros = sorted(p for p in carpeta.iterdir() if p.is_file() and p.suffix.lower() in FORMATOS)
    ok = 0
    for f in ficheros:
        click.echo(f"\n→ {f.name}")
        if _leer_y_registrar(conn, e, f, si):
            hechas.mkdir(exist_ok=True)
            shutil.move(str(f), hechas / f.name)
            ok += 1
    click.echo(f"\n{ok}/{len(ficheros)} facturas contabilizadas")


@factura.command("lista")
@click.option("--entidad", required=True)
@click.option("--desde")
@click.option("--hasta")
@click.option("--tipo", type=click.Choice(["emitida", "recibida"]))
@click.pass_context
def factura_lista(ctx, entidad, desde, hasta, tipo):
    """Lista facturas registradas con su importe pendiente."""
    conn, e = ctx.obj, _entidad(ctx, entidad)
    filas = [dict(f) | {"pendiente": facturas.pendiente(conn, f["id"])} for f in facturas.listar(conn, e, desde, hasta, tipo)]
    _tabla(filas, [("id", "Id"), ("fecha", "Fecha"), ("tipo", "Tipo"), ("numero", "Número"), ("tercero_nombre", "Tercero"),
                   ("base", "Base"), ("cuota_iva", "IVA"), ("retencion", "Ret."), ("total", "Total"), ("pendiente", "Pendiente")],
           moneda={"base", "cuota_iva", "retencion", "total", "pendiente"})


@factura.command("cobro-pago")
@click.option("--entidad", required=True)
@click.argument("factura_id", type=int)
@click.option("--fecha", default=lambda: date.today().isoformat())
@click.option("--importe", type=float, help="Por defecto, todo lo pendiente")
@click.option("--cuenta", default="5720", show_default=True)
@click.pass_context
def factura_cobro(ctx, entidad, factura_id, fecha, importe, cuenta):
    """Registra el cobro (emitida) o pago (recibida) de una factura."""
    try:
        facturas.registrar_cobro_pago(ctx.obj, _entidad(ctx, entidad), factura_id, fecha, importe, cuenta)
    except (KeyError, ValueError) as ex:
        raise click.ClickException(str(ex))
    click.echo("Registrado")


# ------------------------------------------------------------------ otras operaciones
@main.command()
@click.option("--entidad", required=True)
@click.option("--fecha", required=True)
@click.option("--cuenta", required=True, help="Cuenta de gasto (6xxx) o ingreso (7xxx)")
@click.option("--importe", required=True, type=float)
@click.option("--concepto", required=True)
@click.option("--linea")
@click.option("--contrapartida", default="5720", show_default=True)
@click.pass_context
def movimiento(ctx, entidad, fecha, cuenta, importe, concepto, linea, contrapartida):
    """Gasto o ingreso sin factura (cuota de autónomos, comisiones, tributos...)."""
    operaciones.movimiento_directo(ctx.obj, _entidad(ctx, entidad), fecha, concepto, cuenta, importe, linea, contrapartida)
    click.echo("Registrado")


@main.group()
def inversion():
    """Operaciones de inversión (holding)."""


@inversion.command("compra")
@click.option("--entidad", required=True)
@click.option("--fecha", required=True)
@click.option("--cuenta", required=True, help="2403 participaciones grupo, 2500/5400 cartera, 2210 inmuebles...")
@click.option("--importe", required=True, type=float)
@click.option("--concepto", required=True)
@click.option("--linea")
@click.pass_context
def inv_compra(ctx, entidad, fecha, cuenta, importe, concepto, linea):
    operaciones.compra_inversion(ctx.obj, _entidad(ctx, entidad), fecha, concepto, cuenta, importe, linea)
    click.echo("Registrado")


@inversion.command("venta")
@click.option("--entidad", required=True)
@click.option("--fecha", required=True)
@click.option("--cuenta", required=True)
@click.option("--coste", required=True, type=float)
@click.option("--precio", required=True, type=float)
@click.option("--concepto", required=True)
@click.option("--linea")
@click.option("--exento-art21", is_flag=True, help="Plusvalía exenta (participación ≥5 % durante ≥1 año)")
@click.option("--retencion", default=0.0, type=float)
@click.pass_context
def inv_venta(ctx, entidad, fecha, cuenta, coste, precio, concepto, linea, exento_art21, retencion):
    operaciones.venta_inversion(ctx.obj, _entidad(ctx, entidad), fecha, concepto, cuenta, coste, precio, linea,
                                exento_art21, retencion)
    click.echo("Registrado")


@inversion.command("dividendo")
@click.option("--entidad", required=True)
@click.option("--fecha", required=True)
@click.option("--bruto", required=True, type=float)
@click.option("--concepto", required=True)
@click.option("--retencion", default=19.0, show_default=True, help="% de retención")
@click.option("--linea")
@click.option("--grupo", is_flag=True, help="Dividendo de empresa del grupo")
@click.option("--exento-art21", is_flag=True)
@click.pass_context
def inv_dividendo(ctx, entidad, fecha, bruto, concepto, retencion, linea, grupo, exento_art21):
    operaciones.dividendo_recibido(ctx.obj, _entidad(ctx, entidad), fecha, concepto, bruto, retencion, linea, grupo,
                                   exento_art21)
    click.echo("Registrado")


@inversion.command("interes")
@click.option("--entidad", required=True)
@click.option("--fecha", required=True)
@click.option("--bruto", required=True, type=float)
@click.option("--concepto", required=True)
@click.option("--retencion", default=19.0, show_default=True)
@click.option("--linea")
@click.pass_context
def inv_interes(ctx, entidad, fecha, bruto, concepto, retencion, linea):
    operaciones.interes_recibido(ctx.obj, _entidad(ctx, entidad), fecha, concepto, bruto, retencion, linea)
    click.echo("Registrado")


@main.command("reparto-dividendo")
@click.option("--entidad", required=True)
@click.option("--fecha", required=True)
@click.option("--importe", required=True, type=float)
@click.option("--cuenta-cargo", default="1130", show_default=True, help="Reservas (1130) o resultado (1290)")
@click.pass_context
def reparto(ctx, entidad, fecha, importe, cuenta_cargo):
    """Distribuye dividendos a los socios (retención 19 % a personas físicas, modelo 123)."""
    operaciones.distribuir_dividendo(ctx.obj, _entidad(ctx, entidad), fecha, importe, cuenta_cargo)
    click.echo("Registrado")


@main.command("asiento")
@click.option("--entidad", required=True)
@click.argument("fichero", type=click.Path(exists=True))
@click.pass_context
def cmd_asiento(ctx, entidad, fichero):
    """Asiento manual desde JSON: {"fecha", "concepto", "apuntes": [{"cuenta", "debe", "haber", "linea"}]}."""
    from .ledger import Apunte, AsientoDescuadrado, crear_asiento
    conn, e = ctx.obj, _entidad(ctx, entidad)
    d = json.loads(Path(fichero).read_text(encoding="utf-8"))
    ap = [Apunte(a["cuenta"], a.get("debe", 0), a.get("haber", 0), ent.linea_id(conn, e, a.get("linea")))
          for a in d["apuntes"]]
    try:
        aid = crear_asiento(conn, e, d["fecha"], d["concepto"], ap, origen=d.get("origen", "manual"))
    except (AsientoDescuadrado, ValueError) as ex:
        raise click.ClickException(str(ex))
    click.echo(f"Asiento {aid} registrado")


@main.command("apertura")
@click.option("--entidad", required=True)
@click.option("--fecha", required=True, help="Fecha del asiento de apertura, p. ej. 2026-10-01")
@click.argument("fichero", type=click.Path(exists=True))
@click.pass_context
def cmd_apertura(ctx, entidad, fecha, fichero):
    """Saldos iniciales desde CSV con columnas cuenta;saldo (saldo deudor positivo, acreedor negativo)."""
    import csv
    from .ledger import Apunte, AsientoDescuadrado, crear_asiento
    conn, e = ctx.obj, _entidad(ctx, entidad)
    texto = Path(fichero).read_text(encoding="utf-8-sig")
    sep = ";" if texto.count(";") >= texto.count(",") else ","
    ap = []
    for fila in csv.DictReader(texto.splitlines(), delimiter=sep):
        importe = (fila.get("saldo") or "0").replace(".", "").replace(",", ".") if sep == ";" else fila.get("saldo", "0")
        v = float(importe)
        ap.append(Apunte(fila["cuenta"].strip(), debe=v if v > 0 else 0, haber=-v if v < 0 else 0))
    try:
        aid = crear_asiento(conn, e, fecha, "Asiento de apertura", ap, origen="apertura")
    except (AsientoDescuadrado, ValueError) as ex:
        raise click.ClickException(str(ex))
    click.echo(f"Apertura registrada (asiento {aid})")


# ------------------------------------------------------------------ cierres e informes
@main.command("trimestre")
@click.argument("ejercicio", type=int)
@click.argument("t", type=click.IntRange(1, 4))
@click.option("--entidad", help="Sólo una entidad (por defecto, todas + resumen de grupo)")
@click.option("--salida", default="informes", show_default=True)
@click.pass_context
def cmd_trimestre(ctx, ejercicio, t, entidad, salida):
    """Genera el paquete trimestral: estados financieros, impuestos e informes de gestión (Excel)."""
    conn = ctx.obj
    if entidad:
        rutas = [cierre.paquete_trimestral(conn, _entidad(ctx, entidad), ejercicio, t, salida)]
    else:
        rutas = cierre.cerrar_trimestre_todas(conn, ejercicio, t, salida)
    for r in rutas:
        click.echo(f"✔ {r}")


@main.command("anual")
@click.argument("ejercicio", type=int)
@click.option("--entidad")
@click.option("--cerrar", is_flag=True, help="Contabiliza el IS y regulariza el ejercicio (irreversible)")
@click.option("--salida", default="informes", show_default=True)
@click.option("--dias-alquilado", type=int, help="Vivienda turística: días alquilados en el año")
@click.option("--dias-uso-propio", type=int, default=0, help="Vivienda turística: días de uso propio")
@click.pass_context
def cmd_anual(ctx, ejercicio, entidad, cerrar, salida, dias_alquilado, dias_uso_propio):
    """Declaraciones anuales (390, 347, 184, IS) y, opcionalmente, cierre del ejercicio."""
    conn = ctx.obj
    lista = [_entidad(ctx, entidad)] if entidad else ent.listar_entidades(conn)
    if cerrar and not click.confirm(f"Se cerrará el ejercicio {ejercicio} de {', '.join(e.codigo for e in lista)}. ¿Continuar?"):
        return
    for e in lista:
        click.echo(f"✔ {cierre.paquete_anual(conn, e, ejercicio, salida, cerrar, dias_alquilado, dias_uso_propio)}")


@main.command("provision-renta")
@click.option("--entidad", required=True)
@click.argument("ejercicio", type=int)
@click.argument("t", type=click.IntRange(1, 4), default=4)
@click.pass_context
def cmd_provision_renta(ctx, entidad, ejercicio, t):
    """Estimación de la Renta del año y provisión necesaria con los datos hasta el trimestre indicado."""
    from .impuestos import estimacion_renta
    e = _entidad(ctx, entidad)
    if e.tipo != "autonomo":
        raise click.ClickException("Sólo para autónomos")
    est = estimacion_renta.estimar_renta(ctx.obj, e, ejercicio, t)
    click.echo(f"\nRenta {ejercicio} · {e.nombre} · datos hasta {est['datos_hasta']}")
    _tabla(estimacion_renta.filas(est), [("concepto", "Concepto"), ("importe", "Importe")], {"importe"})
    click.echo(f"Tipo medio estimado: {est['tipo_medio_pct']} %")
    for a in est["avisos"]:
        click.secho(f"  ⚠ {a}", fg="yellow")


@main.command("banco")
@click.option("--entidad", required=True)
@click.argument("extracto", type=click.Path(exists=True))
@click.option("--repartir", is_flag=True, help="Reparte las facturas resumen del periodo entre líneas según los cobros")
@click.pass_context
def cmd_banco(ctx, entidad, extracto, repartir):
    """Concilia los cobros del extracto bancario con las facturas emitidas y los reparte por línea de ingreso."""
    from . import banco
    conn, e = ctx.obj, _entidad(ctx, entidad)
    movs = banco.repartir_cobros(banco.leer_movimientos(extracto), banco.Reglas.de_entidad(conn, e))
    filas = banco.conciliar(conn, e, movs)
    lineas_cod = sorted({k for f in filas for k in f if k not in ("mes", "cobros", "facturado", "diferencia")})
    a_cent = [{k: (round(v * 100) if isinstance(v, float) else v) for k, v in f.items()} for f in filas]
    _tabla(a_cent, [("mes", "Mes"), ("cobros", "Cobros banco"), ("facturado", "Facturado"), ("diferencia", "Diferencia")]
           + [(l, l) for l in lineas_cod], {"cobros", "facturado", "diferencia", *lineas_cod})
    dudosos = [m for m in movs if "sin descomposición" in m.nota]
    if dudosos:
        click.secho(f"  ⚠ {len(dudosos)} cobros con tarjeta agrupados sin descomposición exacta (asignados por defecto)",
                    fg="yellow")
    if repartir:
        res = banco.resumen_mensual(movs)
        for f in facturas.listar(conn, e, tipo="emitida"):
            mes = f["fecha"][:7]
            pesos = {k: v for k, v in res.get(mes, {}).items() if not k.startswith("_")}
            n = conn.execute("SELECT COUNT(*) FROM factura_lineas WHERE factura_id=?", (f["id"],)).fetchone()[0]
            if pesos and n == 1 and f["tercero_nif"].startswith("SIN-NIF"):
                banco.repartir_factura(conn, e, f["id"], pesos)
                click.echo(f"  ✔ Factura {f['numero']} repartida: " + ", ".join(f"{k} {v:,.2f}" for k, v in pesos.items()))


@main.command("presentada")
@click.option("--entidad", required=True)
@click.option("--modelo", required=True, type=click.Choice(["303", "130", "111", "115", "123", "202"]))
@click.argument("ejercicio", type=int)
@click.argument("periodo")
@click.option("--casilla", "-c", multiple=True, help="Casilla=importe, p. ej. -c 01=39313.74 -c 02=8316.07")
@click.option("--resultado", type=float, required=True)
@click.option("--fecha")
@click.option("--justificante")
@click.pass_context
def cmd_presentada(ctx, entidad, modelo, ejercicio, periodo, casilla, resultado, fecha, justificante):
    """Registra una autoliquidación ya presentada (p. ej. por la gestoría) para validarla y arrastrar sus datos."""
    from .impuestos import presentadas
    cas = {}
    for c in casilla:
        k, v = c.split("=")
        cas[k.strip().zfill(2)] = float(v.replace(",", "."))
    presentadas.registrar(ctx.obj, _entidad(ctx, entidad), modelo, ejercicio, periodo, cas, resultado, fecha, justificante)
    click.echo(f"Modelo {modelo} {periodo} {ejercicio} registrado")


@main.command("comparar")
@click.option("--entidad", required=True)
@click.option("--modelo", required=True, type=click.Choice(["303", "130"]))
@click.argument("ejercicio", type=int)
@click.argument("t", type=click.IntRange(1, 4))
@click.pass_context
def cmd_comparar(ctx, entidad, modelo, ejercicio, t):
    """Compara casilla a casilla el cálculo de FinSoft con la autoliquidación presentada."""
    from .impuestos import irpf, iva, presentadas
    e = _entidad(ctx, entidad)
    pres = presentadas.obtener(ctx.obj, e, modelo, ejercicio, f"{t}T")
    if pres is None:
        raise click.ClickException("No hay autoliquidación presentada registrada para ese periodo")
    calc = iva.modelo_303(ctx.obj, e, ejercicio, t) if modelo == "303" else irpf.modelo_130(ctx.obj, e, ejercicio, t)
    filas = presentadas.comparar(calc, pres, iva.NOMBRES_303 if modelo == "303" else None)
    for f in filas:
        f["estado"] = "✔" if f["cuadra"] else "✖"
    _tabla(filas, [("casilla", "Casilla"), ("descripcion", "Descripción"), ("finsoft", "FinSoft"),
                   ("presentado", "Presentado"), ("diferencia", "Diferencia"), ("estado", "")],
           {"finsoft", "presentado", "diferencia"})


@main.command("provisiones")
@click.argument("ejercicio", type=int)
@click.argument("t", type=click.IntRange(1, 4))
@click.pass_context
def cmd_provisiones(ctx, ejercicio, t):
    """Provisiones de impuestos por entidad y total del grupo."""
    pv = informes.provisiones_grupo(ctx.obj, ejercicio, t)
    filas = pv["entidades"] + [{"nombre": "TOTAL GRUPO", **pv["total"]}]
    _tabla(filas, [("nombre", "Entidad"), ("trimestral", f"Liquid. {t}T"), ("anual", "Impuesto anual"),
                   ("total", "Total a apartar"), ("concepto_anual", "Concepto")], {"trimestral", "anual", "total"})


@main.command("renta-cb")
@click.option("--entidad", required=True, help="Comunero (p. ej. XVR)")
@click.argument("ejercicio", type=int)
@click.option("--dias-alquilado", type=int)
@click.option("--dias-uso-propio", type=int, default=0)
@click.pass_context
def cmd_renta_cb(ctx, entidad, ejercicio, dias_alquilado, dias_uso_propio):
    """Datos para declarar en la Renta tu parte de una CB de capital inmobiliario (vivienda turística)."""
    from .impuestos import renta
    fichas = renta.ficha_comunero(ctx.obj, _entidad(ctx, entidad), ejercicio, dias_alquilado, dias_uso_propio)
    if not fichas:
        raise click.ClickException("Esta entidad no participa en ninguna CB de capital inmobiliario")
    for f in fichas:
        click.echo(f"\n{f['entidad']} · NIF {f['nif']} · tu participación {f['porcentaje']:g} % · ejercicio {ejercicio}")
        _tabla(renta.filas_ficha(f), [("concepto", "Concepto"), ("total", "CB 100 %"), ("parte", "Tu parte")],
               {"total", "parte"})
        for a in f["avisos"]:
            click.secho(f"  ⚠ {a}", fg="yellow")


@main.command("liquidar-iva")
@click.option("--entidad", required=True)
@click.argument("ejercicio", type=int)
@click.argument("t", type=click.IntRange(1, 4))
@click.pass_context
def liquidar_iva(ctx, entidad, ejercicio, t):
    """Contabiliza la liquidación del 303 del trimestre (4770/4720 → 4750/4700)."""
    conn, e = ctx.obj, _entidad(ctx, entidad)
    m = iva.modelo_303(conn, e, ejercicio, t)
    aid = iva.contabilizar_303(conn, e, m)
    click.echo(f"Liquidación contabilizada (asiento {aid}) · resultado {fmt(from_cents(m.get('resultado', 0)))} €"
               if aid else "Esta entidad no presenta 303")


@main.command("pagar")
@click.option("--entidad", required=True)
@click.option("--modelo", required=True, help="303, 111, 115, 123, 130, 202, 200")
@click.option("--importe", required=True, type=float)
@click.option("--fecha", default=lambda: date.today().isoformat())
@click.pass_context
def cmd_pagar(ctx, entidad, modelo, importe, fecha):
    """Registra el pago de una liquidación a Hacienda."""
    cuenta = {"303": "4750", "111": "4751", "115": "4751", "123": "4751", "130": "4731", "202": "4731",
              "200": "4752"}.get(modelo)
    if not cuenta:
        raise click.ClickException("Modelo no reconocido")
    pagar(ctx.obj, _entidad(ctx, entidad), fecha, modelo, cuenta, importe)
    click.echo("Pago registrado")


@main.command("pyg")
@click.option("--entidad", required=True)
@click.argument("ejercicio", type=int)
@click.argument("t", type=click.IntRange(1, 4), default=4)
@click.pass_context
def cmd_pyg(ctx, entidad, ejercicio, t):
    """Muestra la cuenta de pérdidas y ganancias por trimestres."""
    filas = pyg_filas(ctx.obj, _entidad(ctx, entidad), ejercicio, t)
    cols = [("concepto", "Concepto")] + [(k, k) for k in filas[0] if k not in ("concepto", "nivel")]
    _tabla([f for f in filas if f["nivel"] == 0 or any(f[k] for k, _ in cols[1:])], cols, {k for k, _ in cols[1:]})


@main.command("balance")
@click.option("--entidad", required=True)
@click.option("--fecha", default=lambda: date.today().isoformat())
@click.pass_context
def cmd_balance(ctx, entidad, fecha):
    """Muestra el balance de situación a una fecha."""
    _tabla(balance_filas(ctx.obj, _entidad(ctx, entidad), fecha), [("concepto", "Concepto"), ("actual", fecha)], {"actual"})


@main.command("rentabilidad")
@click.option("--entidad", required=True)
@click.argument("ejercicio", type=int)
@click.argument("t", type=click.IntRange(1, 4), default=4)
@click.pass_context
def cmd_rentabilidad(ctx, entidad, ejercicio, t):
    """Rentabilidad por línea de ingreso (acumulado hasta el trimestre)."""
    r = informes.rentabilidad_por_linea(ctx.obj, _entidad(ctx, entidad), f"{ejercicio}-01-01", trimestre(ejercicio, t)[1])
    _tabla(r["lineas"], [("codigo", "Línea"), ("ingresos", "Ingresos"), ("costes_directos", "Costes"),
                         ("margen_pct", "Margen %"), ("gastos_estructura", "Estructura"), ("resultado", "Resultado"),
                         ("rentabilidad_capital_anual_pct", "Rent. capital %")],
           {"ingresos", "costes_directos", "gastos_estructura", "resultado"})


if __name__ == "__main__":
    main()
