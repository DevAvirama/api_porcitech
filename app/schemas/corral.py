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
    id_local: Optional[uuid.UUID] = Field(None, description="UUID v4 local para idempotencia offline")


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
    ocupacion_actual: int = Field(0, ge=0, description="Cantidad de animales activos actualmente en el corral")
    fecha_inicio: date = Field(..., description="Fecha de inicio")
    fecha_cierre: Optional[date] = Field(None, description="Fecha de cierre proyectada o ejecutada")
    created_at: datetime = Field(..., description="Fecha de creación del registro")

    model_config = ConfigDict(from_attributes=True)


class CorralCicloCreate(BaseModel):
    codigo_lote: str = Field(..., min_length=2, max_length=80)
    fecha_entrada: date
    fecha_salida: Optional[date] = None
    cantidad_animales: int = Field(0, ge=0)
    dias_vacio_requerido: int = Field(7, ge=0, le=90)
    desinfeccion_verificada: bool = False
    observaciones: Optional[str] = None


class CorralCicloUpdate(BaseModel):
    fecha_salida: Optional[date] = None
    cantidad_animales: Optional[int] = Field(None, ge=0)
    dias_vacio_requerido: Optional[int] = Field(None, ge=0, le=90)
    desinfeccion_verificada: Optional[bool] = None
    observaciones: Optional[str] = None


class CorralCicloResponse(BaseModel):
    id: uuid.UUID
    corral_id: uuid.UUID
    corral_codigo: Optional[str] = None
    codigo_lote: str
    fecha_entrada: date
    fecha_salida: Optional[date] = None
    cantidad_animales: int
    dias_vacio_requerido: int
    desinfeccion_verificada: bool
    observaciones: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AuditoriaVacioItem(BaseModel):
    id: uuid.UUID
    corral_id: uuid.UUID
    corral_codigo: str
    corral_fase: str
    codigo_lote: str
    lote_anterior: Optional[str] = None
    fecha_salida_lote_anterior: Optional[date] = None
    fecha_entrada: date
    fecha_salida: Optional[date] = None
    cantidad_animales: int
    dias_vacio_requerido: int
    desinfeccion_verificada: bool
    observaciones: Optional[str] = None
    dias_vacio_sanitario: Optional[int] = None
    estado_auditoria: str

