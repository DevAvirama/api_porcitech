import asyncio
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.models.animal import Animal
from app.models.corral import Corral
from app.models.pesaje import RegistroPeso
from app.schemas.pesaje import BoundingBoxSchema, InferenciaVisionResponse
from app.vision.pipeline import decode_image, process_pig_frame

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/estimate-weight",
    response_model=InferenciaVisionResponse,
    summary="Estimar peso de cerdo mediante Visión Artificial",
    description="Recibe una imagen dorsal, ejecuta inferencia no bloqueante con YOLO y opcionalmente persiste el registro en TimescaleDB actualizando el peso del animal.",
)
async def estimate_weight(
    request: Request,
    file: UploadFile = File(..., description="Fotografía en plano cenital / dorsal del cerdo"),
    id_cerdo: Optional[uuid.UUID] = Form(None, description="UUID del animal pesado"),
    corral_id: Optional[uuid.UUID] = Form(None, description="UUID del corral donde reside el cerdo"),
    persistir: bool = Form(True, description="Si es True, persiste la medición en registro_pesos y actualiza el animal"),
    db: AsyncSession = Depends(get_db),
) -> InferenciaVisionResponse:
    # 1. Validación y lectura de bytes de la imagen
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El archivo proporcionado debe ser una imagen válida (JPEG, PNG, etc.).",
        )

    try:
        image_bytes = await file.read()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error leyendo los bytes del archivo: {str(exc)}",
        )

    # 2. Decodificación binaria de imagen (OpenCV) en hilo desacoplado con asyncio.to_thread
    try:
        image_bgr = await asyncio.to_thread(decode_image, image_bytes)
    except ValueError as val_err:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(val_err),
        )

    # 3. Obtener el modelo alojado en el ciclo de vida de la aplicación
    yolo_model = getattr(request.app.state, "yolo_model", None)
    if yolo_model is None:
        return InferenciaVisionResponse(
            detectado=False,
            mensaje="El modelo de inferencia YOLO dorsal no está cargado en el servidor.",
        )

    # 4. Inferencia aislada en hilo de trabajo separado (CPU-bound) con asyncio.to_thread
    # Garantiza que el Event Loop asíncrono de FastAPI NUNCA se bloquee.
    resultado = await asyncio.to_thread(process_pig_frame, yolo_model, image_bgr)

    if not resultado.get("detectado", False):
        return InferenciaVisionResponse(
            detectado=False,
            mensaje=resultado.get("mensaje", "No se detectó ningún cerdo en el plano dorsal."),
        )

    id_registro_persistido: Optional[uuid.UUID] = None
    tiempo_registro: Optional[datetime] = None

    # 5. Persistencia condicional en TimescaleDB e hipertabla registro_pesos
    if persistir and id_cerdo:
        animal = await db.get(Animal, id_cerdo)
        if not animal:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No se encontró el animal con ID {id_cerdo}.",
            )

        target_corral_id = corral_id or animal.corral_id
        if not target_corral_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No se pudo determinar el corral para el registro de pesaje. Proporcione corral_id.",
            )

        # Verificar existencia del corral
        corral = await db.get(Corral, target_corral_id)
        if not corral:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No se encontró el corral con ID {target_corral_id}.",
            )

        tiempo_registro = datetime.now(timezone.utc)
        nuevo_id = uuid.uuid4()
        nuevo_registro = RegistroPeso(
            tiempo=tiempo_registro,
            id_registro=nuevo_id,
            id_cerdo=id_cerdo,
            corral_id=target_corral_id,
            peso_kg=resultado["peso_kg"],
            metodo="vision_ai",
            confianza_ia=resultado["confianza"],
            area_cm2=resultado["area_cm2"],
            largo_cm=resultado["largo_cm"],
            ancho_cm=resultado["ancho_cm"],
            bbox=resultado["bbox"],
            sync_status="synced",
        )
        db.add(nuevo_registro)

        # Actualización atómica en la tabla animales
        animal.peso_actual_kg = Decimal(str(resultado["peso_kg"]))
        animal.updated_at = func.clock_timestamp()

        await db.commit()
        id_registro_persistido = nuevo_id

    return InferenciaVisionResponse(
        detectado=True,
        confianza=resultado.get("confianza"),
        peso_estimado_kg=resultado.get("peso_kg"),
        area_cm2=resultado.get("area_cm2"),
        largo_cm=resultado.get("largo_cm"),
        ancho_cm=resultado.get("ancho_cm"),
        bbox=BoundingBoxSchema(**resultado["bbox"]) if resultado.get("bbox") else None,
        id_registro_persistido=id_registro_persistido,
        tiempo_registro=tiempo_registro,
        mensaje=resultado.get("mensaje", "Detección dorsal y pesaje morfométrico exitoso."),
    )
