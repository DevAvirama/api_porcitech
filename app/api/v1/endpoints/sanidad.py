import math
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.idempotency import lock_id_local
from app.api.deps import get_current_user_optional, get_db, resolve_user_or_fallback
from app.models.animal import Animal
from app.models.corral import Corral
from app.models.inventario import InventarioItem, InventarioLote, MovimientoInventario
from app.models.sanidad import (
    EjecucionBioseguridad,
    ProtocoloBioseguridad,
    SanidadTratamiento,
)
from app.models.user import Usuario
from app.services.zootecnia_service import consumir_lotes_fefo
from app.schemas.sanidad import (
    AlertaRetiroResponse,
    EjecucionBioseguridadCreate,
    EjecucionBioseguridadResponse,
    ProtocoloBioseguridadResponse,
    SanidadTratamientoCreate,
    SanidadTratamientoResponse,
)

router = APIRouter()


def _to_bioseguridad_response(ej: EjecucionBioseguridad) -> EjecucionBioseguridadResponse:
    return EjecucionBioseguridadResponse(
        id=ej.id,
        protocolo_id=ej.protocolo_id,
        corral_id=ej.corral_id,
        fecha=ej.fecha_registro.strftime("%Y-%m-%d"),
        cumplido=ej.cumplido,
        observaciones=ej.observaciones,
    )


def _to_tratamiento_response(t: SanidadTratamiento) -> SanidadTratamientoResponse:
    vet_nombre = f"{t.veterinario.nombre} {t.veterinario.apellido}".strip() if t.veterinario else None
    return SanidadTratamientoResponse(
        id=t.id,
        animal_id=t.animal_id,
        animal_arete=t.animal.codigo_arete if t.animal else None,
        animal_alias=t.animal.nombre_alias if t.animal else None,
        corral_id=t.corral_id,
        corral_codigo=t.corral.codigo if t.corral else None,
        veterinario_id=t.veterinario_id,
        veterinario_nombre=vet_nombre,
        medicamento_id=t.medicamento_id,
        lote_inventario_id=t.lote_inventario_id,
        lote_biologico=t.lote_biologico,
        tipo_evento=t.tipo_evento,
        producto_nombre=t.producto_nombre,
        diagnostico=t.diagnostico,
        dosis=float(t.dosis) if t.dosis is not None else None,
        unidad_dosis=t.unidad_dosis,
        via_administracion=t.via_administracion,
        tiempo_retiro_dias=t.tiempo_retiro_dias,
        fecha_tratamiento=t.fecha_tratamiento,
        fecha_proxima_dosis=t.fecha_proxima_dosis,
        observaciones=t.observaciones,
    )


# ============================================================================
# 1. PROTOCOLOS Y BIOSEGURIDAD
# ============================================================================

@router.get("/bioseguridad/protocolos", response_model=List[ProtocoloBioseguridadResponse])
async def list_protocolos(
    activo: Optional[bool] = Query(None, description="Filtrar por estado activo"),
    db: AsyncSession = Depends(get_db),
):
    """
    Retorna la lista de protocolos de bioseguridad maestros.
    """
    query = (
        select(ProtocoloBioseguridad)
        .where(ProtocoloBioseguridad.deleted_at.is_(None))
        .order_by(ProtocoloBioseguridad.codigo.asc())
    )
    if activo is not None:
        query = query.where(ProtocoloBioseguridad.activo == activo)

    result = await db.execute(query)
    return result.scalars().all()


@router.get("/bioseguridad/ejecuciones", response_model=List[EjecucionBioseguridadResponse])
async def list_ejecuciones(
    fecha: Optional[date] = Query(None, description="Fecha de consulta (por defecto hoy)"),
    corral_id: Optional[UUID] = Query(None, description="Filtrar por corral específico"),
    db: AsyncSession = Depends(get_db),
):
    """
    Retorna las ejecuciones o cumplimientos de protocolos para una fecha específica (YYYY-MM-DD).
    """
    target_date = fecha or date.today()
    start_dt = datetime.combine(target_date, time.min, tzinfo=timezone.utc)
    end_dt = start_dt + timedelta(days=1)

    query = select(EjecucionBioseguridad).where(
        EjecucionBioseguridad.fecha_registro >= start_dt,
        EjecucionBioseguridad.fecha_registro < end_dt,
        EjecucionBioseguridad.deleted_at.is_(None),
    )
    if corral_id is not None:
        query = query.where(EjecucionBioseguridad.corral_id == corral_id)
    result = await db.execute(query)
    ejecuciones = result.scalars().all()
    return [_to_bioseguridad_response(ej) for ej in ejecuciones]


