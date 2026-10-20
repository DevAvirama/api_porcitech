import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from sqlalchemy import delete, desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.api.deps import get_current_user_optional, get_db, resolve_user_or_fallback
from app.models.animal import Animal
from app.models.corral import Corral
from app.models.pesaje import RegistroPeso
from app.models.user import Usuario
from app.schemas.pesaje import (
    GMDDiariaItem,
    PesajeCamaraRTSPRequest,
    PesajeDeleteResponse,
    PesajeManualCreate,
    PesajeResponse,
    PesajeVisionResponse,
)
from app.vision.pipeline import (
    PESO_MAXIMO_FISIOLOGICO_KG,
    decodificar_bytes_imagen_sync,
    ejecutar_inferencia_morfometrica_sync,
)
from app.vision.rtsp_client import capturar_fotograma_v380_sync

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Utilidades internas de visión artificial
# ---------------------------------------------------------------------------
def _obtener_modelo(request: Request) -> Any:
    model = getattr(request.app.state, "model", None) or getattr(request.app.state, "yolo_model", None)
    if model is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El modelo YOLO no está cargado en el servidor (modo contingencia).",
        )
    return model


async def _persistir_pesaje_vision(
    db: AsyncSession,
    id_cerdo: uuid.UUID,
    corral_id: Optional[uuid.UUID],
    resultado: Dict[str, Any],
) -> Dict[str, Any]:
    peso = float(resultado["peso_estimado_kg"])
    if peso <= 0 or peso > PESO_MAXIMO_FISIOLOGICO_KG:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Peso estimado fuera de rango fisiológico ({peso} kg). No se persistió. "
                "Revise el factor de calibración pixels_per_cm o la altura de la cámara."
            ),
        )

    animal = await db.get(Animal, id_cerdo)
    if not animal or animal.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No se encontró el animal con ID {id_cerdo}.")

    target_corral_id = corral_id or animal.corral_id
    corral = await db.get(Corral, target_corral_id) if target_corral_id else None
    if not corral or corral.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No se encontró el corral con ID {target_corral_id}.")

    tiempo = datetime.now(timezone.utc)
    nuevo_id = uuid.uuid4()
    db.add(
        RegistroPeso(
            tiempo=tiempo,
            id_registro=nuevo_id,
            id_cerdo=id_cerdo,
            corral_id=target_corral_id,
            peso_kg=peso,
            metodo="vision_ai",
            confianza_ia=resultado["confianza"],
            area_cm2=resultado["area_cm2"],
            largo_cm=resultado["largo_cm"],
            ancho_cm=resultado["ancho_cm"],
            bbox=resultado["bbox"],
            sync_status="synced",
        )
    )
    animal.peso_actual_kg = Decimal(str(round(peso, 2)))
    await db.commit()
    return {"id_registro": nuevo_id, "tiempo": tiempo, "peso_actual_kg": round(peso, 2)}


def _construir_respuesta_vision(
    resultado: Dict[str, Any], fuente: str, persistencia: Optional[Dict[str, Any]]
) -> PesajeVisionResponse:
    return PesajeVisionResponse(
        detectado=True,
        fuente=fuente,
        peso_estimado_kg=resultado["peso_estimado_kg"],
        confianza=resultado["confianza"],
        area_cm2=resultado["area_cm2"],
        largo_cm=resultado["largo_cm"],
        ancho_cm=resultado["ancho_cm"],
        bbox=resultado["bbox"],
        imagen_anotada_base64=resultado["imagen_anotada_base64"],
        persistido=persistencia is not None,
        id_registro_persistido=persistencia["id_registro"] if persistencia else None,
        tiempo_registro=persistencia["tiempo"] if persistencia else None,
        peso_actual_kg=persistencia["peso_actual_kg"] if persistencia else None,
        mensaje="Detección dorsal y pesaje morfométrico exitoso.",
    )


