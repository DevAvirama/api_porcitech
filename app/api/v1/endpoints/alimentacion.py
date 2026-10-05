import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_optional, get_db
from app.models.alimentacion import AlimentacionRacion
from app.models.corral import Corral
from app.models.inventario import InventarioItem, MovimientoInventario
from app.models.user import Usuario
from app.schemas.alimentacion import (
    AlimentacionRacionCreate,
    AlimentacionRacionResponse,
)

router = APIRouter()


async def get_fallback_user_id(
    db: AsyncSession, current_user: Optional[Usuario] = None, role: Optional[str] = None
) -> uuid.UUID:
    if current_user:
        return current_user.id
    query = select(Usuario.id).where(Usuario.activo.is_(True), Usuario.deleted_at.is_(None))
    if role:
        query = query.where(Usuario.rol == role)
    result = await db.execute(query.limit(1))
    user_id = result.scalar_one_or_none()
    if user_id:
        return user_id
    # Fallback operario seed
    return uuid.UUID("b3eebc99-9c0b-4ef8-bb6d-6bb9bd380a24")


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

    resp = []
    for r in raciones:
        op_name = None
        if r.operario:
            op_name = f"{r.operario.nombre} {r.operario.apellido}".strip()

        resp.append(
            AlimentacionRacionResponse(
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
        )
    return resp


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

    # Descontar stock si es posible
    if item.stock_actual < cant_dec:
        # Si no hay suficiente stock en bultos/kg, avisamos o permitimos según política
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Stock insuficiente del alimento '{item.nombre}'. Disponible: {item.stock_actual} {item.unidad_medida}, Requerido: {cant_dec} kg",
        )

    item.stock_actual -= cant_dec
    costo_total = item.costo_unitario * cant_dec

    operario_id = await get_fallback_user_id(db, current_user, role="operario")

    # Registrar salida en Kardex
    mov_kardex = MovimientoInventario(
        item_id=item.id,
        tipo_movimiento="salida_consumo",
        cantidad=cant_dec,
        costo_unitario=item.costo_unitario,
        usuario_id=operario_id,
        motivo=f"Alimentación {body.fase_alimentacion} en corral {corral.codigo}",
        sync_status="synced",
    )
    db.add(mov_kardex)

    fecha_sum = body.fecha_suministro or datetime.now(timezone.utc)

    nueva_racion = AlimentacionRacion(
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
    await db.commit()
    await db.refresh(nueva_racion)

    # Recargar con relaciones
    stmt = select(AlimentacionRacion).where(AlimentacionRacion.id == nueva_racion.id)
    fresh_res = await db.execute(stmt)
    saved = fresh_res.scalar_one()

    op_name = None
    if saved.operario:
        op_name = f"{saved.operario.nombre} {saved.operario.apellido}".strip()

    return AlimentacionRacionResponse(
        id=saved.id,
        corral_id=saved.corral_id,
        corral_codigo=saved.corral.codigo if saved.corral else None,
        alimento_item_id=saved.alimento_item_id,
        alimento_nombre=saved.alimento.nombre if saved.alimento else None,
        operario_id=saved.operario_id,
        operario_nombre=op_name,
        fase_alimentacion=saved.fase_alimentacion,
        cantidad_kg=float(saved.cantidad_kg),
        costo_total=float(saved.costo_total),
        fecha_suministro=saved.fecha_suministro,
        observaciones=saved.observaciones,
    )
