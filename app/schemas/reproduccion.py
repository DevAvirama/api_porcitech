from datetime import date, datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class ReproduccionServicioBase(BaseModel):
    hembra_id: UUID
    macho_id: Optional[UUID] = None
    tipo_servicio: str = Field(..., description="'inseminacion_artificial' | 'monta_natural'")
    codigo_pajilla_macho: Optional[str] = None
    fecha_servicio: date
    estado_confirmacion: Optional[str] = "pendiente"
    fecha_diagnostico: Optional[date] = None


class ReproduccionServicioCreate(ReproduccionServicioBase):
    pass


class ServicioUpdate(BaseModel):
    estado_confirmacion: str = Field(..., description="'pendiente', 'positiva', 'negativa', 'repetida'")
    fecha_diagnostico: Optional[date] = None
    observaciones: Optional[str] = None


class ReproduccionServicioResponse(BaseModel):
    id: UUID
    hembra_id: UUID
    hembra_arete: Optional[str] = None
    hembra_alias: Optional[str] = None
    macho_id: Optional[UUID] = None
    macho_arete: Optional[str] = None
    tecnico_id: UUID
    tecnico_nombre: Optional[str] = None
    tipo_servicio: str
    codigo_pajilla_macho: Optional[str] = None
    fecha_servicio: date
    fecha_probable_parto: Optional[date] = None
    estado_confirmacion: str
    fecha_diagnostico: Optional[date] = None
    sync_status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ReproduccionPartoBase(BaseModel):
    servicio_id: Optional[UUID] = None
    hembra_id: UUID
    corral_maternidad_id: Optional[UUID] = None
    fecha_parto: Optional[datetime] = None
    nacidos_vivos: int = Field(default=0, ge=0)
    nacidos_muertos: int = Field(default=0, ge=0)
    momias: int = Field(default=0, ge=0)
    peso_camada_total_kg: float = Field(default=0.0, ge=0)
    observaciones: Optional[str] = None


class ReproduccionPartoCreate(ReproduccionPartoBase):
    pass


class ReproduccionPartoResponse(BaseModel):
    id: UUID
    servicio_id: Optional[UUID] = None
    hembra_id: UUID
    hembra_arete: Optional[str] = None
    hembra_alias: Optional[str] = None
    corral_maternidad_id: Optional[UUID] = None
    corral_maternidad_codigo: Optional[str] = None
    atendido_por: UUID
    atendido_por_nombre: Optional[str] = None
    fecha_parto: datetime
    nacidos_vivos: int
    nacidos_muertos: int
    momias: int
    peso_camada_total_kg: float
    observaciones: Optional[str] = None
    sync_status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ReproduccionDesteteBase(BaseModel):
    parto_id: Optional[UUID] = None
    hembra_id: UUID
    corral_destino_id: Optional[UUID] = None
    fecha_destete: Optional[date] = None
    lechones_destetados: int = Field(..., ge=0)
    peso_total_kg: float = Field(..., ge=0)
    dias_lactancia: Optional[int] = Field(default=None, ge=0)


class ReproduccionDesteteCreate(ReproduccionDesteteBase):
    pass


class ReproduccionDesteteResponse(BaseModel):
    id: UUID
    parto_id: Optional[UUID] = None
    hembra_id: UUID
    hembra_arete: Optional[str] = None
    hembra_alias: Optional[str] = None
    corral_destino_id: Optional[UUID] = None
    corral_destino_codigo: Optional[str] = None
    operario_id: UUID
    operario_nombre: Optional[str] = None
    fecha_destete: date
    lechones_destetados: int
    peso_total_kg: float
    dias_lactancia: Optional[int] = None
    sync_status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