MAX_IMAGE_UPLOAD_BYTES = 15 * 1024 * 1024  # 15 MB


@router.post(
    "/vision",
    response_model=PesajeVisionResponse,
    summary="Estimar peso desde fotografía (UploadFile)",
    description="Decodifica la imagen en RAM, ejecuta YOLO con escala normalizada a 1280 px y persiste en registro_pesos.",
)
async def estimar_peso_vision(
    request: Request,
    file: UploadFile = File(..., description="Fotografía cenital / dorsal del cerdo"),
    id_cerdo: Optional[uuid.UUID] = Form(None),
    corral_id: Optional[uuid.UUID] = Form(None),
    pixels_per_cm: Optional[float] = Form(None),
    persistir: bool = Form(True),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[Usuario] = Depends(get_current_user_optional),
) -> PesajeVisionResponse:
    await resolve_user_or_fallback(db, current_user, role="operario")

    if pixels_per_cm is not None and not (1.0 < pixels_per_cm <= 100.0):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="pixels_per_cm debe estar en (1, 100].")

    model = _obtener_modelo(request)
    contenido = await file.read(MAX_IMAGE_UPLOAD_BYTES + 1)
    if not contenido:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El archivo de imagen está vacío.")
    if len(contenido) > MAX_IMAGE_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="La imagen supera el tamaño máximo permitido (15 MB).",
        )

    yolo_lock: Optional[asyncio.Lock] = getattr(request.app.state, "yolo_lock", None)
    try:
        img_bgr = await asyncio.to_thread(decodificar_bytes_imagen_sync, contenido)
        if yolo_lock is not None:
            async with yolo_lock:
                resultado = await asyncio.to_thread(
                    ejecutar_inferencia_morfometrica_sync, model, img_bgr, pixels_per_cm
                )
        else:
            resultado = await asyncio.to_thread(
                ejecutar_inferencia_morfometrica_sync, model, img_bgr, pixels_per_cm
            )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    persistencia = None
    if persistir and id_cerdo:
        persistencia = await _persistir_pesaje_vision(db, id_cerdo, corral_id, resultado)

    return _construir_respuesta_vision(resultado, "archivo", persistencia)


@router.post(
    "/camara-rtsp",
    response_model=PesajeVisionResponse,
    summary="Capturar y estimar peso desde cámara cenital V380 Pro (RTSP/TCP)",
    description="Drena el buffer RTSP, captura el fotograma en vivo y ejecuta la inferencia sin bloquear el event loop.",
)
async def estimar_peso_camara_rtsp(
    request: Request,
    payload: PesajeCamaraRTSPRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[Usuario] = Depends(get_current_user_optional),
) -> PesajeVisionResponse:
    await resolve_user_or_fallback(db, current_user, role="operario")

    rtsp_url = (payload.rtsp_url or os.getenv("V380_RTSP_URL") or "").strip()
    if not rtsp_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No se indicó rtsp_url ni está definida la variable de entorno V380_RTSP_URL.",
        )
    if not rtsp_url.lower().startswith(("rtsp://", "rtsps://")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La URL de la cámara debe usar estrictamente el protocolo rtsp:// o rtsps://.",
        )

    model = _obtener_modelo(request)
    try:
        frame_bgr = await asyncio.to_thread(capturar_fotograma_v380_sync, rtsp_url)
    except (ConnectionError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))

    yolo_lock: Optional[asyncio.Lock] = getattr(request.app.state, "yolo_lock", None)
    try:
        if yolo_lock is not None:
            async with yolo_lock:
                resultado = await asyncio.to_thread(
                    ejecutar_inferencia_morfometrica_sync,
                    model,
                    frame_bgr,
                    payload.pixels_per_cm,
                    0.45,
                    payload.validar_bordes,
                )
        else:
            resultado = await asyncio.to_thread(
                ejecutar_inferencia_morfometrica_sync,
                model,
                frame_bgr,
                payload.pixels_per_cm,
                0.45,
                payload.validar_bordes,
            )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    persistencia = None
    if payload.persistir:
        persistencia = await _persistir_pesaje_vision(db, payload.id_cerdo, payload.corral_id, resultado)

    return _construir_respuesta_vision(resultado, "camara_rtsp", persistencia)


