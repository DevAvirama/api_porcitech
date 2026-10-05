from datetime import datetime
from typing import Optional, Dict, Any
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class BoundingBoxSchema(BaseModel):
    x_min: int = Field(..., description="Coordenada X mínima del recuadro")
    y_min: int = Field(..., description="Coordenada Y mínima del recuadro")
    x_max: int = Field(..., description="Coordenada X máxima del recuadro")
    y_max: int = Field(..., description="Coordenada Y máxima del recuadro")


class InferenciaVisionResponse(BaseModel):
    detectado: bool = Field(..., description="Indica si se detectó el cerdo en la imagen")
    confianza: Optional[float] = Field(None, description="Nivel de certeza de la detección (0.0 a 1.0)")
    peso_estimado_kg: Optional[float] = Field(None, description="Peso estimado en kg mediante alometría dorsal")
    area_cm2: Optional[float] = Field(None, description="Área superficial dorsal calculada en cm²")
    largo_cm: Optional[float] = Field(None, description="Longitud cabeza-cola en cm")
    ancho_cm: Optional[float] = Field(None, description="Ancho torácico dorsal en cm")
    bbox: Optional[BoundingBoxSchema] = Field(None, description="Recuadro delimitador en píxeles")
    mensaje: Optional[str] = Field(None, description="Mensaje explicativo o estado de inferencia")
    id_registro_persistido: Optional[UUID] = Field(None, description="UUID del registro generado en TimescaleDB si persistió")
    tiempo_registro: Optional[datetime] = Field(None, description="Marca de tiempo asignada al pesaje")


class PesajeManualCreate(BaseModel):
    id_cerdo: UUID = Field(..., description="UUID del cerdo pesado")
    corral_id: UUID = Field(..., description="UUID del corral actual del cerdo")
    peso_kg: float = Field(..., gt=0, description="Peso registrado en kilogramos")
    metodo: Optional[str] = Field("manual", description="Método utilizado ('manual' o 'bascula_digital')")
    tiempo: Optional[datetime] = Field(None, description="Marca de tiempo del pesaje (opcional, por omisión hora actual)")
    observaciones: Optional[str] = Field(None, description="Observaciones adicionales")


class PesajeResponse(BaseModel):
    id_registro: UUID
    tiempo: datetime
    id_cerdo: UUID
    corral_id: UUID
    peso_kg: float
    metodo: str
    confianza_ia: Optional[float] = None
    area_cm2: Optional[float] = None
    largo_cm: Optional[float] = None
    ancho_cm: Optional[float] = None
    bbox: Optional[Dict[str, Any]] = None
    foto_evidencia_url: Optional[str] = None
    sync_status: str
    created_at: datetime
    cerdo_alias: Optional[str] = None
    cerdo_arete: Optional[str] = None
    corral_codigo: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class GMDDiariaItem(BaseModel):
    fecha: datetime = Field(..., description="Fecha de agregación del pesaje")
    corral_id: UUID = Field(..., description="UUID del corral asociado")
    total_pesajes: int = Field(..., description="Cantidad total de pesajes en la fecha")
    peso_promedio_kg: float = Field(..., description="Peso promedio de los cerdos en kg")
    peso_minimo_kg: float = Field(..., description="Peso mínimo registrado en kg")
    peso_maximo_kg: float = Field(..., description="Peso máximo registrado en kg")
    gmd_kg: Optional[float] = Field(None, description="Ganancia media diaria en kg respecto al día anterior")
    variacion_porcentual: Optional[float] = Field(None, description="Variación porcentual de peso respecto al día anterior")

    model_config = ConfigDict(from_attributes=True)
