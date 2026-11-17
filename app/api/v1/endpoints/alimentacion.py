import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_optional, get_db, require_roles, resolve_user_or_fallback
from app.models.alimentacion import AlimentacionPlan, AlimentacionRacion
from app.models.corral import Corral
from app.models.inventario import InventarioItem, MovimientoInventario
from app.models.user import Usuario
from app.schemas.alimentacion import (
    AlimentacionPlanCreate,
    AlimentacionPlanResponse,
    AlimentacionPlanUpdate,
    AlimentacionRacionCreate,
    AlimentacionRacionResponse,
    ConversionAlimenticiaItem,
)
from app.services.zootecnia_service import calcular_conversion_alimenticia

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


def _to_plan_response(plan: AlimentacionPlan) -> AlimentacionPlanResponse:
    creador = (
        f"{plan.creador.nombre} {plan.creador.apellido}".strip() if plan.creador else None
    )
    return AlimentacionPlanResponse(
        id=plan.id,
        nombre=plan.nombre,
        fase_alimentacion=plan.fase_alimentacion,
        corral_id=plan.corral_id,
        corral_codigo=plan.corral.codigo if plan.corral else None,
        alimento_item_id=plan.alimento_item_id,
        alimento_nombre=plan.alimento.nombre if plan.alimento else None,
        cantidad_kg_por_animal=float(plan.cantidad_kg_por_animal),
        frecuencia_diaria=plan.frecuencia_diaria,
        fcr_objetivo=float(plan.fcr_objetivo) if plan.fcr_objetivo is not None else None,
        activo=plan.activo,
        creado_por_nombre=creador,
        observaciones=plan.observaciones,
        created_at=plan.created_at,
    )


@router.get("/planes", response_model=List[AlimentacionPlanResponse])
async def list_planes_alimentacion(
    corral_id: Optional[uuid.UUID] = Query(None, description="Filtrar planes por corral"),
    fase: Optional[str] = Query(None, description="Filtrar por fase alimenticia"),
    solo_activos: bool = Query(True, description="Solo planes activos"),
    db: AsyncSession = Depends(get_db),
):
    """Lista planes de alimentación estandarizados por etapa o corral (HU24)."""
    stmt = (
        select(AlimentacionPlan)
        .where(AlimentacionPlan.deleted_at.is_(None))
        .order_by(AlimentacionPlan.created_at.desc())
    )
    if corral_id:
        stmt = stmt.where(AlimentacionPlan.corral_id == corral_id)
    if fase:
        stmt = stmt.where(func.lower(AlimentacionPlan.fase_alimentacion) == fase.strip().lower())
    if solo_activos:
        stmt = stmt.where(AlimentacionPlan.activo.is_(True))

    plans = (await db.execute(stmt)).scalars().all()
    return [_to_plan_response(p) for p in plans]


@router.post("/planes", response_model=AlimentacionPlanResponse, status_code=status.HTTP_201_CREATED)
async def create_plan_alimentacion(
    body: AlimentacionPlanCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(require_roles(["administrador", "veterinario"])),
):
    """Crea un plan de alimentación asignable a una fase o corral específico (HU24)."""
    if body.corral_id:
        corral_res = await db.execute(
            select(Corral).where(Corral.id == body.corral_id, Corral.deleted_at.is_(None))
        )
        if not corral_res.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Corral no encontrado.")

    item_res = await db.execute(
        select(InventarioItem).where(
            InventarioItem.id == body.alimento_item_id, InventarioItem.deleted_at.is_(None)
        )
    )
    if not item_res.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Insumo alimenticio no encontrado.")

    nuevo_plan = AlimentacionPlan(
        nombre=body.nombre.strip(),
        fase_alimentacion=body.fase_alimentacion.strip(),
        corral_id=body.corral_id,
        alimento_item_id=body.alimento_item_id,
        cantidad_kg_por_animal=Decimal(str(body.cantidad_kg_por_animal)),
        frecuencia_diaria=body.frecuencia_diaria,
        fcr_objetivo=Decimal(str(body.fcr_objetivo)) if body.fcr_objetivo is not None else None,
        activo=body.activo,
        creado_por=current_user.id,
        observaciones=body.observaciones,
    )
    db.add(nuevo_plan)
    await db.commit()

    saved = (
        await db.execute(select(AlimentacionPlan).where(AlimentacionPlan.id == nuevo_plan.id))
    ).scalar_one()
    return _to_plan_response(saved)


@router.put("/planes/{plan_id}", response_model=AlimentacionPlanResponse)
async def update_plan_alimentacion(
    plan_id: uuid.UUID,
    body: AlimentacionPlanUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(require_roles(["administrador", "veterinario"])),
):
    """Actualiza dosis, frecuencia, estado o meta FCR de un plan de alimentación (HU24)."""
    plan = (
        await db.execute(
            select(AlimentacionPlan).where(
                AlimentacionPlan.id == plan_id, AlimentacionPlan.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan no encontrado.")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if field in ("cantidad_kg_por_animal", "fcr_objetivo") and value is not None:
            setattr(plan, field, Decimal(str(value)))
        elif isinstance(value, str):
            setattr(plan, field, value.strip())
        else:
            setattr(plan, field, value)

    await db.commit()
    refreshed = (
        await db.execute(select(AlimentacionPlan).where(AlimentacionPlan.id == plan_id))
    ).scalar_one()
    return _to_plan_response(refreshed)


@router.delete("/planes/{plan_id}")
async def delete_plan_alimentacion(
    plan_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(require_roles(["administrador", "veterinario"])),
):
    """Elimina lógicamente un plan de alimentación (HU24)."""
    plan = (
        await db.execute(
            select(AlimentacionPlan).where(
                AlimentacionPlan.id == plan_id, AlimentacionPlan.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan no encontrado.")

    plan.deleted_at = func.clock_timestamp()
    plan.activo = False
    await db.commit()
    return {"message": f"Plan '{plan.nombre}' eliminado correctamente."}


@router.get("/conversion-alimenticia", response_model=List[ConversionAlimenticiaItem])
async def get_conversion_alimenticia(
    fecha_inicio: Optional[date] = Query(None, description="Fecha inicio período (YYYY-MM-DD)"),
    fecha_fin: Optional[date] = Query(None, description="Fecha fin período (YYYY-MM-DD)"),
    corral_id: Optional[uuid.UUID] = Query(None, description="Filtrar por corral específico"),
    db: AsyncSession = Depends(get_db),
):
    """
    Calcula la Conversión Alimenticia (FCR = kg alimento / kg ganancia) por corral
    en el rango temporal seleccionado y evalúa alertas frente al estándar Porkcolombia (HU26).
    """
    return await calcular_conversion_alimenticia(
        db=db,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        corral_id=corral_id,
    )