@router.post("/bioseguridad/ejecuciones", response_model=EjecucionBioseguridadResponse)
async def toggle_protocol_execution(
    body: EjecucionBioseguridadCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[Usuario] = Depends(get_current_user_optional),
):
    """
    Registra o actualiza el toggle de cumplimiento de un protocolo de bioseguridad para el día actual.
    """
    today = date.today()
    start_dt = datetime.combine(today, time.min, tzinfo=timezone.utc)
    end_dt = start_dt + timedelta(days=1)

    # Validar existencia del protocolo
    p_query = select(ProtocoloBioseguridad).where(
        ProtocoloBioseguridad.id == body.protocolo_id,
        ProtocoloBioseguridad.deleted_at.is_(None),
    )
    p_res = await db.execute(p_query)
    protocolo = p_res.scalar_one_or_none()
    if not protocolo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Protocolo con id '{body.protocolo_id}' no encontrado",
        )

    # Buscar ejecución existente hoy (rango indexable por fecha_registro)
    query_exec = select(EjecucionBioseguridad).where(
        EjecucionBioseguridad.protocolo_id == body.protocolo_id,
        EjecucionBioseguridad.deleted_at.is_(None),
        EjecucionBioseguridad.fecha_registro >= start_dt,
        EjecucionBioseguridad.fecha_registro < end_dt,
    )
    if body.corral_id:
        query_exec = query_exec.where(EjecucionBioseguridad.corral_id == body.corral_id)
    else:
        query_exec = query_exec.where(EjecucionBioseguridad.corral_id.is_(None))

    res_exec = await db.execute(query_exec.limit(1))
    existing_exec = res_exec.scalar_one_or_none()

    if existing_exec:
        existing_exec.cumplido = body.cumplido
        if body.observaciones is not None:
            existing_exec.observaciones = body.observaciones
        await db.commit()
        await db.refresh(existing_exec)
        return _to_bioseguridad_response(existing_exec)

    operario_id = await resolve_user_or_fallback(db, current_user, role="operario")

    new_exec = EjecucionBioseguridad(
        protocolo_id=body.protocolo_id,
        operario_id=operario_id,
        corral_id=body.corral_id,
        estado="completado" if body.cumplido else "pendiente",
        observaciones=body.observaciones,
        sync_status="synced",
    )
    db.add(new_exec)
    await db.commit()
    await db.refresh(new_exec)

    return _to_bioseguridad_response(new_exec)


# ============================================================================
# 2. TRATAMIENTOS CLÍNICOS Y FARMACOLÓGICOS
# ============================================================================

