from datetime import date, datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class ProtocoloBioseguridadResponse(BaseModel):
    id: UUID
    codigo: str
    tipo_protocolo: str
    tarea: str
    descripcion: Optional[str] = None
    icono: Optional[str] = None
    frecuencia: str
    activo: bool

    model_config = ConfigDict(from_attributes=True)


class EjecucionBioseguridadCreate(BaseModel):
    protocolo_id: UUID
    corral_id: Optional[UUID] = None
    cumplido: bool = Field(True, description="Si el protocolo fue cumplido en la verificación")
    observaciones: Optional[str] = None


class EjecucionBioseguridadResponse(BaseModel):
    id: UUID
    protocolo_id: UUID
    corral_id: Optional[UUID] = None
    fecha: str
    cumplido: bool
    observaciones: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class SanidadTratamientoCreate(BaseModel):
    animal_id: Optional[UUID] = None
    corral_id: Optional[UUID] = None
    medicamento_id: Optional[UUID] = None
    tipo_evento: str = Field(..., description="'vacuna' | 'tratamiento' | 'desparasitacion'")
    producto_nombre: str
    diagnostico: Optional[str] = None
    dosis: Optional[float] = None
    unidad_dosis: Optional[str] = None
    via_administracion: Optional[str] = None
    tiempo_retiro_dias: Optional[int] = 0
    fecha_tratamiento: Optional[datetime] = None
    fecha_proxima_dosis: Optional[date] = None
    observaciones: Optional[str] = None


class SanidadTratamientoResponse(BaseModel):
    id: UUID
    animal_id: Optional[UUID] = None
    animal_arete: Optional[str] = None
    animal_alias: Optional[str] = None
    corral_id: Optional[UUID] = None
    corral_codigo: Optional[str] = None
    veterinario_id: UUID
    veterinario_nombre: Optional[str] = None
    medicamento_id: Optional[UUID] = None
    tipo_evento: str
    producto_nombre: str
    diagnostico: Optional[str] = None
    dosis: Optional[float] = None
    unidad_dosis: Optional[str] = None
    via_administracion: Optional[str] = None
    tiempo_retiro_dias: int
    fecha_tratamiento: datetime
    fecha_proxima_dosis: Optional[date] = None
    observaciones: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class AlertaRetiroResponse(BaseModel):
    id: Optional[UUID] = None
    animal_id: UUID
    animal_arete: str
    animal_alias: Optional[str] = None
    corral_codigo: Optional[str] = None
    producto_nombre: str
    fecha_tratamiento: datetime
    tiempo_retiro_dias: int
    fecha_fin_retiro: datetime
    dias_restantes: int

    model_config = ConfigDict(from_attributes=True)
