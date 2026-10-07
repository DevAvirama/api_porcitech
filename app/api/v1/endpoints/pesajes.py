import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user_optional, get_db
from app.models.animal import Animal
from app.models.corral import Corral
from app.models.pesaje import RegistroPeso
from app.models.user import Usuario
from app.schemas.pesaje import GMDDiariaItem, PesajeManualCreate, PesajeResponse

router = APIRouter()


@router.post(
    "/manual",
    response_model=PesajeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar pesaje manual o por báscula digital",
    description="Inserta una medición física en la hipertabla TimescaleDB registro_pesos y actualiza el peso actual del cerdo.",
)
async def create_pesaje_manual(
    pesaje_in: PesajeManualCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[Usuario] = Depends(get_current_user_optional),
) -> PesajeResponse:
    # 1. Validar existencia del animal
    animal = await db.get(Animal, pesaje_in.id_cerdo)
    if not animal or animal.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No se encontró el animal con ID {pesaje_in.id_cerdo}.",
        )

    # 2. Validar existencia del corral
    corral = await db.get(Corral, pesaje_in.corral_id)
    if not corral or corral.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No se encontró el corral con ID {pesaje_in.corral_id}.",
        )

    # 3. Formatear y persistir registro en TimescaleDB
    tiempo_pesaje = pesaje_in.tiempo or datetime.now(timezone.utc)
    metodo_val = pesaje_in.metodo if pesaje_in.metodo in ["manual", "bascula_digital", "vision_ai"] else "manual"
    nuevo_id = uuid.uuid4()

    nuevo_registro = RegistroPeso(
        tiempo=tiempo_pesaje,
        id_registro=nuevo_id,
        id_cerdo=pesaje_in.id_cerdo,
        corral_id=pesaje_in.corral_id,
        peso_kg=float(pesaje_in.peso_kg),
        metodo=metodo_val,
        sync_status="synced",
    )
    db.add(nuevo_registro)

    # 4. Actualización en tabla animales
    animal.peso_actual_kg = Decimal(str(round(pesaje_in.peso_kg, 2)))
    animal.updated_at = func.clock_timestamp()

    await db.commit()

    return PesajeResponse(
        id_registro=nuevo_id,
        tiempo=tiempo_pesaje,
        id_cerdo=pesaje_in.id_cerdo,
        corral_id=pesaje_in.corral_id,
        peso_kg=float(pesaje_in.peso_kg),
        metodo=metodo_val,
        confianza_ia=None,
        area_cm2=None,
        largo_cm=None,
        ancho_cm=None,
        bbox=None,
        foto_evidencia_url=None,
        sync_status="synced",
        created_at=tiempo_pesaje,
        cerdo_alias=animal.nombre_alias,
        cerdo_arete=animal.codigo_arete,
        corral_codigo=corral.codigo,
    )


@router.get(
    "/animal/{animal_id}",
    response_model=List[PesajeResponse],
    summary="Historial de pesajes de un animal",
    description="Retorna todas las mediciones registradas en la serie temporal para un cerdo, ordenadas cronológicamente de la más reciente a la más antigua.",
)
async def get_pesajes_by_animal(
    animal_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[Usuario] = Depends(get_current_user_optional),
) -> List[PesajeResponse]:
    animal = await db.get(Animal, animal_id)
    if not animal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No se encontró el animal con ID {animal_id}.",
        )

    query = (
        select(RegistroPeso)
        .options(selectinload(RegistroPeso.animal), selectinload(RegistroPeso.corral))
        .where(RegistroPeso.id_cerdo == animal_id)
        .order_by(desc(RegistroPeso.tiempo))
    )
    result = await db.execute(query)
    registros = result.scalars().all()

    response_list: List[PesajeResponse] = []
    for reg in registros:
        response_list.append(
            PesajeResponse(
                id_registro=reg.id_registro,
                tiempo=reg.tiempo,
                id_cerdo=reg.id_cerdo,
                corral_id=reg.corral_id,
                peso_kg=reg.peso_kg,
                metodo=reg.metodo,
                confianza_ia=reg.confianza_ia,
                area_cm2=reg.area_cm2,
                largo_cm=reg.largo_cm,
                ancho_cm=reg.ancho_cm,
                bbox=reg.bbox,
                foto_evidencia_url=reg.foto_evidencia_url,
                sync_status=reg.sync_status,
                created_at=reg.created_at,
                cerdo_alias=reg.animal.nombre_alias if reg.animal else None,
                cerdo_arete=reg.animal.codigo_arete if reg.animal else None,
                corral_codigo=reg.corral.codigo if reg.corral else None,
            )
        )

    return response_list


