"""
Servicios de cálculo zootécnico compartido (FCR - HU26 y Auditoría de Vacíos - HU38).
Reutilizado por los endpoints de alimentación, corrales y exportación de reportes (HU39).
"""

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy import and_, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alimentacion import AlimentacionPlan, AlimentacionRacion
from app.models.animal import Animal
from app.models.corral import Corral
from app.models.inventario import InventarioItem, InventarioLote, MovimientoInventario
from app.schemas.alimentacion import ConversionAlimenticiaItem
from app.schemas.corral import AuditoriaVacioItem

ESTADOS_INACTIVOS_CORRAL = ("vendido", "muerto", "descarte")

# Referencias estándar Porkcolombia por etapa productiva (kg alimento / kg ganancia)
ESTANDAR_FCR_FASE: Dict[str, float] = {
    "precebo": 1.65,
    "destete": 1.65,
    "levante": 2.25,
    "ceba": 2.75,
    "engorde": 2.75,
    "maternidad": 2.20,
    "gestacion": 2.40,
    "cuarentena": 2.50,
}


def resolve_date_window(
    fecha_inicio: Optional[date] = None,
    fecha_fin: Optional[date] = None,
    default_days: int = 30,
) -> tuple[datetime, datetime]:
    """Convierte límites de fecha opcionales en rango TIMESTAMPTZ UTC inclusivo."""
    today = datetime.now(timezone.utc).date()
    f_fin = fecha_fin or today
    f_ini = fecha_inicio or (f_fin - timedelta(days=default_days))
    dt_ini = datetime.combine(f_ini, datetime.min.time(), tzinfo=timezone.utc)
    dt_fin = datetime.combine(f_fin, datetime.max.time(), tzinfo=timezone.utc)
    return dt_ini, dt_fin


def _classify_fcr(fcr_real: Optional[float], objetivo: float, alimento_kg: float) -> tuple[str, str]:
    if alimento_kg <= 0:
        return "Sin datos", "Sin raciones suministradas en el período seleccionado."
    if fcr_real is None:
        return "Sin ganancia registrada", "Se suministró alimento, pero faltan al menos 2 controles de peso en el período."
    if fcr_real <= objetivo * 1.05:
        return "Óptimo", f"Conversión eficiente dentro de meta Porkcolombia (<= {objetivo:.2f})."
    if fcr_real <= objetivo * 1.15:
        return "Atención", f"Desviación moderada sobre estándar {objetivo:.2f}; revisar desperdicio en comedero."
    return "Fuera de Estándar", f"Conversión crítica (>15% sobre estándar {objetivo:.2f}); auditar dieta y sanidad."


