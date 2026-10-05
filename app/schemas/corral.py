import uuid
from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class CorralBase(BaseModel):
    codigo: str = Field(..., min_length=1, max_length=50, description="Código único identificador del corral")
    descripcion: Optional[str] = Field(None, description="Descripción funcional o ubicación del corral")
    fase: str = Field(..., description="Fase productiva: 'maternidad', 'precebo', 'levante', 'ceba', 'cuarentena', 'gestacion'")
    capacidad_maxima: int = Field(..., gt=0, description="Capacidad máxima de animales")
    activo: bool = Field(True, description="Estado operativo del corral")


class CorralCreate(CorralBase):
    fecha_inicio: Optional[date] = Field(None, description="Fecha de asignación o inicio de lote")


class CorralUpdate(BaseModel):
    codigo: Optional[str] = Field(None, min_length=1, max_length=50)
    descripcion: Optional[str] = None
    fase: Optional[str] = None
    capacidad_maxima: Optional[int] = Field(None, gt=0)
    fecha_inicio: Optional[date] = None
    fecha_cierre: Optional[date] = None
    activo: Optional[bool] = None


class CorralResponse(CorralBase):
    id: uuid.UUID = Field(..., description="Identificador único UUID del corral")
    fecha_inicio: date = Field(..., description="Fecha de inicio")
    fecha_cierre: Optional[date] = Field(None, description="Fecha de cierre proyectada o ejecutada")
    created_at: datetime = Field(..., description="Fecha de creación del registro")

    model_config = ConfigDict(from_attributes=True)
