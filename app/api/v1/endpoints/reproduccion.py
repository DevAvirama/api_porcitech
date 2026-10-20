import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_optional, get_db, resolve_user_or_fallback
from app.models.animal import Animal
from app.models.corral import Corral
from app.models.reproduccion import (
    ReproduccionDestete,
    ReproduccionParto,
    ReproduccionServicio,
)
from app.models.user import Usuario
from app.schemas.reproduccion import (
    ReproduccionDesteteCreate,
    ReproduccionDesteteResponse,
    ReproduccionPartoCreate,
    ReproduccionPartoResponse,
    ReproduccionServicioCreate,
    ReproduccionServicioResponse,
    ServicioUpdate,
)

router = APIRouter()


def _to_servicio_response(s: ReproduccionServicio) -> ReproduccionServicioResponse:
    tec_nombre = f"{s.tecnico.nombre} {s.tecnico.apellido}".strip() if s.tecnico else None
    return ReproduccionServicioResponse(
        id=s.id,
        hembra_id=s.hembra_id,
        hembra_arete=s.hembra.codigo_arete if s.hembra else None,
        hembra_alias=s.hembra.nombre_alias if s.hembra else None,
        macho_id=s.macho_id,
        macho_arete=s.macho.codigo_arete if s.macho else None,
        tecnico_id=s.tecnico_id,
        tecnico_nombre=tec_nombre,
        tipo_servicio=s.tipo_servicio,
        codigo_pajilla_macho=s.codigo_pajilla_macho,
        fecha_servicio=s.fecha_servicio,
        fecha_probable_parto=s.fecha_probable_parto,
        estado_confirmacion=s.estado_confirmacion,
        fecha_diagnostico=s.fecha_diagnostico,
        observaciones=s.observaciones,
        sync_status=s.sync_status,
        created_at=s.created_at,
        updated_at=s.updated_at,
    )