async def calcular_conversion_alimenticia(
    db: AsyncSession,
    fecha_inicio: Optional[date] = None,
    fecha_fin: Optional[date] = None,
    corral_id: Optional[uuid.UUID] = None,
) -> List[ConversionAlimenticiaItem]:
    """
    Calcula la Conversión Alimenticia (FCR = kg alimento / kg ganancia de peso)
    agrupada por corral en la ventana temporal indicada (HU26).
    """
    dt_ini, dt_fin = resolve_date_window(fecha_inicio, fecha_fin, default_days=30)

    # 1. Corrales activos + cantidad de animales activos por corral
    corral_stmt = (
        select(Corral, func.count(Animal.id).label("animales_activos"))
        .outerjoin(
            Animal,
            and_(
                Animal.corral_id == Corral.id,
                Animal.deleted_at.is_(None),
                Animal.estado.notin_(ESTADOS_INACTIVOS_CORRAL),
            ),
        )
        .where(Corral.deleted_at.is_(None), Corral.activo.is_(True))
        .group_by(Corral.id)
        .order_by(Corral.codigo.asc())
    )
    if corral_id:
        corral_stmt = corral_stmt.where(Corral.id == corral_id)

    corrales_rows = (await db.execute(corral_stmt)).all()
    if not corrales_rows:
        return []

    corral_ids = [row[0].id for row in corrales_rows]

    # 2. Alimento total y costo por corral en el período
    feed_stmt = (
        select(
            AlimentacionRacion.corral_id,
            func.coalesce(func.sum(AlimentacionRacion.cantidad_kg), 0).label("total_kg"),
            func.coalesce(func.sum(AlimentacionRacion.costo_total), 0).label("total_cop"),
        )
        .where(
            AlimentacionRacion.deleted_at.is_(None),
            AlimentacionRacion.corral_id.in_(corral_ids),
            AlimentacionRacion.fecha_suministro >= dt_ini,
            AlimentacionRacion.fecha_suministro <= dt_fin,
        )
        .group_by(AlimentacionRacion.corral_id)
    )
    feed_map: Dict[uuid.UUID, tuple[float, float]] = {
        r.corral_id: (float(r.total_kg), float(r.total_cop))
        for r in (await db.execute(feed_stmt)).all()
    }

    # 3. FCR objetivo personalizado desde planes activos (HU24) si existe
    plans_stmt = select(AlimentacionPlan).where(
        AlimentacionPlan.deleted_at.is_(None),
        AlimentacionPlan.activo.is_(True),
        AlimentacionPlan.fcr_objetivo.is_not(None),
    )
    plans = (await db.execute(plans_stmt)).scalars().all()
    plan_target_by_corral: Dict[uuid.UUID, float] = {}
    for plan in plans:
        if plan.corral_id and plan.fcr_objetivo is not None:
            plan_target_by_corral[plan.corral_id] = float(plan.fcr_objetivo)

    # 4. Ganancia de peso individual acumulada por corral desde TimescaleDB registro_pesos
    weight_sql = text(
        """
        WITH ranked AS (
            SELECT
                corral_id,
                id_cerdo,
                peso_kg,
                tiempo,
                ROW_NUMBER() OVER (PARTITION BY corral_id, id_cerdo ORDER BY tiempo ASC) AS rn_asc,
                ROW_NUMBER() OVER (PARTITION BY corral_id, id_cerdo ORDER BY tiempo DESC) AS rn_desc,
                COUNT(*) OVER (PARTITION BY corral_id, id_cerdo) AS total_lecturas
            FROM registro_pesos
            WHERE corral_id = ANY(:corral_ids)
              AND tiempo >= :dt_ini
              AND tiempo <= :dt_fin
        ),
        por_animal AS (
            SELECT
                corral_id,
                id_cerdo,
                MAX(CASE WHEN rn_asc = 1 THEN peso_kg END) AS peso_ini,
                MAX(CASE WHEN rn_desc = 1 THEN peso_kg END) AS peso_fin,
                MAX(total_lecturas) AS total_lecturas
            FROM ranked
            GROUP BY corral_id, id_cerdo
        )
        SELECT
            corral_id,
            ROUND(AVG(peso_ini)::numeric, 2) AS peso_inicial_prom,
            ROUND(AVG(peso_fin)::numeric, 2) AS peso_final_prom,
            ROUND(SUM(CASE WHEN total_lecturas >= 2 THEN GREATEST(peso_fin - peso_ini, 0) ELSE 0 END)::numeric, 2) AS ganancia_total_kg
        FROM por_animal
        GROUP BY corral_id
        """
    )
    weight_res = await db.execute(
        weight_sql,
        {"corral_ids": corral_ids, "dt_ini": dt_ini, "dt_fin": dt_fin},
    )
    weight_map: Dict[uuid.UUID, Dict[str, Any]] = {
        row.corral_id: {
            "peso_ini": float(row.peso_inicial_prom) if row.peso_inicial_prom is not None else None,
            "peso_fin": float(row.peso_final_prom) if row.peso_final_prom is not None else None,
            "ganancia": float(row.ganancia_total_kg) if row.ganancia_total_kg is not None else 0.0,
        }
        for row in weight_res.all()
    }

    items: List[ConversionAlimenticiaItem] = []
    for corral, animales_activos in corrales_rows:
        alimento_kg, costo_cop = feed_map.get(corral.id, (0.0, 0.0))
        w_info = weight_map.get(corral.id, {})
        peso_ini = w_info.get("peso_ini")
        peso_fin = w_info.get("peso_fin")
        ganancia = round(float(w_info.get("ganancia", 0.0)), 2)

        fase_key = str(corral.fase).lower()
        objetivo = plan_target_by_corral.get(corral.id, ESTANDAR_FCR_FASE.get(fase_key, 2.50))

        fcr_real: Optional[float] = None
        desviacion: Optional[float] = None
        if alimento_kg > 0 and ganancia > 0:
            fcr_real = round(alimento_kg / ganancia, 2)
            desviacion = round(((fcr_real - objetivo) / objetivo) * 100.0, 1)

        estado_fcr, observacion = _classify_fcr(fcr_real, objetivo, alimento_kg)
        items.append(
            ConversionAlimenticiaItem(
                corral_id=corral.id,
                corral_codigo=corral.codigo,
                fase=str(corral.fase),
                animales_activos=int(animales_activos or 0),
                alimento_suministrado_kg=round(alimento_kg, 2),
                costo_alimento_cop=round(costo_cop, 2),
                peso_inicial_prom_kg=peso_ini,
                peso_final_prom_kg=peso_fin,
                ganancia_total_estimada_kg=ganancia,
                fcr_real=fcr_real,
                fcr_objetivo_estandar=round(objetivo, 2),
                desviacion_porcentual=desviacion,
                estado_fcr=estado_fcr,
                observacion_zootecnica=observacion,
            )
        )

    return items


