import uuid
from decimal import Decimal
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.api.deps import get_current_user, get_db
from app.models.animal import Animal
from app.models.corral import Corral
from app.models.user import Usuario
from app.schemas.animal import AnimalCreate, AnimalResponse, AnimalUpdate

router = APIRouter()


@router.get(
    "",
    response_model=List[AnimalResponse],
    summary="Listar animales con filtros",
    description="Retorna el censo de animales con filtros opcionales de búsqueda, corral, estado y sexo.",
)
async def list_animales(
    search: Optional[str] = Query(None, description="Búsqueda por arete, alias o raza"),
    corral_id: Optional[uuid.UUID] = Query(None, description="Filtrar por corral asignado"),
    estado: Optional[str] = Query(None, description="Filtrar por estado o etapa productiva"),
    sexo: Optional[str] = Query(None, description="Filtrar por sexo: 'macho' o 'hembra'"),
    db: AsyncSession = Depends(get_db),
) -> List[AnimalResponse]:
    query = (
        select(Animal)
        .options(joinedload(Animal.corral))
        .where(Animal.deleted_at.is_(None))
    )

    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.where(
            or_(
                Animal.codigo_arete.ilike(term),
                Animal.nombre_alias.ilike(term),
                Animal.raza.ilike(term),
                Animal.codigo_qr.ilike(term),
            )
        )

    if corral_id is not None:
        query = query.where(Animal.corral_id == corral_id)

    if estado and estado != "all":
        query = query.where(Animal.estado == estado)

    if sexo and sexo != "all":
        query = query.where(Animal.sexo == sexo)

    query = query.order_by(Animal.created_at.desc(), Animal.codigo_arete.asc())
    result = await db.execute(query)
    animales = result.scalars().all()
    return [AnimalResponse.model_validate(a) for a in animales]


@router.get(
    "/qr/{codigo_qr}",
    response_model=AnimalResponse,
    summary="Consultar animal por código QR o arete",
    description="Endpoint público de trazabilidad para pasaporte sanitario móvil.",
)
async def get_animal_by_qr(
    codigo_qr: str,
    db: AsyncSession = Depends(get_db),
) -> AnimalResponse:
    clean_code = codigo_qr.strip()

    # Búsqueda principal por código QR
    query = (
        select(Animal)
        .options(joinedload(Animal.corral))
        .where(
            Animal.deleted_at.is_(None),
            or_(
                Animal.codigo_qr == clean_code,
                Animal.codigo_arete == clean_code,
            ),
        )
    )
    result = await db.execute(query)
    animal = result.scalar_one_or_none()

    if not animal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No se encontró ningún animal registrado con el código '{clean_code}'.",
        )

    return AnimalResponse.model_validate(animal)


@router.get(
    "/{id}",
    response_model=AnimalResponse,
    summary="Obtener detalle de un animal por UUID",
    description="Retorna la ficha técnica y genealógica completa del animal.",
)
async def get_animal(
    id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> AnimalResponse:
    query = (
        select(Animal)
        .options(joinedload(Animal.corral))
        .where(Animal.id == id, Animal.deleted_at.is_(None))
    )
    result = await db.execute(query)
    animal = result.scalar_one_or_none()

    if not animal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Animal no encontrado o inactivo",
        )

    return AnimalResponse.model_validate(animal)


