import uuid
from datetime import date
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.animal import Animal
from app.models.corral import Corral
from app.models.user import Usuario
from app.schemas.corral import CorralCreate, CorralResponse, CorralUpdate

router = APIRouter()


@router.get(
    "",
    response_model=List[CorralResponse],
    summary="Listar corrales",
    description="Retorna la lista de corrales activos o filtrados por estado, ordenados por código.",
)
async def list_corrales(
    activo: Optional[bool] = Query(None, description="Filtrar por estado activo/inactivo"),
    db: AsyncSession = Depends(get_db),
) -> List[CorralResponse]:
    query = select(Corral).where(Corral.deleted_at.is_(None))

    if activo is not None:
        query = query.where(Corral.activo == activo)

    query = query.order_by(Corral.codigo.asc())
    result = await db.execute(query)
    corrales = result.scalars().all()
    return [CorralResponse.model_validate(c) for c in corrales]


@router.get(
    "/{id}",
    response_model=CorralResponse,
    summary="Obtener detalle de un corral",
    description="Consulta la información detallada de un corral específico mediante su UUID.",
)
async def get_corral(
    id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> CorralResponse:
    query = select(Corral).where(Corral.id == id, Corral.deleted_at.is_(None))
    result = await db.execute(query)
    corral = result.scalar_one_or_none()

    if not corral:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Corral no encontrado o inactivo",
        )
    return CorralResponse.model_validate(corral)


@router.post(
    "",
    response_model=CorralResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Crear nuevo corral",
    description="Registra un nuevo corral en la base de datos asegurando la unicidad de su código.",
)
async def create_corral(
    corral_in: CorralCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
) -> CorralResponse:
    codigo_clean = corral_in.codigo.strip()

    # Validar código único
    check_query = select(Corral).where(
        func.lower(Corral.codigo) == codigo_clean.lower(),
        Corral.deleted_at.is_(None),
    )
    existing = (await db.execute(check_query)).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Ya existe un corral registrado con el código '{codigo_clean}'.",
        )

    corral = Corral(
        codigo=codigo_clean,
        descripcion=corral_in.descripcion.strip() if corral_in.descripcion else None,
        fase=corral_in.fase.strip().lower(),
        capacidad_maxima=corral_in.capacidad_maxima,
        fecha_inicio=corral_in.fecha_inicio or date.today(),
        activo=corral_in.activo if corral_in.activo is not None else True,
        sync_status="synced",
    )
    db.add(corral)
    await db.commit()
    await db.refresh(corral)
    return CorralResponse.model_validate(corral)


@router.put(
    "/{id}",
    response_model=CorralResponse,
    summary="Actualizar corral",
    description="Modifica las propiedades de un corral existente.",
)
async def update_corral(
    id: uuid.UUID,
    corral_in: CorralUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
) -> CorralResponse:
    query = select(Corral).where(Corral.id == id, Corral.deleted_at.is_(None))
    result = await db.execute(query)
    corral = result.scalar_one_or_none()

    if not corral:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Corral no encontrado para actualizar",
        )

    update_data = corral_in.model_dump(exclude_unset=True)

    if "codigo" in update_data and update_data["codigo"]:
        codigo_clean = update_data["codigo"].strip()
        dup_query = select(Corral).where(
            func.lower(Corral.codigo) == codigo_clean.lower(),
            Corral.id != id,
            Corral.deleted_at.is_(None),
        )
        if (await db.execute(dup_query)).scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"El código '{codigo_clean}' ya está en uso por otro corral.",
            )
        corral.codigo = codigo_clean

    if "descripcion" in update_data:
        corral.descripcion = update_data["descripcion"].strip() if update_data["descripcion"] else None

    if "fase" in update_data and update_data["fase"]:
        corral.fase = update_data["fase"].strip().lower()

    if "capacidad_maxima" in update_data and update_data["capacidad_maxima"] is not None:
        corral.capacidad_maxima = update_data["capacidad_maxima"]

    if "fecha_inicio" in update_data:
        corral.fecha_inicio = update_data["fecha_inicio"]

    if "activo" in update_data:
        corral.activo = update_data["activo"]
        # Si activo pasa a False o se provee fecha_cierre, actualiza fecha_cierre
        if not corral.activo and not corral.fecha_cierre and not update_data.get("fecha_cierre"):
            corral.fecha_cierre = date.today()

    if "fecha_cierre" in update_data:
        corral.fecha_cierre = update_data["fecha_cierre"] or date.today()

    corral.updated_at = func.clock_timestamp()
    await db.commit()
    await db.refresh(corral)
    return CorralResponse.model_validate(corral)


@router.delete(
    "/{id}",
    summary="Eliminar corral (Soft Delete)",
    description="Aplica borrado lógico al corral y lo desactiva en el sistema.",
)
async def delete_corral(
    id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    query = select(Corral).where(Corral.id == id, Corral.deleted_at.is_(None))
    result = await db.execute(query)
    corral = result.scalar_one_or_none()

    if not corral:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Corral no encontrado para eliminar",
        )

    # Validar que no tenga animales asignados activos
    animals_query = select(Animal).where(
        Animal.corral_id == id,
        Animal.deleted_at.is_(None),
        Animal.estado.notin_(["vendido", "muerto"]),
    )
    animals_result = await db.execute(animals_query)
    if animals_result.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No se puede eliminar un corral con animales asignados. Trasládelos primero.",
        )

    corral.deleted_at = func.clock_timestamp()
    corral.activo = False
    if not corral.fecha_cierre:
        corral.fecha_cierre = date.today()
    corral.updated_at = func.clock_timestamp()

    await db.commit()
    return {"message": f"Corral '{corral.codigo}' eliminado lógicamente con éxito."}