def _to_parto_response(p: ReproduccionParto) -> ReproduccionPartoResponse:
    atendido_nombre = (
        f"{p.atendido_por_usuario.nombre} {p.atendido_por_usuario.apellido}".strip()
        if p.atendido_por_usuario
        else None
    )
    return ReproduccionPartoResponse(
        id=p.id,
        servicio_id=p.servicio_id,
        hembra_id=p.hembra_id,
        hembra_arete=p.hembra.codigo_arete if p.hembra else None,
        hembra_alias=p.hembra.nombre_alias if p.hembra else None,
        corral_maternidad_id=p.corral_maternidad_id,
        corral_maternidad_codigo=p.corral_maternidad.codigo if p.corral_maternidad else None,
        atendido_por=p.atendido_por,
        atendido_por_nombre=atendido_nombre,
        fecha_parto=p.fecha_parto,
        numero_parto=p.numero_parto,
        nacidos_vivos=p.nacidos_vivos,
        nacidos_muertos=p.nacidos_muertos,
        momias=p.momias,
        peso_camada_total_kg=float(p.peso_camada_total_kg),
        observaciones=p.observaciones,
        sync_status=p.sync_status,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


def _to_destete_response(d: ReproduccionDestete) -> ReproduccionDesteteResponse:
    op_name = f"{d.operario.nombre} {d.operario.apellido}".strip() if d.operario else None
    return ReproduccionDesteteResponse(
        id=d.id,
        parto_id=d.parto_id,
        hembra_id=d.hembra_id,
        hembra_arete=d.hembra.codigo_arete if d.hembra else None,
        hembra_alias=d.hembra.nombre_alias if d.hembra else None,
        corral_destino_id=d.corral_destino_id,
        corral_destino_codigo=d.corral_destino.codigo if d.corral_destino else None,
        operario_id=d.operario_id,
        operario_nombre=op_name,
        fecha_destete=d.fecha_destete,
        lechones_destetados=d.lechones_destetados,
        peso_total_kg=float(d.peso_total_kg),
        dias_lactancia=d.dias_lactancia,
        observaciones=d.observaciones,
        sync_status=d.sync_status,
        created_at=d.created_at,
    )


# ============================================================================
# 1. SERVICIOS REPRODUCTIVOS (MONTA / INSEMINACIÓN)
# ============================================================================

@router.get("/servicios", response_model=List[ReproduccionServicioResponse])
async def list_servicios(
    hembra_id: Optional[uuid.UUID] = Query(None, description="Filtrar por hembra"),
    estado: Optional[str] = Query(None, description="Filtrar por estado de confirmación"),
    db: AsyncSession = Depends(get_db),
):
    """
    Retorna el historial de servicios reproductivos con fecha probable de parto (+114 días).
    """
    query = (
        select(ReproduccionServicio)
        .where(ReproduccionServicio.deleted_at.is_(None))
        .order_by(ReproduccionServicio.fecha_servicio.desc())
    )

    if hembra_id:
        query = query.where(ReproduccionServicio.hembra_id == hembra_id)
    if estado:
        query = query.where(ReproduccionServicio.estado_confirmacion == estado)

    result = await db.execute(query)
    servicios = result.unique().scalars().all()
    return [_to_servicio_response(s) for s in servicios]


@router.post("/servicios", response_model=ReproduccionServicioResponse, status_code=status.HTTP_201_CREATED)
async def create_servicio(
    body: ReproduccionServicioCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[Usuario] = Depends(get_current_user_optional),
):
    """
    Registra un nuevo servicio de monta natural o inseminación artificial.
    """
    # Validar hembra
    h_query = select(Animal).where(Animal.id == body.hembra_id, Animal.deleted_at.is_(None))
    h_res = await db.execute(h_query)
    hembra = h_res.scalar_one_or_none()
    if not hembra:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Hembra con id '{body.hembra_id}' no encontrada",
        )

    # Validar macho si fue indicado
    if body.macho_id:
        m_query = select(Animal).where(Animal.id == body.macho_id, Animal.deleted_at.is_(None))
        m_res = await db.execute(m_query)
        if not m_res.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Macho con id '{body.macho_id}' no encontrado",
            )

    tecnico_id = await resolve_user_or_fallback(db, current_user, role="veterinario")

    nuevo_servicio = ReproduccionServicio(
        hembra_id=body.hembra_id,
        macho_id=body.macho_id,
        tecnico_id=tecnico_id,
        tipo_servicio=body.tipo_servicio,
        codigo_pajilla_macho=body.codigo_pajilla_macho,
        fecha_servicio=body.fecha_servicio,
        estado_confirmacion=body.estado_confirmacion or "pendiente",
        fecha_diagnostico=body.fecha_diagnostico,
        observaciones=body.observaciones,
        sync_status="synced",
    )
    db.add(nuevo_servicio)
    await db.commit()

    # Recargar con relaciones y columna generada por PostgreSQL
    stmt = select(ReproduccionServicio).where(ReproduccionServicio.id == nuevo_servicio.id)
    fresh_res = await db.execute(stmt)
    saved = fresh_res.unique().scalar_one()

    return _to_servicio_response(saved)


@router.put("/servicios/{id}", response_model=ReproduccionServicioResponse)
async def update_servicio(
    id: uuid.UUID,
    body: ServicioUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[Usuario] = Depends(get_current_user_optional),
):
    """
    Actualiza el estado de confirmación / diagnóstico de preñez de un servicio reproductivo.
    Si el diagnóstico es 'positiva', actualiza automáticamente el estado de la cerda a 'gestacion'.
    """
    await resolve_user_or_fallback(db, current_user, role="veterinario")

    valid_estados = ["pendiente", "positiva", "negativa", "repetida"]
    clean_estado = body.estado_confirmacion.strip().lower()
    if clean_estado not in valid_estados:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Estado de confirmación '{body.estado_confirmacion}' no válido. Permitidos: {', '.join(valid_estados)}",
        )

    stmt = select(ReproduccionServicio).where(
        ReproduccionServicio.id == id,
        ReproduccionServicio.deleted_at.is_(None),
    )
    res = await db.execute(stmt)
    servicio = res.unique().scalar_one_or_none()
    if not servicio:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Servicio reproductivo con ID '{id}' no encontrado",
        )

    servicio.estado_confirmacion = clean_estado
    servicio.fecha_diagnostico = body.fecha_diagnostico or date.today()
    if body.observaciones is not None:
        servicio.observaciones = body.observaciones

    # Regla de Negocio Zootécnica: si es 'positiva', pasar la cerda a 'gestacion'
    if clean_estado == "positiva":
        hembra_query = select(Animal).where(
            Animal.id == servicio.hembra_id,
            Animal.deleted_at.is_(None),
        )
        hembra_res = await db.execute(hembra_query)
        hembra = hembra_res.unique().scalar_one_or_none()
        if hembra:
            if hembra.estado in ("activo", "lactante", "precebo", "levante", "reemplazo"):
                hembra.estado = "gestacion"

    await db.commit()

    # Recargar con relaciones
    fresh_stmt = select(ReproduccionServicio).where(ReproduccionServicio.id == id)
    saved = (await db.execute(fresh_stmt)).unique().scalar_one()

    return _to_servicio_response(saved)