@router.get("/tratamientos", response_model=List[SanidadTratamientoResponse])
async def list_tratamientos(
    animal_id: Optional[uuid.UUID] = Query(None, description="Filtrar por animal"),
    corral_id: Optional[uuid.UUID] = Query(None, description="Filtrar por corral"),
    tipo_evento: Optional[str] = Query(None, description="vacuna | tratamiento | desparasitacion"),
    limite: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """
    Retorna el historial de tratamientos clínicos, vacunaciones y desparasitaciones.
    """
    query = (
        select(SanidadTratamiento)
        .where(SanidadTratamiento.deleted_at.is_(None))
        .order_by(SanidadTratamiento.fecha_tratamiento.desc())
        .limit(limite)
    )

    if animal_id:
        query = query.where(SanidadTratamiento.animal_id == animal_id)
    if corral_id:
        query = query.where(SanidadTratamiento.corral_id == corral_id)
    if tipo_evento:
        query = query.where(SanidadTratamiento.tipo_evento == tipo_evento)

    result = await db.execute(query)
    tratamientos = result.scalars().all()
    return [_to_tratamiento_response(t) for t in tratamientos]


@router.post("/tratamientos", response_model=SanidadTratamientoResponse, status_code=status.HTTP_201_CREATED)
async def create_tratamiento(
    body: SanidadTratamientoCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[Usuario] = Depends(get_current_user_optional),
):
    """
    Registra un evento clínico o vacunación. Si se provee medicamento_id y dosis,
    descuenta automáticamente de bodega generando un movimiento Kardex.
    """
    if not body.animal_id and not body.corral_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Debe especificar al menos un animal (animal_id) o un corral (corral_id) para el tratamiento sanitario.",
        )

    if body.id_local:
        await lock_id_local(db, body.id_local)
        idem_t = (
            select(SanidadTratamiento)
            .where(SanidadTratamiento.id_local == body.id_local, SanidadTratamiento.deleted_at.is_(None))
            .limit(1)
        )
        existing_t = (await db.execute(idem_t)).scalar_one_or_none()
        if existing_t:
            return _to_tratamiento_response(existing_t)

    # Validar animal si fue provisto
    if body.animal_id:
        a_query = select(Animal).where(Animal.id == body.animal_id, Animal.deleted_at.is_(None))
        a_res = await db.execute(a_query)
        if not a_res.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Animal con id '{body.animal_id}' no encontrado",
            )

    # Validar corral si fue provisto
    if body.corral_id:
        c_query = select(Corral).where(Corral.id == body.corral_id, Corral.deleted_at.is_(None))
        c_res = await db.execute(c_query)
        if not c_res.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Corral con id '{body.corral_id}' no encontrado",
            )

    vet_id = await resolve_user_or_fallback(db, current_user, role="veterinario")
    fecha_trata = body.fecha_tratamiento or datetime.now(timezone.utc)
    tratamiento_id = uuid.uuid4()
    hoy = date.today()
    resolved_lote_id = body.lote_inventario_id
    resolved_lote_biologico = body.lote_biologico.strip() if body.lote_biologico else None
    target_lote: Optional[InventarioLote] = None

    # Validación HU30: Nunca permitir usar un lote vencido en vacunaciones o tratamientos
    if body.lote_inventario_id:
        lote_query = (
            select(InventarioLote)
            .where(
                InventarioLote.id == body.lote_inventario_id,
                InventarioLote.deleted_at.is_(None),
            )
            .with_for_update(of=InventarioLote)
        )
        target_lote = (await db.execute(lote_query)).scalar_one_or_none()
        if not target_lote:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Lote de inventario con id '{body.lote_inventario_id}' no encontrado",
            )
        if body.medicamento_id and target_lote.item_id != body.medicamento_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"El lote '{target_lote.numero_lote}' no corresponde al medicamento seleccionado.",
            )
        if target_lote.fecha_vencimiento is not None and target_lote.fecha_vencimiento < hoy:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Operación bloqueada (HU30 / Normativa ICA): El lote '{target_lote.numero_lote}' "
                    f"venció el {target_lote.fecha_vencimiento.isoformat()}. No está permitido aplicar productos vencidos."
                ),
            )
        if not resolved_lote_biologico:
            resolved_lote_biologico = target_lote.numero_lote

    # Si se asocia medicamento del inventario, validar lotes vigentes y descontar stock de bodega
    if body.medicamento_id and body.dosis and body.dosis > 0:
        item_query = (
            select(InventarioItem)
            .where(InventarioItem.id == body.medicamento_id, InventarioItem.deleted_at.is_(None))
            .with_for_update(of=InventarioItem)
        )
        item_res = await db.execute(item_query)
        item = item_res.scalar_one_or_none()
        if not item:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Medicamento con id '{body.medicamento_id}' no encontrado en inventario",
            )

        cant_dec = Decimal(str(body.dosis))
        if item.stock_actual < cant_dec:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Stock insuficiente del medicamento '{item.nombre}'. Disponible: {item.stock_actual}, Dosis requerida: {cant_dec}",
            )

        lote_id_res, lote_num_res = await consumir_lotes_fefo(
            db=db,
            item=item,
            cantidad=cant_dec,
            usuario_id=vet_id,
            motivo=f"Aplicación clínica ({body.tipo_evento}): {body.diagnostico or body.producto_nombre}",
            referencia_modulo="sanidad",
            referencia_id=tratamiento_id,
            target_lote=target_lote,
            hoy=hoy,
        )
        if lote_id_res is not None:
            resolved_lote_id = lote_id_res
        if not resolved_lote_biologico and lote_num_res:
            resolved_lote_biologico = lote_num_res[:100]

    nuevo_tratamiento = SanidadTratamiento(
        id=tratamiento_id,
        animal_id=body.animal_id,
        corral_id=body.corral_id,
        veterinario_id=vet_id,
        medicamento_id=body.medicamento_id,
        lote_inventario_id=resolved_lote_id,
        lote_biologico=resolved_lote_biologico,
        tipo_evento=body.tipo_evento,
        producto_nombre=body.producto_nombre.strip(),
        diagnostico=body.diagnostico.strip() if body.diagnostico else None,
        dosis=Decimal(str(body.dosis)) if body.dosis is not None else None,
        unidad_dosis=body.unidad_dosis.strip() if body.unidad_dosis else None,
        via_administracion=body.via_administracion.strip() if body.via_administracion else None,
        tiempo_retiro_dias=body.tiempo_retiro_dias or 0,
        fecha_tratamiento=fecha_trata,
        fecha_proxima_dosis=body.fecha_proxima_dosis,
        observaciones=body.observaciones,
        id_local=body.id_local,
        sync_status="synced",
    )
    db.add(nuevo_tratamiento)
    await db.commit()

    # Recargar con relaciones en una sola consulta
    stmt = (
        select(SanidadTratamiento)
        .where(SanidadTratamiento.id == nuevo_tratamiento.id)
    )
    fresh_res = await db.execute(stmt)
    saved = fresh_res.scalar_one()

    return _to_tratamiento_response(saved)



