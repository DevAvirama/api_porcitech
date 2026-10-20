import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_optional, get_db, resolve_user_or_fallback
from app.models.alimentacion import AlimentacionRacion
from app.models.corral import Corral
from app.models.inventario import InventarioItem, MovimientoInventario
from app.models.user import Usuario
from app.schemas.alimentacion import (
    AlimentacionRacionCreate,
    AlimentacionRacionResponse,
)

router = APIRouter()


def _to_racion_response(r: AlimentacionRacion) -> AlimentacionRacionResponse:
    op_name = f"{r.operario.nombre} {r.operario.apellido}".strip() if r.operario else None
    return AlimentacionRacionResponse(
        id=r.id,
        corral_id=r.corral_id,
        corral_codigo=r.corral.codigo if r.corral else None,
        alimento_item_id=r.alimento_item_id,
        alimento_nombre=r.alimento.nombre if r.alimento else None,
        operario_id=r.operario_id,
        operario_nombre=op_name,
        fase_alimentacion=r.fase_alimentacion,
        cantidad_kg=float(r.cantidad_kg),
        costo_total=float(r.costo_total),
        fecha_suministro=r.fecha_suministro,
        observaciones=r.observaciones,
    )


@router.get("/raciones", response_model=List[AlimentacionRacionResponse])
async def list_raciones(
    corral_id: Optional[uuid.UUID] = Query(None, description="Filtrar por corral"),
    limite: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """
    Retorna el historial de raciones y dietas suministradas en corrales.
    """
    query = (
        select(AlimentacionRacion)
        .where(AlimentacionRacion.deleted_at.is_(None))
        .order_by(AlimentacionRacion.fecha_suministro.desc())
        .limit(limite)
    )

    if corral_id:
        query = query.where(AlimentacionRacion.corral_id == corral_id)

    result = await db.execute(query)
    raciones = result.scalars().all()
    return [_to_racion_response(r) for r in raciones]


@router.post("/raciones", response_model=AlimentacionRacionResponse, status_code=status.HTTP_201_CREATED)
async def register_racion(
    body: AlimentacionRacionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[Usuario] = Depends(get_current_user_optional),
):
    """
    Registra el suministro de alimento en un corral, calculando costos y
    descontando stock disponible en bodega/silo con su correspondiente movimiento Kardex.
    """
    # Validar corral
    c_query = select(Corral).where(Corral.id == body.corral_id, Corral.deleted_at.is_(None))
    c_res = await db.execute(c_query)
    corral = c_res.scalar_one_or_none()
    if not corral:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Corral con id '{body.corral_id}' no encontrado",
        )

    # Validar insumo de alimento y bloquear para actualización de stock
    item_query = (
        select(InventarioItem)
        .where(InventarioItem.id == body.alimento_item_id, InventarioItem.deleted_at.is_(None))
        .with_for_update(of=InventarioItem)
    )
    item_res = await db.execute(item_query)
    item = item_res.scalar_one_or_none()
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Insumo de alimento con id '{body.alimento_item_id}' no encontrado",
        )

    cant_dec = Decimal(str(body.cantidad_kg))
    if cant_dec <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La cantidad de alimento suministrado debe ser mayor a 0 kg",
        )

    # Calcular conversión según factor_conversion_kg del insumo (ej. bulto de 40 kg vs 1 kg)
    factor = item.factor_conversion_kg if (item.factor_conversion_kg and item.factor_conversion_kg > 0) else Decimal("1.000")
    unidades_descontar = (cant_dec / factor).quantize(Decimal("0.01"))
    if unidades_descontar <= 0:
        unidades_descontar = Decimal("0.01")

    if item.stock_actual < unidades_descontar:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Stock insuficiente del alimento '{item.nombre}'. Disponible: {item.stock_actual} {item.unidad_medida}, Requerido: {unidades_descontar} {item.unidad_medida} ({cant_dec} kg)",
        )

    item.stock_actual -= unidades_descontar
    costo_total = (unidades_descontar * item.costo_unitario).quantize(Decimal("0.01"))

    operario_id = await resolve_user_or_fallback(db, current_user, role="operario")
    fecha_sum = body.fecha_suministro or datetime.now(timezone.utc)
    racion_id = uuid.uuid4()

    nueva_racion = AlimentacionRacion(
        id=racion_id,
        corral_id=body.corral_id,
        alimento_item_id=body.alimento_item_id,
        operario_id=operario_id,
        fase_alimentacion=body.fase_alimentacion.strip(),
        cantidad_kg=cant_dec,
        fecha_suministro=fecha_sum,
        costo_total=costo_total,
        observaciones=body.observaciones,
        sync_status="synced",
    )
    db.add(nueva_racion)

    # Registrar salida en Kardex vinculada a la ración
    mov_kardex = MovimientoInventario(
        item_id=item.id,
        tipo_movimiento="salida_consumo",
        cantidad=unidades_descontar,
        costo_unitario=item.costo_unitario,
        usuario_id=operario_id,
        motivo=f"Alimentación {body.fase_alimentacion} en corral {corral.codigo} ({cant_dec} kg)",
        referencia_modulo="alimentacion",
        referencia_id=racion_id,
        sync_status="synced",
    )
    db.add(mov_kardex)
    await db.commit()

    # Recargar con relaciones en una sola consulta
    stmt = select(AlimentacionRacion).where(AlimentacionRacion.id == nueva_racion.id)
    fresh_res = await db.execute(stmt)
    saved = fresh_res.scalar_one()

    return _to_racion_response(saved)

