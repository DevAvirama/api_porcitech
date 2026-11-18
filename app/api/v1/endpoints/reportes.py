"""
Endpoint de exportación oficial de reportes zootécnicos en Excel (.xlsx) y PDF (.pdf) (HU39).
Integra métricas de inventario/pesos (TimescaleDB), sanidad, reproducción,
conversión alimenticia FCR (HU26) y auditoría de vacíos sanitarios (HU38).
"""

import asyncio
import html
import io
import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.animal import Animal
from app.models.reproduccion import ReproduccionParto
from app.models.sanidad import SanidadTratamiento
from app.models.user import Usuario
from app.services.zootecnia_service import (
    ESTADOS_INACTIVOS_CORRAL,
    calcular_conversion_alimenticia,
    obtener_auditoria_vacios,
    resolve_date_window,
)

router = APIRouter()

TIPO_REPORTE = Literal[
    "consolidado",
    "produccion",
    "salud",
    "reproduccion",
    "nutricion",
    "fcr",
    "vacios",
]


async def _collect_report_data(
    db: AsyncSession,
    tipo: str,
    fecha_inicio: Optional[date],
    fecha_fin: Optional[date],
    corral_id: Optional[uuid.UUID],
) -> Dict[str, Any]:
    dt_ini, dt_fin = resolve_date_window(fecha_inicio, fecha_fin, default_days=30)

    # Población activa
    animal_stmt = select(func.count(Animal.id)).where(
        Animal.deleted_at.is_(None),
        Animal.estado.notin_(ESTADOS_INACTIVOS_CORRAL),
    )
    if corral_id:
        animal_stmt = animal_stmt.where(Animal.corral_id == corral_id)
    total_animales = int((await db.execute(animal_stmt)).scalar() or 0)

    # Pesajes y promedio por corral en período
    peso_clauses = ["rp.tiempo >= :dt_ini", "rp.tiempo <= :dt_fin"]
    peso_params: Dict[str, Any] = {"dt_ini": dt_ini, "dt_fin": dt_fin}
    if corral_id:
        peso_clauses.append("rp.corral_id = :corral_id")
        peso_params["corral_id"] = corral_id

    pesos_sql = text(
        f"""
        SELECT
            c.codigo AS corral_codigo,
            c.fase AS corral_fase,
            COUNT(rp.id_registro) AS total_pesajes,
            ROUND(AVG(rp.peso_kg)::numeric, 2) AS peso_promedio_kg,
            ROUND(MIN(rp.peso_kg)::numeric, 2) AS peso_min_kg,
            ROUND(MAX(rp.peso_kg)::numeric, 2) AS peso_max_kg,
            MAX(rp.tiempo) AS ultimo_control
        FROM registro_pesos rp
        JOIN corrales c ON c.id = rp.corral_id
        WHERE {' AND '.join(peso_clauses)}
        GROUP BY c.codigo, c.fase
        ORDER BY c.codigo ASC
        """
    )
    pesos_rows = (await db.execute(pesos_sql, peso_params)).all()

    # FCR y Vacíos sanitarios reutilizando servicios compartidos
    fcr_items = await calcular_conversion_alimenticia(db, fecha_inicio, fecha_fin, corral_id)
    vacios_items = await obtener_auditoria_vacios(db, corral_id, fecha_inicio, fecha_fin)

    # Sanidad
    sanidad_stmt = (
        select(SanidadTratamiento)
        .where(
            SanidadTratamiento.deleted_at.is_(None),
            SanidadTratamiento.fecha_tratamiento >= dt_ini,
            SanidadTratamiento.fecha_tratamiento <= dt_fin,
        )
        .order_by(SanidadTratamiento.fecha_tratamiento.desc())
        .limit(100)
    )
    if corral_id:
        sanidad_stmt = sanidad_stmt.where(SanidadTratamiento.corral_id == corral_id)
    sanidad_rows = (await db.execute(sanidad_stmt)).scalars().all()

    # Reproducción (Partos)
    partos_stmt = (
        select(ReproduccionParto)
        .where(
            ReproduccionParto.deleted_at.is_(None),
            ReproduccionParto.fecha_parto >= dt_ini,
            ReproduccionParto.fecha_parto <= dt_fin,
        )
        .order_by(ReproduccionParto.fecha_parto.desc())
        .limit(100)
    )
    if corral_id:
        partos_stmt = partos_stmt.where(
            or_(
                ReproduccionParto.corral_maternidad_id == corral_id,
                ReproduccionParto.hembra_id.in_(
                    select(Animal.id).where(Animal.corral_id == corral_id)
                ),
            )
        )
    partos_rows = (await db.execute(partos_stmt)).scalars().all()

    alertas_vacios = sum(
        1 for v in vacios_items if v.estado_auditoria in ("alerta_vacio_insuficiente", "pendiente_desinfeccion")
    )
    fcr_con_datos = [f.fcr_real for f in fcr_items if f.fcr_real is not None]
    fcr_promedio = round(sum(fcr_con_datos) / len(fcr_con_datos), 2) if fcr_con_datos else None

    kpis = [
        ["Inventario Animal Activo", str(total_animales), "Cabezas en granja"],
        ["Pesajes en el Período", str(sum(int(r.total_pesajes) for r in pesos_rows)), f"{dt_ini.date()} a {dt_fin.date()}"],
        ["FCR Promedio Activo", f"{fcr_promedio:.2f}" if fcr_promedio else "N/A", "kg alimento / kg ganancia"],
        ["Eventos Sanitarios", str(len(sanidad_rows)), "Tratamientos y vacunaciones"],
        ["Partos Registrados", str(len(partos_rows)), f"Vivos totales: {sum(p.nacidos_vivos for p in partos_rows)}"],
        ["Alertas Vacío Sanitario", str(alertas_vacios), "Ciclos Todo Adentro/Afuera"],
    ]

    sections: List[Dict[str, Any]] = [
        {
            "sheet": "Resumen KPIs",
            "title": "Indicadores Clave de Desempeño Zootécnico",
            "headers": ["Indicador", "Valor Actual", "Contexto / Referencia"],
            "rows": kpis,
        }
    ]

    if tipo in ("consolidado", "produccion"):
        sections.append(
            {
                "sheet": "Pesos y Crecimiento",
                "title": "Desempeño y Crecimiento por Corral",
                "headers": ["Corral", "Fase", "Controles", "Prom (kg)", "Mín (kg)", "Máx (kg)", "Último Control"],
                "rows": [
                    [
                        r.corral_codigo,
                        str(r.corral_fase).capitalize(),
                        int(r.total_pesajes),
                        float(r.peso_promedio_kg or 0),
                        float(r.peso_min_kg or 0),
                        float(r.peso_max_kg or 0),
                        r.ultimo_control.strftime("%Y-%m-%d") if r.ultimo_control else "--",
                    ]
                    for r in pesos_rows
                ],
            }
        )

    if tipo in ("consolidado", "nutricion", "fcr"):
        sections.append(
            {
                "sheet": "Conversión FCR",
                "title": "Conversión Alimenticia (FCR) vs Estándar Porkcolombia",
                "headers": [
                    "Corral",
                    "Fase",
                    "Animales",
                    "Alimento (kg)",
                    "Ganancia (kg)",
                    "FCR Real",
                    "Meta FCR",
                    "Desviación (%)",
                    "Estado",
                ],
                "rows": [
                    [
                        f.corral_codigo,
                        f.fase.capitalize(),
                        f.animales_activos,
                        f.alimento_suministrado_kg,
                        f.ganancia_total_estimada_kg,
                        f.fcr_real if f.fcr_real is not None else "N/A",
                        f.fcr_objetivo_estandar,
                        f"{f.desviacion_porcentual:+.1f}%" if f.desviacion_porcentual is not None else "--",
                        f.estado_fcr,
                    ]
                    for f in fcr_items
                ],
            }
        )

    if tipo in ("consolidado", "vacios", "salud"):
        sections.append(
            {
                "sheet": "Vacíos Sanitarios",
                "title": "Auditoría de Vacíos Sanitarios (Todo Adentro / Todo Afuera)",
                "headers": [
                    "Corral",
                    "Lote Actual",
                    "Lote Anterior",
                    "Salida Ant.",
                    "Entrada Act.",
                    "Días Vacío",
                    "Mín Req.",
                    "Desinfección",
                    "Estado Auditoría",
                ],
                "rows": [
                    [
                        v.corral_codigo,
                        v.codigo_lote,
                        v.lote_anterior or "N/A",
                        str(v.fecha_salida_lote_anterior) if v.fecha_salida_lote_anterior else "--",
                        str(v.fecha_entrada),
                        v.dias_vacio_sanitario if v.dias_vacio_sanitario is not None else "--",
                        v.dias_vacio_requerido,
                        "Verificada" if v.desinfeccion_verificada else "Pendiente",
                        v.estado_auditoria.replace("_", " ").upper(),
                    ]
                    for v in vacios_items
                ],
            }
        )

    if tipo in ("consolidado", "salud"):
        sections.append(
            {
                "sheet": "Sanidad",
                "title": "Registro Clínico, Vacunación y Retiros ICA",
                "headers": ["Fecha", "Evento", "Producto / Biológico", "Diagnóstico", "Retiro (días)", "Corral / Animal"],
                "rows": [
                    [
                        s.fecha_tratamiento.strftime("%Y-%m-%d"),
                        s.tipo_evento,
                        s.producto_nombre,
                        s.diagnostico or "Preventivo",
                        s.tiempo_retiro_dias,
                        s.corral.codigo if s.corral else (s.animal.codigo_arete if s.animal else "Granja"),
                    ]
                    for s in sanidad_rows
                ],
            }
        )

    if tipo in ("consolidado", "reproduccion"):
        sections.append(
            {
                "sheet": "Reproducción",
                "title": "Historial de Partos y Productividad Materna",
                "headers": ["Fecha Parto", "Hembra", "Parto #", "Nac. Vivos", "Muertos", "Momias", "Peso Camada (kg)"],
                "rows": [
                    [
                        p.fecha_parto.strftime("%Y-%m-%d"),
                        p.hembra.nombre_alias or p.hembra.codigo_arete if p.hembra else "N/A",
                        p.numero_parto,
                        p.nacidos_vivos,
                        p.nacidos_muertos,
                        p.momias,
                        float(p.peso_camada_total_kg),
                    ]
                    for p in partos_rows
                ],
            }
        )

    return {
        "tipo": tipo,
        "fecha_inicio": str(dt_ini.date()),
        "fecha_fin": str(dt_fin.date()),
        "generado_en": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "sections": sections,
    }


