"""Plan General de Contabilidad de PYMES (subconjunto a 4 dígitos) y mapeo a
balance y cuenta de pérdidas y ganancias abreviados."""

CUENTAS = {
    # Grupo 1
    "1000": "Capital social",
    "1020": "Capital (empresario individual / comunidad)",
    "1120": "Reserva legal",
    "1130": "Reservas voluntarias",
    "1180": "Aportaciones de socios o propietarios",
    "1200": "Remanente",
    "1210": "Resultados negativos de ejercicios anteriores",
    "1290": "Resultado del ejercicio",
    "1633": "Otras deudas a largo plazo con empresas del grupo",
    "1700": "Deudas a largo plazo con entidades de crédito",
    "1710": "Deudas a largo plazo",
    "1800": "Fianzas recibidas a largo plazo",
    # Grupo 2
    "2060": "Aplicaciones informáticas",
    "2100": "Terrenos y bienes naturales",
    "2110": "Construcciones",
    "2150": "Otras instalaciones",
    "2160": "Mobiliario",
    "2170": "Equipos para procesos de información",
    "2180": "Elementos de transporte",
    "2190": "Otro inmovilizado material",
    "2200": "Inversiones en terrenos y bienes naturales",
    "2210": "Inversiones en construcciones",
    "2403": "Participaciones a largo plazo en empresas del grupo",
    "2404": "Participaciones a largo plazo en empresas asociadas",
    "2423": "Créditos a largo plazo a empresas del grupo",
    "2500": "Inversiones financieras a largo plazo en instrumentos de patrimonio",
    "2510": "Valores representativos de deuda a largo plazo",
    "2520": "Créditos a largo plazo",
    "2600": "Fianzas constituidas a largo plazo",
    "2800": "Amortización acumulada del inmovilizado intangible",
    "2811": "Amortización acumulada de construcciones",
    "2815": "Amortización acumulada de otras instalaciones",
    "2816": "Amortización acumulada de mobiliario",
    "2817": "Amortización acumulada de equipos para procesos de información",
    "2818": "Amortización acumulada de elementos de transporte",
    "2819": "Amortización acumulada de otro inmovilizado material",
    "2821": "Amortización acumulada de inversiones inmobiliarias",
    "2930": "Deterioro de participaciones a largo plazo en empresas del grupo",
    # Grupo 3
    "3000": "Mercaderías",
    # Grupo 4
    "4000": "Proveedores",
    "4100": "Acreedores por prestaciones de servicios",
    "4300": "Clientes",
    "4400": "Deudores",
    "4650": "Remuneraciones pendientes de pago",
    "4700": "Hacienda Pública, deudora por IVA",
    "4709": "Hacienda Pública, deudora por devolución de impuestos",
    "4720": "Hacienda Pública, IVA soportado",
    "4730": "Hacienda Pública, retenciones y pagos a cuenta (retenciones soportadas)",
    "4731": "Hacienda Pública, pagos fraccionados (130 / 202)",
    "4750": "Hacienda Pública, acreedora por IVA",
    "4751": "Hacienda Pública, acreedora por retenciones practicadas",
    "4752": "Hacienda Pública, acreedora por Impuesto sobre Sociedades",
    "4760": "Organismos de la Seguridad Social, acreedores",
    "4770": "Hacienda Pública, IVA repercutido",
    "4800": "Gastos anticipados",
    "4850": "Ingresos anticipados",
    # Grupo 5
    "5200": "Deudas a corto plazo con entidades de crédito",
    "5230": "Proveedores de inmovilizado a corto plazo",
    "5260": "Dividendo activo a pagar",
    "5323": "Créditos a corto plazo a empresas del grupo",
    "5400": "Inversiones financieras a corto plazo en instrumentos de patrimonio",
    "5410": "Valores representativos de deuda a corto plazo",
    "5480": "Imposiciones a corto plazo",
    "5500": "Titular de la explotación",
    "5510": "Cuenta corriente con socios y administradores",
    "5523": "Cuenta corriente con empresas del grupo",
    "5570": "Dividendo a cuenta",
    "5600": "Fianzas recibidas a corto plazo",
    "5650": "Fianzas constituidas a corto plazo",
    "5700": "Caja, euros",
    "5720": "Bancos e instituciones de crédito c/c vista, euros",
    # Grupo 6
    "6000": "Compras de mercaderías",
    "6020": "Compras de otros aprovisionamientos",
    "6070": "Trabajos realizados por otras empresas",
    "6210": "Arrendamientos y cánones",
    "6220": "Reparaciones y conservación",
    "6230": "Servicios de profesionales independientes",
    "6240": "Transportes",
    "6250": "Primas de seguros",
    "6251": "Primas de seguro de enfermedad del titular (límite 500 €/persona, art. 30.2.5ª.a LIRPF)",
    "6260": "Servicios bancarios y similares",
    "6270": "Publicidad, propaganda y relaciones públicas",
    "6280": "Suministros",
    "6290": "Otros servicios",
    "6291": "Comisiones de plataformas de reserva",
    "6295": "Gastos de comunidad de propietarios",
    "6300": "Impuesto corriente",
    "6310": "Otros tributos",
    "6340": "Ajustes negativos en la imposición indirecta (regularización prorrata)",
    "6390": "Ajustes positivos en la imposición indirecta (regularización prorrata)",
    "6400": "Sueldos y salarios",
    "6420": "Seguridad Social a cargo de la empresa / cuota de autónomos",
    "6490": "Otros gastos sociales",
    "6620": "Intereses de deudas",
    "6630": "Pérdidas por valoración de instrumentos financieros por su valor razonable",
    "6660": "Pérdidas en participaciones y valores representativos de deuda",
    "6680": "Diferencias negativas de cambio",
    "6690": "Otros gastos financieros",
    "6710": "Pérdidas procedentes del inmovilizado material",
    "6780": "Gastos excepcionales",
    "6800": "Amortización del inmovilizado intangible",
    "6810": "Amortización del inmovilizado material",
    "6820": "Amortización de las inversiones inmobiliarias",
    "6960": "Pérdidas por deterioro de participaciones y valores a largo plazo",
    # Grupo 7
    "7000": "Ventas de mercaderías",
    "7050": "Prestaciones de servicios",
    "7090": "Rappels sobre ventas",
    "7520": "Ingresos por arrendamientos",
    "7590": "Ingresos por servicios diversos",
    "7600": "Ingresos de participaciones en instrumentos de patrimonio, empresas del grupo",
    "7603": "Ingresos de participaciones en instrumentos de patrimonio, otras empresas",
    "7610": "Ingresos de valores representativos de deuda",
    "7620": "Ingresos de créditos",
    "7630": "Beneficios por valoración de instrumentos financieros por su valor razonable",
    "7660": "Beneficios en participaciones y valores representativos de deuda",
    "7680": "Diferencias positivas de cambio",
    "7690": "Otros ingresos financieros",
    "7710": "Beneficios procedentes del inmovilizado material",
    "7780": "Ingresos excepcionales",
}

