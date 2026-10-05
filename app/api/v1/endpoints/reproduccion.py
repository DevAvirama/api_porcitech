import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_optional, get_db
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
    # Fallback default seed user
    return uuid.UUID("b1eebc99-9c0b-4ef8-bb6d-6bb9bd380a22")


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
    servicios = result.scalars().all()

    resp = []
    for s in servicios:
        tec_nombre = None
        if s.tecnico:
            tec_nombre = f"{s.tecnico.nombre} {s.tecnico.apellido}".strip()

        resp.append(
            ReproduccionServicioResponse(
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
                sync_status=s.sync_status,
                created_at=s.created_at,
                updated_at=s.updated_at,
            )
        )
    return resp


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

    tecnico_id = await get_fallback_user_id(db, current_user, role="veterinario")

    nuevo_servicio = ReproduccionServicio(
        hembra_id=body.hembra_id,
        macho_id=body.macho_id,
        tecnico_id=tecnico_id,
        tipo_servicio=body.tipo_servicio,
        codigo_pajilla_macho=body.codigo_pajilla_macho,
        fecha_servicio=body.fecha_servicio,
        estado_confirmacion=body.estado_confirmacion or "pendiente",
        fecha_diagnostico=body.fecha_diagnostico,
        sync_status="synced",
    )
    db.add(nuevo_servicio)
    await db.commit()
    await db.refresh(nuevo_servicio)

    # Recargar con relaciones y columna generada por PostgreSQL
    stmt = select(ReproduccionServicio).where(ReproduccionServicio.id == nuevo_servicio.id)
    fresh_res = await db.execute(stmt)
    saved = fresh_res.scalar_one()

    tec_nombre = None
    if saved.tecnico:
        tec_nombre = f"{saved.tecnico.nombre} {saved.tecnico.apellido}".strip()

    return ReproduccionServicioResponse(
        id=saved.id,
        hembra_id=saved.hembra_id,
        hembra_arete=saved.hembra.codigo_arete if saved.hembra else None,
        hembra_alias=saved.hembra.nombre_alias if saved.hembra else None,
        macho_id=saved.macho_id,
        macho_arete=saved.macho.codigo_arete if saved.macho else None,
        tecnico_id=saved.tecnico_id,
        tecnico_nombre=tec_nombre,
        tipo_servicio=saved.tipo_servicio,
        codigo_pajilla_macho=saved.codigo_pajilla_macho,
        fecha_servicio=saved.fecha_servicio,
        fecha_probable_parto=saved.fecha_probable_parto,
        estado_confirmacion=saved.estado_confirmacion,
        fecha_diagnostico=saved.fecha_diagnostico,
        sync_status=saved.sync_status,
        created_at=saved.created_at,
        updated_at=saved.updated_at,
    )


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
    servicio = res.scalar_one_or_none()
    if not servicio:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Servicio reproductivo con ID '{id}' no encontrado",
        )

    servicio.estado_confirmacion = clean_estado
    servicio.fecha_diagnostico = body.fecha_diagnostico or date.today()
    servicio.updated_at = func.clock_timestamp()

    # Regla de Negocio Zootécnica: si es 'positiva', pasar la cerda a 'gestacion'
    if clean_estado == "positiva":
        hembra_query = select(Animal).where(
            Animal.id == servicio.hembra_id,
            Animal.deleted_at.is_(None),
        )
        hembra_res = await db.execute(hembra_query)
        hembra = hembra_res.scalar_one_or_none()
        if hembra:
            if hembra.estado in ("activo", "lactante", "precebo", "levante"):
                hembra.estado = "gestacion"
                hembra.updated_at = func.clock_timestamp()

    await db.commit()

    # Recargar con relaciones
    fresh_stmt = select(ReproduccionServicio).where(ReproduccionServicio.id == id)
    saved = (await db.execute(fresh_stmt)).scalar_one()

    tec_nombre = None
    if saved.tecnico:
        tec_nombre = f"{saved.tecnico.nombre} {saved.tecnico.apellido}".strip()

    return ReproduccionServicioResponse(
        id=saved.id,
        hembra_id=saved.hembra_id,
        hembra_arete=saved.hembra.codigo_arete if saved.hembra else None,
        hembra_alias=saved.hembra.nombre_alias if saved.hembra else None,
        macho_id=saved.macho_id,
        macho_arete=saved.macho.codigo_arete if saved.macho else None,
        tecnico_id=saved.tecnico_id,
        tecnico_nombre=tec_nombre,
        tipo_servicio=saved.tipo_servicio,
        codigo_pajilla_macho=saved.codigo_pajilla_macho,
        fecha_servicio=saved.fecha_servicio,
        fecha_probable_parto=saved.fecha_probable_parto,
        estado_confirmacion=saved.estado_confirmacion,
        fecha_diagnostico=saved.fecha_diagnostico,
        sync_status=saved.sync_status,
        created_at=saved.created_at,
        updated_at=saved.updated_at,
    )


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
    partos = result.scalars().all()

    resp = []
    for p in partos:
        atendido_nombre = None
        if p.atendido_por_usuario:
            atendido_nombre = f"{p.atendido_por_usuario.nombre} {p.atendido_por_usuario.apellido}".strip()

        resp.append(
            ReproduccionPartoResponse(
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
                nacidos_vivos=p.nacidos_vivos,
                nacidos_muertos=p.nacidos_muertos,
                momias=p.momias,
                peso_camada_total_kg=float(p.peso_camada_total_kg),
                observaciones=p.observaciones,
                sync_status=p.sync_status,
                created_at=p.created_at,
                updated_at=p.updated_at,
            )
        )
    return resp


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
    hembra = h_res.scalar_one_or_none()
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
        servicio = s_res.scalar_one_or_none()
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

    atendido_id = await get_fallback_user_id(db, current_user, role="veterinario")
    fecha_parto_dt = body.fecha_parto or datetime.now(timezone.utc)

    nuevo_parto = ReproduccionParto(
        servicio_id=body.servicio_id,
        hembra_id=body.hembra_id,
        corral_maternidad_id=body.corral_maternidad_id,
        atendido_por=atendido_id,
        fecha_parto=fecha_parto_dt,
        nacidos_vivos=body.nacidos_vivos,
        nacidos_muertos=body.nacidos_muertos,
        momias=body.momias,
        peso_camada_total_kg=Decimal(str(body.peso_camada_total_kg or 0.0)),
        observaciones=body.observaciones,
        sync_status="synced",
    )
    db.add(nuevo_parto)
    await db.commit()
    await db.refresh(nuevo_parto)

    # Recargar con relaciones
    stmt = select(ReproduccionParto).where(ReproduccionParto.id == nuevo_parto.id)
    fresh_res = await db.execute(stmt)
    saved = fresh_res.scalar_one()

    atendido_nombre = None
    if saved.atendido_por_usuario:
        atendido_nombre = f"{saved.atendido_por_usuario.nombre} {saved.atendido_por_usuario.apellido}".strip()

    return ReproduccionPartoResponse(
        id=saved.id,
        servicio_id=saved.servicio_id,
        hembra_id=saved.hembra_id,
        hembra_arete=saved.hembra.codigo_arete if saved.hembra else None,
        hembra_alias=saved.hembra.nombre_alias if saved.hembra else None,
        corral_maternidad_id=saved.corral_maternidad_id,
        corral_maternidad_codigo=saved.corral_maternidad.codigo if saved.corral_maternidad else None,
        atendido_por=saved.atendido_por,
        atendido_por_nombre=atendido_nombre,
        fecha_parto=saved.fecha_parto,
        nacidos_vivos=saved.nacidos_vivos,
        nacidos_muertos=saved.nacidos_muertos,
        momias=saved.momias,
        peso_camada_total_kg=float(saved.peso_camada_total_kg),
        observaciones=saved.observaciones,
        sync_status=saved.sync_status,
        created_at=saved.created_at,
        updated_at=saved.updated_at,
    )


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
    destetes = result.scalars().all()

    resp = []
    for d in destetes:
        op_name = None
        if d.operario:
            op_name = f"{d.operario.nombre} {d.operario.apellido}".strip()

        resp.append(
            ReproduccionDesteteResponse(
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
                sync_status=d.sync_status,
                created_at=d.created_at,
            )
        )
    return resp


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
    hembra = h_res.scalar_one_or_none()
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
        parto = p_res.scalar_one_or_none()
        if not parto:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Parto con id '{body.parto_id}' no encontrado",
            )
        if calculated_lactancia is None:
            parto_date = parto.fecha_parto.date()
            diff_days = (fecha_des - parto_date).days
            calculated_lactancia = max(0, diff_days)

    operario_id = await get_fallback_user_id(db, current_user, role="operario")

    nuevo_destete = ReproduccionDestete(
        parto_id=body.parto_id,
        hembra_id=body.hembra_id,
        corral_destino_id=body.corral_destino_id,
        operario_id=operario_id,
        fecha_destete=fecha_des,
        lechones_destetados=body.lechones_destetados,
        peso_total_kg=Decimal(str(body.peso_total_kg)),
        dias_lactancia=calculated_lactancia,
        sync_status="synced",
    )
    db.add(nuevo_destete)
    await db.commit()
    await db.refresh(nuevo_destete)

    # Recargar con relaciones
    stmt = select(ReproduccionDestete).where(ReproduccionDestete.id == nuevo_destete.id)
    fresh_res = await db.execute(stmt)
    saved = fresh_res.scalar_one()

    op_name = None
    if saved.operario:
        op_name = f"{saved.operario.nombre} {saved.operario.apellido}".strip()

    return ReproduccionDesteteResponse(
        id=saved.id,
        parto_id=saved.parto_id,
        hembra_id=saved.hembra_id,
        hembra_arete=saved.hembra.codigo_arete if saved.hembra else None,
        hembra_alias=saved.hembra.nombre_alias if saved.hembra else None,
        corral_destino_id=saved.corral_destino_id,
        corral_destino_codigo=saved.corral_destino.codigo if saved.corral_destino else None,
        operario_id=saved.operario_id,
        operario_nombre=op_name,
        fecha_destete=saved.fecha_destete,
        lechones_destetados=saved.lechones_destetados,
        peso_total_kg=float(saved.peso_total_kg),
        dias_lactancia=saved.dias_lactancia,
        sync_status=saved.sync_status,
        created_at=saved.created_at,
    )