def _sanitize_excel_cell(value: Any) -> Any:
    """Neutraliza prefijos de fórmulas en Excel/Calc para prevenir CSV/Formula Injection."""
    if isinstance(value, str) and value and value[0] in ("=", "+", "-", "@", "\t", "\r"):
        return f"'{value}"
    return value


def _render_xlsx(payload: Dict[str, Any]) -> bytes:
    """Construye un libro Excel (.xlsx) formateado con openpyxl."""
    wb = Workbook()
    wb.remove(wb.active)

    header_fill = PatternFill(start_color="0F172A", end_color="0F172A", fill_type="solid")
    sub_fill = PatternFill(start_color="059669", end_color="059669", fill_type="solid")
    alt_fill = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
    white_bold = Font(color="FFFFFF", bold=True, size=11)
    title_font = Font(color="0F172A", bold=True, size=14)
    meta_font = Font(color="475569", italic=True, size=10)

    for sec in payload["sections"]:
        ws = wb.create_sheet(title=sec["sheet"][:31])
        ws["A1"] = f"PORCITECH 4.0 — {sec['title']}"
        ws["A1"].font = title_font
        ws["A2"] = (
            f"Período auditado: {payload['fecha_inicio']} al {payload['fecha_fin']} | "
            f"Generado: {payload['generado_en']}"
        )
        ws["A2"].font = meta_font

        headers = sec["headers"]
        row_start = 4
        for col_idx, h in enumerate(headers, start=1):
            cell = ws.cell(row=row_start, column=col_idx, value=_sanitize_excel_cell(h))
            cell.fill = header_fill if sec["sheet"] == "Resumen KPIs" else sub_fill
            cell.font = white_bold
            cell.alignment = Alignment(horizontal="center", vertical="center")

        rows = sec["rows"] or [["Sin registros en el período seleccionado"] + [""] * (len(headers) - 1)]
        for r_offset, row_vals in enumerate(rows, start=1):
            r_num = row_start + r_offset
            for c_idx, val in enumerate(row_vals, start=1):
                cell = ws.cell(row=r_num, column=c_idx, value=_sanitize_excel_cell(val))
                if r_offset % 2 == 0:
                    cell.fill = alt_fill
                cell.alignment = Alignment(vertical="center")

        # Ajuste automático de ancho de columnas
        for col_idx in range(1, len(headers) + 1):
            col_letter = get_column_letter(col_idx)
            max_len = max(
                [len(str(ws.cell(row=r, column=col_idx).value or "")) for r in range(row_start, row_start + len(rows) + 1)],
                default=12,
            )
            ws.column_dimensions[col_letter].width = min(max(max_len + 4, 14), 42)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


