import math
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_optional, get_db
from app.models.animal import Animal
from app.models.corral import Corral
from app.models.inventario import InventarioItem, MovimientoInventario
from app.models.sanidad import (
    EjecucionBioseguridad,
    ProtocoloBioseguridad,
    SanidadTratamiento,
)
from app.models.user import Usuario
from app.schemas.sanidad import (
    AlertaRetiroResponse,
    EjecucionBioseguridadCreate,
    EjecucionBioseguridadResponse,
    ProtocoloBioseguridadResponse,
    SanidadTratamientoCreate,
    SanidadTratamientoResponse,
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
    # Fallback admin / vet seed
    return uuid.UUID("b2eebc99-9c0b-4ef8-bb6d-6bb9bd380a23")


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
    db: AsyncSession = Depends(get_db),
):
    """
    Retorna las ejecuciones o cumplimientos de protocolos para una fecha específica (YYYY-MM-DD).
    """
    target_date = fecha or date.today()

    query = select(EjecucionBioseguridad).where(
        func.date(EjecucionBioseguridad.fecha_ejecucion) == target_date
    )
    result = await db.execute(query)
    ejecuciones = result.scalars().all()

    response = []
    for ej in ejecuciones:
        response.append(
            EjecucionBioseguridadResponse(
                id=ej.id,
                protocolo_id=ej.protocolo_id,
                corral_id=ej.corral_id,
                fecha=ej.fecha_ejecucion.strftime("%Y-%m-%d"),
                cumplido=ej.cumplido,
                observaciones=ej.observaciones,
            )
        )
    return response


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

    # Buscar ejecución existente hoy
    query_exec = select(EjecucionBioseguridad).where(
        EjecucionBioseguridad.protocolo_id == body.protocolo_id,
        func.date(EjecucionBioseguridad.fecha_ejecucion) == today,
    )
    if body.corral_id:
        query_exec = query_exec.where(EjecucionBioseguridad.corral_id == body.corral_id)

    res_exec = await db.execute(query_exec)
    existing_exec = res_exec.scalar_one_or_none()

    if existing_exec:
        existing_exec.cumplido = body.cumplido
        if body.observaciones is not None:
            existing_exec.observaciones = body.observaciones
        await db.commit()
        await db.refresh(existing_exec)
        return EjecucionBioseguridadResponse(
            id=existing_exec.id,
            protocolo_id=existing_exec.protocolo_id,
            corral_id=existing_exec.corral_id,
            fecha=existing_exec.fecha_ejecucion.strftime("%Y-%m-%d"),
            cumplido=existing_exec.cumplido,
            observaciones=existing_exec.observaciones,
        )

    operario_id = await get_fallback_user_id(db, current_user, role="operario")

    new_exec = EjecucionBioseguridad(
        protocolo_id=body.protocolo_id,
        operario_id=operario_id,
        corral_id=body.corral_id,
        cumplido=body.cumplido,
        observaciones=body.observaciones,
        sync_status="synced",
    )
    db.add(new_exec)
    await db.commit()
    await db.refresh(new_exec)

    return EjecucionBioseguridadResponse(
        id=new_exec.id,
        protocolo_id=new_exec.protocolo_id,
        corral_id=new_exec.corral_id,
        fecha=new_exec.fecha_ejecucion.strftime("%Y-%m-%d"),
        cumplido=new_exec.cumplido,
        observaciones=new_exec.observaciones,
    )


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

    resp = []
    for t in tratamientos:
        vet_nombre = None
        if t.veterinario:
            vet_nombre = f"{t.veterinario.nombre} {t.veterinario.apellido}".strip()

        resp.append(
            SanidadTratamientoResponse(
                id=t.id,
                animal_id=t.animal_id,
                animal_arete=t.animal.codigo_arete if t.animal else None,
                animal_alias=t.animal.nombre_alias if t.animal else None,
                corral_id=t.corral_id,
                corral_codigo=t.corral.codigo if t.corral else None,
                veterinario_id=t.veterinario_id,
                veterinario_nombre=vet_nombre,
                medicamento_id=t.medicamento_id,
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
        )
    return resp


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

    vet_id = await get_fallback_user_id(db, current_user, role="veterinario")

    # Si se asocia medicamento del inventario, descontar stock de bodega
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

        item.stock_actual -= cant_dec
        mov_kardex = MovimientoInventario(
            item_id=item.id,
            tipo_movimiento="salida_consumo",
            cantidad=cant_dec,
            costo_unitario=item.costo_unitario,
            usuario_id=vet_id,
            motivo=f"Aplicación clínica ({body.tipo_evento}): {body.diagnostico or body.producto_nombre}",
            sync_status="synced",
        )
        db.add(mov_kardex)

    fecha_trata = body.fecha_tratamiento or datetime.now(timezone.utc)

    nuevo_tratamiento = SanidadTratamiento(
        animal_id=body.animal_id,
        corral_id=body.corral_id,
        veterinario_id=vet_id,
        medicamento_id=body.medicamento_id,
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
        sync_status="synced",
    )
    db.add(nuevo_tratamiento)
    await db.commit()
    await db.refresh(nuevo_tratamiento)

    # Recargar con relaciones
    stmt = (
        select(SanidadTratamiento)
        .where(SanidadTratamiento.id == nuevo_tratamiento.id)
    )
    fresh_res = await db.execute(stmt)
    saved = fresh_res.scalar_one()

    vet_nombre = None
    if saved.veterinario:
        vet_nombre = f"{saved.veterinario.nombre} {saved.veterinario.apellido}".strip()

    return SanidadTratamientoResponse(
        id=saved.id,
        animal_id=saved.animal_id,
        animal_arete=saved.animal.codigo_arete if saved.animal else None,
        animal_alias=saved.animal.nombre_alias if saved.animal else None,
        corral_id=saved.corral_id,
        corral_codigo=saved.corral.codigo if saved.corral else None,
        veterinario_id=saved.veterinario_id,
        veterinario_nombre=vet_nombre,
        medicamento_id=saved.medicamento_id,
        tipo_evento=saved.tipo_evento,
        producto_nombre=saved.producto_nombre,
        diagnostico=saved.diagnostico,
        dosis=float(saved.dosis) if saved.dosis is not None else None,
        unidad_dosis=saved.unidad_dosis,
        via_administracion=saved.via_administracion,
        tiempo_retiro_dias=saved.tiempo_retiro_dias,
        fecha_tratamiento=saved.fecha_tratamiento,
        fecha_proxima_dosis=saved.fecha_proxima_dosis,
        observaciones=saved.observaciones,
    )


# ============================================================================
# 3. ALERTAS DE RETIRO FARMACOLÓGICO
# ============================================================================

@router.get("/alertas-retiro", response_model=List[AlertaRetiroResponse])
async def list_alertas_retiro(db: AsyncSession = Depends(get_db)):
    """
    Calcula y retorna los animales actualmente bajo tiempo de retiro farmacológico activo,
    indicando fecha estimada de faenado permitido y días restantes.
    """
    now = datetime.now(timezone.utc)

    query = (
        select(SanidadTratamiento)
        .where(
            SanidadTratamiento.deleted_at.is_(None),
            SanidadTratamiento.tiempo_retiro_dias > 0,
            SanidadTratamiento.animal_id.is_not(None),
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