@router.get(
    "/corral/{corral_id}/gmd",
    response_model=List[GMDDiariaItem],
    summary="Serie temporal de GMD por corral",
    description="Consulta la vista continua de TimescaleDB con la evolución diaria de peso y Ganancia Media Diaria.",
)
async def get_corral_gmd(
    corral_id: uuid.UUID,
    limite: int = Query(30, ge=1, le=365, description="Número máximo de días a consultar"),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[Usuario] = Depends(get_current_user_optional),
) -> List[GMDDiariaItem]:
    # 1. Consulta primaria sobre la vista continua de TimescaleDB
    query = text(
        """
        SELECT fecha, corral_id, total_pesajes, peso_promedio_kg, peso_minimo_kg, peso_maximo_kg, gmd_kg, variacion_porcentual
        FROM vista_gmd_diaria_corral
        WHERE corral_id = :corral_id
        ORDER BY fecha DESC
        LIMIT :limite;
        """
    )
    result = await db.execute(query, {"corral_id": str(corral_id), "limite": limite})
    rows = result.mappings().all()

    # 2. Si la vista analítica no tiene datos aún (por ventana de agregación), fallback dinámico sobre registro_pesos
    if not rows:
        fallback_query = text(
            """
            WITH diarios AS (
                SELECT 
                    date_trunc('day', tiempo) AS fecha,
                    corral_id,
                    COUNT(*)::bigint AS total_pesajes,
                    ROUND(AVG(peso_kg)::numeric, 3) AS peso_promedio_kg,
                    ROUND(MIN(peso_kg)::numeric, 2) AS peso_minimo_kg,
                    ROUND(MAX(peso_kg)::numeric, 2) AS peso_maximo_kg
                FROM registro_pesos
                WHERE corral_id = :corral_id
                GROUP BY date_trunc('day', tiempo), corral_id
            ),
            con_gmd AS (
                SELECT 
                    fecha,
                    corral_id,
                    total_pesajes,
                    peso_promedio_kg,
                    peso_minimo_kg,
                    peso_maximo_kg,
                    ROUND((peso_promedio_kg - LAG(peso_promedio_kg) OVER (ORDER BY fecha))::numeric, 3) AS gmd_kg,
                    CASE 
                        WHEN LAG(peso_promedio_kg) OVER (ORDER BY fecha) > 0 
                        THEN ROUND(((peso_promedio_kg - LAG(peso_promedio_kg) OVER (ORDER BY fecha)) / LAG(peso_promedio_kg) OVER (ORDER BY fecha) * 100)::numeric, 2)
                        ELSE 0.00
                    END AS variacion_porcentual
                FROM diarios
            )
            SELECT fecha, corral_id, total_pesajes, peso_promedio_kg, peso_minimo_kg, peso_maximo_kg, gmd_kg, variacion_porcentual
            FROM con_gmd
            ORDER BY fecha DESC
            LIMIT :limite;
            """
        )
        fallback_res = await db.execute(fallback_query, {"corral_id": str(corral_id), "limite": limite})
        rows = fallback_res.mappings().all()

    return [
        GMDDiariaItem(
            fecha=r["fecha"],
            corral_id=r["corral_id"],
            total_pesajes=int(r["total_pesajes"]),
            peso_promedio_kg=float(r["peso_promedio_kg"]) if r["peso_promedio_kg"] is not None else 0.0,
            peso_minimo_kg=float(r["peso_minimo_kg"]) if r["peso_minimo_kg"] is not None else 0.0,
            peso_maximo_kg=float(r["peso_maximo_kg"]) if r["peso_maximo_kg"] is not None else 0.0,
            gmd_kg=float(r["gmd_kg"]) if r["gmd_kg"] is not None else None,
            variacion_porcentual=float(r["variacion_porcentual"]) if r["variacion_porcentual"] is not None else None,
        )
        for r in rows
    ]