def _render_pdf(payload: Dict[str, Any]) -> bytes:
    """Construye un reporte PDF oficial en formato horizontal con ReportLab."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(letter),
        leftMargin=28,
        rightMargin=28,
        topMargin=28,
        bottomMargin=28,
    )
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("PorciH1", parent=styles["Heading1"], fontSize=16, textColor=colors.HexColor("#0F172A"))
    h2 = ParagraphStyle("PorciH2", parent=styles["Heading2"], fontSize=12, textColor=colors.HexColor("#059669"), spaceBefore=10, spaceAfter=4)
    meta_style = ParagraphStyle("PorciMeta", parent=styles["Normal"], fontSize=9, textColor=colors.HexColor("#475569"))
    header_style = ParagraphStyle("PorciHdr", parent=styles["Normal"], fontSize=8, leading=10, textColor=colors.white, fontName="Helvetica-Bold")
    cell_style = ParagraphStyle("PorciCell", parent=styles["Normal"], fontSize=7.5, leading=9.5, textColor=colors.HexColor("#0F172A"))

    story: List[Any] = [
        Paragraph(f"PORCITECH 4.0 — Reporte Oficial ({html.escape(str(payload['tipo']).upper())})", h1),
        Paragraph(
            f"Período: {html.escape(str(payload['fecha_inicio']))} al {html.escape(str(payload['fecha_fin']))} | Emitido: {html.escape(str(payload['generado_en']))}",
            meta_style,
        ),
        Spacer(1, 10),
    ]

    usable_width = landscape(letter)[0] - 56.0
    for sec in payload["sections"]:
        story.append(Paragraph(html.escape(sec["title"]), h2))
        headers = sec["headers"]
        rows = sec["rows"] or [["Sin registros en el período"] + [""] * (len(headers) - 1)]
        table_data = [[Paragraph(html.escape(str(h)), header_style) for h in headers]] + [
            [Paragraph(html.escape(str(v if v is not None else "")), cell_style) for v in row]
            for row in rows
        ]
        col_widths = [usable_width / max(len(headers), 1)] * len(headers)

        tbl = Table(table_data, colWidths=col_widths, repeatRows=1)
        tbl.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F172A")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(tbl)
        story.append(Spacer(1, 10))

    doc.build(story)
    buf.seek(0)
    return buf.getvalue()


@router.get(
    "/exportar",
    summary="Exportar informe oficial zootécnico en Excel (.xlsx) o PDF (.pdf) (HU39)",
)
async def exportar_reporte_oficial(
    formato: Literal["xlsx", "pdf"] = Query("xlsx", description="Formato de descarga: xlsx | pdf"),
    tipo: TIPO_REPORTE = Query("consolidado", description="Tipo de reporte zootécnico"),
    fecha_inicio: Optional[date] = Query(None, description="Fecha inicial YYYY-MM-DD"),
    fecha_fin: Optional[date] = Query(None, description="Fecha final YYYY-MM-DD"),
    corral_id: Optional[uuid.UUID] = Query(None, description="Filtrar por corral"),
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    payload = await _collect_report_data(db, tipo, fecha_inicio, fecha_fin, corral_id)
    fecha_slug = datetime.now(timezone.utc).strftime("%Y%m%d")

    if formato == "xlsx":
        content = await asyncio.to_thread(_render_xlsx, payload)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = f"porcitech_{tipo}_{fecha_slug}.xlsx"
    else:
        content = await asyncio.to_thread(_render_pdf, payload)
        media_type = "application/pdf"
        filename = f"porcitech_{tipo}_{fecha_slug}.pdf"

    return StreamingResponse(
        io.BytesIO(content),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