# ============================================================================
# 2. PARTOS Y MATERNIDAD
# ============================================================================

@router.get("/partos", response_model=List[ReproduccionPartoResponse])
async def list_partos(
    hembra_id: Optional[uuid.UUID] = Query(None, description="Filtrar por hembra"),
    limite: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """
    Retorna los partos registrados con conteo de lechones vivos, muertos y momias.
    """
    query = (
        select(ReproduccionParto)
        .where(ReproduccionParto.deleted_at.is_(None))
        .order_by(ReproduccionParto.fecha_parto.desc())
        .limit(limite)
    )

    if hembra_id:
        query = query.where(ReproduccionParto.hembra_id == hembra_id)

    result = await db.execute(query)
    partos = result.unique().scalars().all()
    return [_to_parto_response(p) for p in partos]


@router.post("/partos", response_model=ReproduccionPartoResponse, status_code=status.HTTP_201_CREATED)
async def create_parto(
    body: ReproduccionPartoCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[Usuario] = Depends(get_current_user_optional),
):
    """
    Registra un parto asistido vinculado opcionalmente a su servicio de monta/inseminación.
    """
    # Validar hembra
    h_query = select(Animal).where(Animal.id == body.hembra_id, Animal.deleted_at.is_(None))
    h_res = await db.execute(h_query)
    hembra = h_res.unique().scalar_one_or_none()
    if not hembra:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Hembra con id '{body.hembra_id}' no encontrada",
        )

    # Validar servicio previo si fue indicado
    if body.servicio_id:
        s_query = select(ReproduccionServicio).where(
            ReproduccionServicio.id == body.servicio_id,
            ReproduccionServicio.deleted_at.is_(None),
        )
        s_res = await db.execute(s_query)
        servicio = s_res.unique().scalar_one_or_none()
        if not servicio:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Servicio con id '{body.servicio_id}' no encontrado",
            )
        # Actualizar confirmación de servicio a positiva
        servicio.estado_confirmacion = "positiva"

    # Validar corral maternidad si fue indicado
    if body.corral_maternidad_id:
        c_query = select(Corral).where(
            Corral.id == body.corral_maternidad_id, Corral.deleted_at.is_(None)
        )
        c_res = await db.execute(c_query)
        if not c_res.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Corral con id '{body.corral_maternidad_id}' no encontrado",
            )

    atendido_id = await resolve_user_or_fallback(db, current_user, role="veterinario")
    fecha_parto_dt = body.fecha_parto or datetime.now(timezone.utc)

    nuevo_parto = ReproduccionParto(
        servicio_id=body.servicio_id,
        hembra_id=body.hembra_id,
        corral_maternidad_id=body.corral_maternidad_id,
        atendido_por=atendido_id,
        fecha_parto=fecha_parto_dt,
        numero_parto=body.numero_parto or 1,
        nacidos_vivos=body.nacidos_vivos,
        nacidos_muertos=body.nacidos_muertos,
        momias=body.momias,
        peso_camada_total_kg=Decimal(str(body.peso_camada_total_kg or 0.0)),
        observaciones=body.observaciones,
        sync_status="synced",
    )
    db.add(nuevo_parto)

    # Regla de negocio zootécnica: al parir, la hembra pasa a etapa 'lactante'
    if hembra.estado not in ("muerto", "vendido", "descarte"):
        hembra.estado = "lactante"
    if body.corral_maternidad_id:
        hembra.corral_id = body.corral_maternidad_id

    await db.commit()

    # Recargar con relaciones
    stmt = select(ReproduccionParto).where(ReproduccionParto.id == nuevo_parto.id)
    fresh_res = await db.execute(stmt)
    saved = fresh_res.unique().scalar_one()

    return _to_parto_response(saved)