# ============================================================================
# 3. ALERTAS DE RETIRO FARMACOLÓGICO
# ============================================================================

@router.get("/alertas-retiro", response_model=List[AlertaRetiroResponse])
async def list_alertas_retiro(db: AsyncSession = Depends(get_db)):
    """
    Calcula y retorna los animales actualmente bajo tiempo de retiro farmacológico activo,
    filtrando directamente en PostgreSQL para evitar escaneos históricos en memoria.
    """
    now = datetime.now(timezone.utc)

    query = (
        select(SanidadTratamiento)
        .where(
            SanidadTratamiento.deleted_at.is_(None),
            SanidadTratamiento.tiempo_retiro_dias > 0,
            SanidadTratamiento.animal_id.is_not(None),
            (
                SanidadTratamiento.fecha_tratamiento
                + func.make_interval(0, 0, 0, SanidadTratamiento.tiempo_retiro_dias)
            )
            > func.clock_timestamp(),
        )
        .order_by(SanidadTratamiento.fecha_tratamiento.desc())
    )
    result = await db.execute(query)
    tratamientos = result.scalars().all()

    alertas = []
    for t in tratamientos:
        fin_retiro = t.fecha_tratamiento + timedelta(days=t.tiempo_retiro_dias)
        if fin_retiro > now:
            delta_seconds = (fin_retiro - now).total_seconds()
            dias_restantes = max(1, math.ceil(delta_seconds / 86400))

            corral_code = None
            if t.corral:
                corral_code = t.corral.codigo
            elif t.animal and t.animal.corral:
                corral_code = t.animal.corral.codigo

            alertas.append(
                AlertaRetiroResponse(
                    id=t.id,
                    animal_id=t.animal_id,
                    animal_arete=t.animal.codigo_arete if t.animal else "S/A",
                    animal_alias=t.animal.nombre_alias if t.animal else None,
                    corral_codigo=corral_code,
                    producto_nombre=t.producto_nombre,
                    fecha_tratamiento=t.fecha_tratamiento,
                    tiempo_retiro_dias=t.tiempo_retiro_dias,
                    fecha_fin_retiro=fin_retiro,
                    dias_restantes=dias_restantes,
                )
            )

    return alertas