async def obtener_auditoria_vacios(
    db: AsyncSession,
    corral_id: Optional[uuid.UUID] = None,
    fecha_inicio: Optional[date] = None,
    fecha_fin: Optional[date] = None,
    solo_alertas: bool = False,
) -> List[AuditoriaVacioItem]:
    """Consulta la vista PostgreSQL `vista_auditoria_vacios_corral` (HU38)."""
    clauses = ["1=1"]
    params: Dict[str, Any] = {}

    if corral_id:
        clauses.append("corral_id = :corral_id")
        params["corral_id"] = corral_id
    if fecha_inicio:
        clauses.append("fecha_entrada >= :fecha_inicio")
        params["fecha_inicio"] = fecha_inicio
    if fecha_fin:
        clauses.append("fecha_entrada <= :fecha_fin")
        params["fecha_fin"] = fecha_fin
    if solo_alertas:
        clauses.append("estado_auditoria IN ('alerta_vacio_insuficiente', 'pendiente_desinfeccion')")

    where_sql = " AND ".join(clauses)
    stmt = text(
        f"""
        SELECT
            id,
            corral_id,
            corral_codigo,
            corral_fase,
            codigo_lote,
            lote_anterior,
            fecha_salida_lote_anterior,
            fecha_entrada,
            fecha_salida,
            cantidad_animales,
            dias_vacio_requerido,
            desinfeccion_verificada,
            observaciones,
            dias_vacio_sanitario,
            estado_auditoria
        FROM vista_auditoria_vacios_corral
        WHERE {where_sql}
        ORDER BY fecha_entrada DESC, corral_codigo ASC
        """
    )
    rows = (await db.execute(stmt, params)).all()
    return [
        AuditoriaVacioItem(
            id=r.id,
            corral_id=r.corral_id,
            corral_codigo=r.corral_codigo,
            corral_fase=str(r.corral_fase),
            codigo_lote=r.codigo_lote,
            lote_anterior=r.lote_anterior,
            fecha_salida_lote_anterior=r.fecha_salida_lote_anterior,
            fecha_entrada=r.fecha_entrada,
            fecha_salida=r.fecha_salida,
            cantidad_animales=r.cantidad_animales,
            dias_vacio_requerido=r.dias_vacio_requerido,
            desinfeccion_verificada=r.desinfeccion_verificada,
            observaciones=r.observaciones,
            dias_vacio_sanitario=r.dias_vacio_sanitario,
            estado_auditoria=r.estado_auditoria,
        )
        for r in rows
    ]