# ============================================================================
# 3. DESTETES Y TRANSICIÓN A PRECEBO
# ============================================================================

@router.get("/destetes", response_model=List[ReproduccionDesteteResponse])
async def list_destetes(
    hembra_id: Optional[uuid.UUID] = Query(None, description="Filtrar por hembra"),
    limite: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """
    Retorna el historial de destetes de lechones con días de lactancia registrados.
    """
    query = (
        select(ReproduccionDestete)
        .where(ReproduccionDestete.deleted_at.is_(None))
        .order_by(ReproduccionDestete.fecha_destete.desc())
        .limit(limite)
    )

    if hembra_id:
        query = query.where(ReproduccionDestete.hembra_id == hembra_id)

    result = await db.execute(query)
    destetes = result.unique().scalars().all()
    return [_to_destete_response(d) for d in destetes]


@router.post("/destetes", response_model=ReproduccionDesteteResponse, status_code=status.HTTP_201_CREATED)
async def create_destete(
    body: ReproduccionDesteteCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[Usuario] = Depends(get_current_user_optional),
):
    """
    Registra el destete de una camada, calculando los días de lactancia transcurridos.
    """
    # Validar hembra
    h_query = select(Animal).where(Animal.id == body.hembra_id, Animal.deleted_at.is_(None))
    h_res = await db.execute(h_query)
    hembra = h_res.unique().scalar_one_or_none()
    if not hembra:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Hembra con id '{body.hembra_id}' no encontrada",
        )

    # Validar corral destino si fue indicado
    if body.corral_destino_id:
        c_query = select(Corral).where(
            Corral.id == body.corral_destino_id, Corral.deleted_at.is_(None)
        )
        c_res = await db.execute(c_query)
        if not c_res.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Corral de destino con id '{body.corral_destino_id}' no encontrado",
            )

    fecha_des = body.fecha_destete or date.today()
    calculated_lactancia = body.dias_lactancia

    # Si hay parto asociado y no se especificó dias_lactancia, calcular diferencia
    if body.parto_id:
        p_query = select(ReproduccionParto).where(
            ReproduccionParto.id == body.parto_id,
            ReproduccionParto.deleted_at.is_(None),
        )
        p_res = await db.execute(p_query)
        parto = p_res.unique().scalar_one_or_none()
        if not parto:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Parto con id '{body.parto_id}' no encontrado",
            )
        if calculated_lactancia is None:
            parto_date = parto.fecha_parto.date()
            diff_days = (fecha_des - parto_date).days
            calculated_lactancia = max(0, diff_days)

    operario_id = await resolve_user_or_fallback(db, current_user, role="operario")

    nuevo_destete = ReproduccionDestete(
        parto_id=body.parto_id,
        hembra_id=body.hembra_id,
        corral_destino_id=body.corral_destino_id,
        operario_id=operario_id,
        fecha_destete=fecha_des,
        lechones_destetados=body.lechones_destetados,
        peso_total_kg=Decimal(str(body.peso_total_kg)),
        dias_lactancia=calculated_lactancia,
        observaciones=body.observaciones,
        sync_status="synced",
    )
    db.add(nuevo_destete)

    # Regla de negocio zootécnica: tras el destete, la hembra regresa a estado 'activo'
    if hembra.estado == "lactante":
        hembra.estado = "activo"

    await db.commit()

    # Recargar con relaciones
    stmt = select(ReproduccionDestete).where(ReproduccionDestete.id == nuevo_destete.id)
    fresh_res = await db.execute(stmt)
    saved = fresh_res.unique().scalar_one()

    return _to_destete_response(saved)