@router.delete(
    "/{id_registro}",
    response_model=PesajeDeleteResponse,
    summary="Anular un pesaje anómalo",
    description="Elimina la fila de registro_pesos y recalcula animales.peso_actual_kg con el pesaje restante más reciente.",
)
async def delete_pesaje(
    id_registro: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[Usuario] = Depends(get_current_user_optional),
) -> PesajeDeleteResponse:
    await resolve_user_or_fallback(db, current_user, role="operario")

    result = await db.execute(select(RegistroPeso).where(RegistroPeso.id_registro == id_registro).limit(1))
    pesaje = result.scalars().first()
    if not pesaje:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No existe el pesaje {id_registro}.")

    id_cerdo = pesaje.id_cerdo
    await db.execute(
        delete(RegistroPeso).where(
            RegistroPeso.id_registro == id_registro,
            RegistroPeso.tiempo == pesaje.tiempo,
        )
    )

    ultimo = await db.execute(
        select(RegistroPeso.peso_kg)
        .where(RegistroPeso.id_cerdo == id_cerdo)
        .order_by(desc(RegistroPeso.tiempo))
        .limit(1)
    )
    peso_restante = ultimo.scalar_one_or_none()
    nuevo_peso = round(float(peso_restante), 2) if peso_restante is not None else 0.00

    animal = await db.get(Animal, id_cerdo)
    if animal:
        animal.peso_actual_kg = Decimal(str(nuevo_peso))

    await db.commit()
    return PesajeDeleteResponse(
        id_registro=id_registro,
        id_cerdo=id_cerdo,
        peso_actual_kg=nuevo_peso,
        mensaje="Pesaje anulado y peso actual recalculado.",
    )


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
    await resolve_user_or_fallback(db, current_user, role="operario")

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
        observaciones=pesaje_in.observaciones,
        sync_status="synced",
    )
    db.add(nuevo_registro)

    # 4. Actualización en tabla animales
    animal.peso_actual_kg = Decimal(str(round(pesaje_in.peso_kg, 2)))

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
        observaciones=pesaje_in.observaciones,
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
    limit: Optional[int] = Query(100, ge=1, le=500, description="Número máximo de registros a retornar"),
    offset: int = Query(0, ge=0, description="Desplazamiento para paginación"),
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
        .options(joinedload(RegistroPeso.corral))
        .where(RegistroPeso.id_cerdo == animal_id)
        .order_by(desc(RegistroPeso.tiempo))
    )

    if isinstance(limit, int):
        query = query.limit(limit)
    if isinstance(offset, int) and offset > 0:
        query = query.offset(offset)

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
                observaciones=reg.observaciones,
                sync_status=reg.sync_status,
                created_at=reg.created_at,
                cerdo_alias=animal.nombre_alias,
                cerdo_arete=animal.codigo_arete,
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
                    CASE
                        WHEN LAG(peso_promedio_kg) OVER (ORDER BY fecha) IS NOT NULL
                             AND LAG(fecha) OVER (ORDER BY fecha) IS NOT NULL
                             AND EXTRACT(EPOCH FROM (fecha - LAG(fecha) OVER (ORDER BY fecha))) > 0
                        THEN ROUND(
                            ((peso_promedio_kg - LAG(peso_promedio_kg) OVER (ORDER BY fecha))
                            / GREATEST(1::numeric, (EXTRACT(EPOCH FROM (fecha - LAG(fecha) OVER (ORDER BY fecha))) / 86400.0)::numeric))::numeric,
                            3
                        )
                        ELSE NULL
                    END AS gmd_kg,
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