# Cuentas de amortización acumulada por cuenta de activo
AMORT_ACUMULADA = {
    "206": "2800", "211": "2811", "215": "2815", "216": "2816", "217": "2817",
    "218": "2818", "219": "2819", "221": "2821",
}

# Coeficientes de amortización (vida útil orientativa en años, tablas simplificadas)
VIDA_UTIL = {
    "206": 3, "211": 50, "215": 10, "216": 10, "217": 4, "218": 6, "219": 10, "221": 50,
}


def nombre_cuenta(codigo: str) -> str:
    if codigo in CUENTAS:
        return CUENTAS[codigo]
    for n in range(len(codigo) - 1, 0, -1):
        base = codigo[:n].ljust(4, "0")
        if base in CUENTAS:
            return CUENTAS[base]
    return f"Cuenta {codigo}"


def cuenta_valida(codigo: str) -> bool:
    return codigo.isdigit() and 3 <= len(codigo) <= 10 and codigo[0] in "1234567"


# ---------------------------------------------------------------- Balance
# (masa, grupo, epígrafe, prefijos). Gana el prefijo más largo.
ANC = "A) ACTIVO NO CORRIENTE"
AC = "B) ACTIVO CORRIENTE"
PN = "A) PATRIMONIO NETO"
PNC = "B) PASIVO NO CORRIENTE"
PC = "C) PASIVO CORRIENTE"

