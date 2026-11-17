"""Exportación de informes a Excel."""

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

EUR = '#,##0.00 €;[Red]-#,##0.00 €'
CABECERA = PatternFill("solid", fgColor="1F3864")
TOTAL = PatternFill("solid", fgColor="D9E1F2")


class Libro:
    def __init__(self):
        self.wb = Workbook()
        self.wb.remove(self.wb.active)

    def hoja(self, nombre: str, titulo: str, columnas: list[tuple[str, str]], filas: list[dict],
             moneda: set[str] = frozenset(), subtitulo: str | None = None, nivel_key: str | None = None,
             notas: list[str] | None = None):
        """columnas: [(clave, título)]. Las claves en `moneda` (en céntimos) se muestran en euros."""
        ws = self.wb.create_sheet(nombre[:31])
        ws["A1"] = titulo
        ws["A1"].font = Font(bold=True, size=14)
        r = 2
        if subtitulo:
            ws["A2"] = subtitulo
            ws["A2"].font = Font(italic=True, color="666666")
            r = 3
        r += 1
        for i, (_, t) in enumerate(columnas, 1):
            c = ws.cell(row=r, column=i, value=t)
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = CABECERA
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for f in filas:
            r += 1
            nivel = f.get(nivel_key) if nivel_key else None
            for i, (k, _) in enumerate(columnas, 1):
                v = f.get(k)
                if k in moneda and v is not None:
                    v = v / 100
                c = ws.cell(row=r, column=i, value=v)
                if k in moneda:
                    c.number_format = EUR
                if nivel is not None and nivel <= 0:
                    c.font = Font(bold=True)
                    c.fill = TOTAL
            if nivel == 1:
                ws.cell(row=r, column=1).alignment = Alignment(indent=2)
        for i, (k, t) in enumerate(columnas, 1):
            largo = max([len(str(t))] + [len(str(f.get(k, ""))) for f in filas[:200]])
            ws.column_dimensions[get_column_letter(i)].width = min(max(12, largo + 2), 70)
        if notas:
            r += 2
            for n in notas:
                ws.cell(row=r, column=1, value=n).font = Font(italic=True, color="666666")
                r += 1
        ws.freeze_panes = ws.cell(row=(4 if subtitulo else 3) + 1, column=1)
        return ws

    def casillas(self, nombre: str, titulo: str, casillas: dict, nombres: dict | None = None,
                 subtitulo: str | None = None, notas: list[str] | None = None, no_moneda=("65",)):
        filas = [{"casilla": k, "descripcion": (nombres or {}).get(k, ""),
                  "importe": v if k not in no_moneda else None, "valor": v if k in no_moneda else None}
                 for k, v in sorted(casillas.items(),
                                    key=lambda kv: (not kv[0].isdigit(), int(kv[0]) if kv[0].isdigit() else 0, kv[0]))]
        return self.hoja(nombre, titulo, [("casilla", "Casilla"), ("descripcion", "Descripción"),
                                          ("importe", "Importe"), ("valor", "Valor")], filas, {"importe"},
                         subtitulo, notas=notas)

    def clave_valor(self, nombre: str, titulo: str, datos: dict, moneda: set[str] = frozenset(),
                    subtitulo: str | None = None, notas: list[str] | None = None):
        filas = [{"concepto": k.replace("_", " ").capitalize(), "importe": v if k in moneda else None,
                  "valor": None if k in moneda else (v if not isinstance(v, (list, dict)) else str(v))}
                 for k, v in datos.items()]
        return self.hoja(nombre, titulo, [("concepto", "Concepto"), ("importe", "Importe"), ("valor", "Valor")],
                         filas, {"importe"}, subtitulo, notas=notas)

    def guardar(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.wb.save(path)
        return path