@router.post(
    "",
    response_model=AnimalResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar nuevo animal",
    description="Crea un animal en PostgreSQL validando que el arete y QR sean únicos y que el corral exista.",
)
async def create_animal(
    animal_in: AnimalCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
) -> AnimalResponse:
    arete_clean = animal_in.codigo_arete.strip()
    qr_clean = animal_in.codigo_qr.strip() if animal_in.codigo_qr else f"QR-{arete_clean}"

    # 1. Validar arete o QR duplicados
    dup_query = select(Animal).where(
        Animal.deleted_at.is_(None),
        or_(
            func.lower(Animal.codigo_arete) == arete_clean.lower(),
            func.lower(Animal.codigo_qr) == qr_clean.lower(),
        ),
    )
    existing = (await db.execute(dup_query)).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Ya existe un animal registrado con el arete '{arete_clean}' o QR '{qr_clean}'.",
        )

    # 2. Validar existencia y vigencia del corral si se asigna uno
    if animal_in.corral_id:
        corral_query = select(Corral).where(
            Corral.id == animal_in.corral_id,
            Corral.deleted_at.is_(None),
        )
        corral = (await db.execute(corral_query)).scalar_one_or_none()
        if not corral:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"El corral con ID '{animal_in.corral_id}' no existe o ha sido dado de baja.",
            )

    peso_dec = Decimal(str(animal_in.peso_actual_kg)) if animal_in.peso_actual_kg is not None else Decimal("0.00")

    animal = Animal(
        codigo_arete=arete_clean,
        codigo_qr=qr_clean,
        nombre_alias=animal_in.nombre_alias.strip() if animal_in.nombre_alias else None,
        sexo=animal_in.sexo,
        raza=animal_in.raza.strip(),
        fecha_nacimiento=animal_in.fecha_nacimiento,
        estado=animal_in.estado,
        corral_id=animal_in.corral_id,
        peso_actual_kg=peso_dec,
        foto_url=animal_in.foto_url,
        id_padre=animal_in.id_padre,
        id_madre=animal_in.id_madre,
    )

    db.add(animal)
    await db.commit()

    # Recargar con la relación del corral unida
    reload_query = (
        select(Animal)
        .options(joinedload(Animal.corral))
        .where(Animal.id == animal.id)
    )
    reloaded_animal = (await db.execute(reload_query)).scalar_one()
    return AnimalResponse.model_validate(reloaded_animal)


@router.put(
    "/{id}",
    response_model=AnimalResponse,
    summary="Actualizar animal",
    description="Modifica los datos del animal.",
)
async def update_animal(
    id: uuid.UUID,
    animal_in: AnimalUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
) -> AnimalResponse:
    query = (
        select(Animal)
        .options(joinedload(Animal.corral))
        .where(Animal.id == id, Animal.deleted_at.is_(None))
    )
    result = await db.execute(query)
    animal = result.scalar_one_or_none()

    if not animal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Animal no encontrado para actualizar",
        )

    update_data = animal_in.model_dump(exclude_unset=True)

    # Validar arete si cambió
    if "codigo_arete" in update_data and update_data["codigo_arete"]:
        arete_clean = update_data["codigo_arete"].strip()
        dup_arete = select(Animal).where(
            func.lower(Animal.codigo_arete) == arete_clean.lower(),
            Animal.id != id,
            Animal.deleted_at.is_(None),
        )
        if (await db.execute(dup_arete)).scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"El arete '{arete_clean}' ya está en uso por otro animal.",
            )
        animal.codigo_arete = arete_clean

    # Validar corral si cambió
    if "corral_id" in update_data and update_data["corral_id"] is not None:
        cid = update_data["corral_id"]
        corral_check = select(Corral).where(
            Corral.id == cid,
            Corral.deleted_at.is_(None),
            Corral.activo.is_(True),
        )
        if not (await db.execute(corral_check)).scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"El corral con ID '{cid}' no existe o no se encuentra activo.",
            )
        animal.corral_id = cid

    for field, val in update_data.items():
        if field not in ("codigo_arete", "corral_id"):
            if field == "peso_actual_kg" and val is not None:
                val = Decimal(str(val))
            setattr(animal, field, val)

    animal.updated_at = func.clock_timestamp()
    await db.commit()

    # Recargar con el corral
    reload_query = (
        select(Animal)
        .options(joinedload(Animal.corral))
        .where(Animal.id == id)
    )
    reloaded = (await db.execute(reload_query)).scalar_one()
    return AnimalResponse.model_validate(reloaded)


@router.delete(
    "/{id}",
    summary="Eliminar animal (Soft Delete)",
    description="Aplica borrado lógico al animal y preserva el histórico de pesajes.",
)
async def delete_animal(
    id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    query = select(Animal).where(Animal.id == id, Animal.deleted_at.is_(None))
    result = await db.execute(query)
    animal = result.scalar_one_or_none()

    if not animal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Animal no encontrado para eliminar",
        )

    animal.deleted_at = func.clock_timestamp()
    await db.commit()
    return {"message": f"Animal con arete '{animal.codigo_arete}' eliminado lógicamente con éxito."}