BALANCE = [
    ("ACTIVO", ANC, "I. Inmovilizado intangible", ["20", "280", "290"]),
    ("ACTIVO", ANC, "II. Inmovilizado material", ["21", "23", "281", "291"]),
    ("ACTIVO", ANC, "III. Inversiones inmobiliarias", ["22", "282", "292"]),
    ("ACTIVO", ANC, "IV. Inversiones en empresas del grupo y asociadas a largo plazo",
     ["2403", "2404", "2413", "2414", "2423", "2424", "293"]),
    ("ACTIVO", ANC, "V. Inversiones financieras a largo plazo",
     ["24", "25", "26", "294", "295", "296", "297", "298"]),
    ("ACTIVO", ANC, "VI. Activos por impuesto diferido", ["474"]),
    ("ACTIVO", AC, "I. Existencias", ["3", "407"]),
    ("ACTIVO", AC, "II. Deudores comerciales y otras cuentas a cobrar",
     ["43", "44", "46", "470", "471", "472", "473", "490", "5580"]),
    ("ACTIVO", AC, "III. Inversiones en empresas del grupo y asociadas a corto plazo",
     ["5303", "5304", "5313", "5314", "5323", "5324", "5333", "5334", "5343", "5344", "5353", "5354"]),
    ("ACTIVO", AC, "IV. Inversiones financieras a corto plazo", ["53", "54", "565", "566", "59"]),
    ("ACTIVO", AC, "V. Periodificaciones a corto plazo", ["480", "567"]),
    ("ACTIVO", AC, "VI. Efectivo y otros activos líquidos equivalentes", ["57"]),
    ("PN_PASIVO", PN, "I. Capital", ["10"]),
    ("PN_PASIVO", PN, "II. Prima de emisión", ["110"]),
    ("PN_PASIVO", PN, "III. Reservas", ["11"]),
    ("PN_PASIVO", PN, "IV. (Acciones y participaciones propias)", ["108", "109"]),
    ("PN_PASIVO", PN, "V. Resultados de ejercicios anteriores", ["12"]),
    ("PN_PASIVO", PN, "VI. Otras aportaciones de socios", ["118"]),
    ("PN_PASIVO", PN, "VII. Resultado del ejercicio", ["129"]),
    ("PN_PASIVO", PN, "VIII. (Dividendo a cuenta)", ["557"]),
    ("PN_PASIVO", PN, "IX. Cuenta del titular de la explotación", ["550"]),
    ("PN_PASIVO", PN, "Subvenciones, donaciones y legados recibidos", ["13"]),
    ("PN_PASIVO", PNC, "I. Provisiones a largo plazo", ["14"]),
    ("PN_PASIVO", PNC, "II. Deudas a largo plazo", ["15", "17", "18"]),
    ("PN_PASIVO", PNC, "III. Deudas con empresas del grupo y asociadas a largo plazo", ["16"]),
    ("PN_PASIVO", PNC, "IV. Pasivos por impuesto diferido", ["479"]),
    ("PN_PASIVO", PC, "I. Provisiones a corto plazo", ["499", "529"]),
    ("PN_PASIVO", PC, "II. Deudas a corto plazo", ["50", "52", "551", "555", "56"]),
    ("PN_PASIVO", PC, "III. Deudas con empresas del grupo y asociadas a corto plazo",
     ["5103", "5104", "5113", "5114", "5123", "5124", "5133", "5134", "5523", "5524"]),
    ("PN_PASIVO", PC, "IV. Acreedores comerciales y otras cuentas a pagar",
     ["40", "41", "438", "465", "466", "475", "476", "477"]),
    ("PN_PASIVO", PC, "V. Periodificaciones a corto plazo", ["485", "568"]),
]