async def consumir_lotes_fefo(
    db: AsyncSession,
    item: InventarioItem,
    cantidad: Decimal,
    usuario_id: uuid.UUID,
    motivo: str,
    referencia_modulo: str,
    referencia_id: uuid.UUID,
    target_lote: Optional[InventarioLote] = None,
    hoy: Optional[date] = None,
) -> Tuple[Optional[uuid.UUID], Optional[str]]:
    """
    Descuenta inventario aplicando regla FEFO multi-lote (HU30),
    bloqueando lotes vencidos y registrando salidas en Kardex por cada lote consumido.
    """
    fecha_ref = hoy or date.today()

    if target_lote is not None:
        if target_lote.fecha_vencimiento and target_lote.fecha_vencimiento < fecha_ref:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Operación bloqueada (HU30 / Normativa ICA): El lote '{target_lote.numero_lote}' "
                    f"venció el {target_lote.fecha_vencimiento.isoformat()}."
                ),
            )
        if target_lote.cantidad_disponible < cantidad:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Stock insuficiente en el lote '{target_lote.numero_lote}'. "
                    f"Disponible: {target_lote.cantidad_disponible}, Requerido: {cantidad}"
                ),
            )
        target_lote.cantidad_disponible -= cantidad
        item.stock_actual -= cantidad
        db.add(
            MovimientoInventario(
                item_id=item.id,
                lote_inventario_id=target_lote.id,
                tipo_movimiento="salida_consumo",
                cantidad=cantidad,
                costo_unitario=item.costo_unitario,
                usuario_id=usuario_id,
                motivo=motivo,
                referencia_modulo=referencia_modulo,
                referencia_id=referencia_id,
                sync_status="synced",
            )
        )
        return target_lote.id, target_lote.numero_lote

    lotes_query = (
        select(InventarioLote)
        .where(
            InventarioLote.item_id == item.id,
            InventarioLote.deleted_at.is_(None),
            InventarioLote.cantidad_disponible > 0,
        )
        .order_by(InventarioLote.fecha_vencimiento.asc().nullslast())
        .with_for_update(of=InventarioLote)
    )
    lotes_con_stock = (await db.execute(lotes_query)).scalars().all()

    if not lotes_con_stock:
        item.stock_actual -= cantidad
        db.add(
            MovimientoInventario(
                item_id=item.id,
                lote_inventario_id=None,
                tipo_movimiento="salida_consumo",
                cantidad=cantidad,
                costo_unitario=item.costo_unitario,
                usuario_id=usuario_id,
                motivo=motivo,
                referencia_modulo=referencia_modulo,
                referencia_id=referencia_id,
                sync_status="synced",
            )
        )
        return None, None

    lotes_vigentes = [
        l for l in lotes_con_stock
        if l.fecha_vencimiento is None or l.fecha_vencimiento >= fecha_ref
    ]
    if not lotes_vigentes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Operación bloqueada (HU30 / Normativa ICA): Todos los lotes disponibles de '{item.nombre}' "
                "se encuentran vencidos. Registre un lote vigente antes de utilizarlo."
            ),
        )

    total_vigente = sum((l.cantidad_disponible for l in lotes_vigentes), Decimal("0.00"))
    if total_vigente < cantidad:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Stock vigente insuficiente para '{item.nombre}'. "
                f"Disponible en lotes no vencidos: {total_vigente}, Requerido: {cantidad}"
            ),
        )

    restante = cantidad
    primer_lote_id: Optional[uuid.UUID] = None
    lotes_usados: List[str] = []

    for lote in lotes_vigentes:
        if restante <= 0:
            break
        tomar = min(lote.cantidad_disponible, restante)
        lote.cantidad_disponible -= tomar
        restante -= tomar
        if primer_lote_id is None:
            primer_lote_id = lote.id
        lotes_usados.append(lote.numero_lote)
        db.add(
            MovimientoInventario(
                item_id=item.id,
                lote_inventario_id=lote.id,
                tipo_movimiento="salida_consumo",
                cantidad=tomar,
                costo_unitario=item.costo_unitario,
                usuario_id=usuario_id,
                motivo=f"{motivo} [Lote {lote.numero_lote}]",
                referencia_modulo=referencia_modulo,
                referencia_id=referencia_id,
                sync_status="synced",
            )
        )

    item.stock_actual -= cantidad
    return primer_lote_id, (", ".join(lotes_usados) if lotes_usados else None)
