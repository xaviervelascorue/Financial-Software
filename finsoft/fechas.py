import calendar
from datetime import date


def trimestre(ejercicio: int, t: int) -> tuple[str, str]:
    if t not in (1, 2, 3, 4):
        raise ValueError("El trimestre debe ser 1, 2, 3 o 4")
    m1 = 3 * (t - 1) + 1
    m3 = m1 + 2
    return date(ejercicio, m1, 1).isoformat(), date(ejercicio, m3, calendar.monthrange(ejercicio, m3)[1]).isoformat()


def acumulado(ejercicio: int, t: int) -> tuple[str, str]:
    """Desde el 1 de enero hasta el fin del trimestre t."""
    return f"{ejercicio}-01-01", trimestre(ejercicio, t)[1]


def fin_mes(ejercicio: int, mes: int) -> str:
    return date(ejercicio, mes, calendar.monthrange(ejercicio, mes)[1]).isoformat()


def ejercicio(ejercicio: int) -> tuple[str, str]:
    return f"{ejercicio}-01-01", f"{ejercicio}-12-31"


def trimestre_de(fecha: str) -> int:
    return (int(fecha[5:7]) - 1) // 3 + 1


def dias(desde: str, hasta: str) -> int:
    return (date.fromisoformat(hasta) - date.fromisoformat(desde)).days + 1