# Cuentas de pasivo que, con saldo deudor, se presentan en el activo.
CUENTAS_MIXTAS = {
    "551": ("ACTIVO", AC, "IV. Inversiones financieras a corto plazo"),
    "555": ("ACTIVO", AC, "IV. Inversiones financieras a corto plazo"),
    "5523": ("ACTIVO", AC, "III. Inversiones en empresas del grupo y asociadas a corto plazo"),
}


def _longest(codigo: str, reglas):
    mejor, largo = None, -1
    for regla in reglas:
        for p in regla[-1]:
            if codigo.startswith(p) and len(p) > largo:
                mejor, largo = regla, len(p)
    return mejor


def clasificar_balance(codigo: str, saldo_deudor: int):
    """Devuelve (masa, grupo, epígrafe) de una cuenta de balance."""
    for pref, alt in CUENTAS_MIXTAS.items():
        if codigo.startswith(pref) and saldo_deudor > 0:
            return alt
    regla = _longest(codigo, BALANCE)
    if regla is None:
        return None
    return regla[:3]


# ---------------------------------------------------------------- PyG
PYG = [
    ("1. Importe neto de la cifra de negocios", ["70"]),
    ("2. Variación de existencias de productos terminados y en curso", ["6930", "71", "7930"]),
    ("3. Trabajos realizados por la empresa para su activo", ["73"]),
    ("4. Aprovisionamientos", ["60", "61", "6931", "6932", "6933", "7931", "7932", "7933"]),
    ("5. Otros ingresos de explotación", ["74", "75"]),
    ("6. Gastos de personal", ["64", "7950", "7957"]),
    ("7. Otros gastos de explotación", ["62", "631", "634", "636", "639", "65", "694", "695", "794", "7954"]),
    ("8. Amortización del inmovilizado", ["68"]),
    ("9. Imputación de subvenciones de inmovilizado no financiero y otras", ["746"]),
    ("10. Excesos de provisiones", ["7951", "7952", "7955", "7956"]),
    ("11. Deterioro y resultado por enajenaciones del inmovilizado",
     ["670", "671", "672", "690", "691", "692", "770", "771", "772", "790", "791", "792"]),
    ("12. Otros resultados", ["678", "778"]),
    ("13. Ingresos financieros", ["760", "761", "762", "767", "769"]),
    ("14. Gastos financieros", ["660", "661", "662", "664", "665", "669"]),
    ("15. Variación de valor razonable en instrumentos financieros", ["663", "763"]),
    ("16. Diferencias de cambio", ["668", "768"]),
    ("17. Deterioro y resultado por enajenaciones de instrumentos financieros",
     ["666", "667", "673", "675", "696", "697", "698", "699", "766", "773", "775", "796", "797", "798", "799"]),
    ("18. Impuestos sobre beneficios", ["630", "633", "638"]),
]
PYG_EXPLOTACION = PYG[:12]
PYG_FINANCIERO = PYG[12:17]

# En sociedades holding, los dividendos e intereses de empresas del grupo forman
# parte de la cifra de negocios (consulta ICAC, BOICAC 79/2009).
PYG_HOLDING_CIFRA_NEGOCIOS = ["7600", "7620"]


def clasificar_pyg(codigo: str, es_holding: bool = False) -> str | None:
    if es_holding and any(codigo.startswith(p) for p in PYG_HOLDING_CIFRA_NEGOCIOS):
        return PYG[0][0]
    regla = _longest(codigo, [(n, p) for n, p in PYG])
    return regla[0] if regla else None
