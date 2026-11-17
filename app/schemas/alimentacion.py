from datetime import datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class AlimentacionRacionCreate(BaseModel):
    corral_id: UUID
    alimento_item_id: UUID
    fase_alimentacion: str = Field(..., description="'Pre-iniciador' | 'Iniciador' | 'Levante' | 'Ceba'")
    cantidad_kg: float = Field(..., gt=0, description="Cantidad en kilogramos")
    fecha_suministro: Optional[datetime] = None
    observaciones: Optional[str] = None


class AlimentacionRacionResponse(BaseModel):
    id: UUID
    corral_id: UUID
    corral_codigo: Optional[str] = None
    alimento_item_id: UUID
    alimento_nombre: Optional[str] = None
    operario_id: Optional[UUID] = None
    operario_nombre: Optional[str] = None
    fase_alimentacion: str
    cantidad_kg: float
    costo_total: Optional[float] = 0.0
    fecha_suministro: datetime
    observaciones: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class AlimentacionPlanCreate(BaseModel):
    nombre: str = Field(..., min_length=2, max_length=120)
    fase_alimentacion: str = Field(
        ...,
        description="Pre-iniciador | Iniciador | Levante | Ceba | Gestación | Lactancia",
    )
    corral_id: Optional[UUID] = None
    alimento_item_id: UUID
    cantidad_kg_por_animal: float = Field(..., gt=0, le=50)
    frecuencia_diaria: int = Field(2, ge=1, le=12)
    fcr_objetivo: Optional[float] = Field(2.4, gt=0, le=15)
    activo: bool = True
    observaciones: Optional[str] = None


class AlimentacionPlanUpdate(BaseModel):
    nombre: Optional[str] = Field(None, min_length=2, max_length=120)
    fase_alimentacion: Optional[str] = None
    corral_id: Optional[UUID] = None
    alimento_item_id: Optional[UUID] = None
    cantidad_kg_por_animal: Optional[float] = Field(None, gt=0, le=50)
    frecuencia_diaria: Optional[int] = Field(None, ge=1, le=12)
    fcr_objetivo: Optional[float] = Field(None, gt=0, le=15)
    activo: Optional[bool] = None
    observaciones: Optional[str] = None


class AlimentacionPlanResponse(BaseModel):
    id: UUID
    nombre: str
    fase_alimentacion: str
    corral_id: Optional[UUID] = None
    corral_codigo: Optional[str] = None
    alimento_item_id: UUID
    alimento_nombre: Optional[str] = None
    cantidad_kg_por_animal: float
    frecuencia_diaria: int
    fcr_objetivo: Optional[float] = None
    activo: bool
    creado_por_nombre: Optional[str] = None
    observaciones: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ConversionAlimenticiaItem(BaseModel):
    corral_id: UUID
    corral_codigo: str
    fase: str
    animales_activos: int
    alimento_suministrado_kg: float
    costo_alimento_cop: float
    peso_inicial_prom_kg: Optional[float] = None
    peso_final_prom_kg: Optional[float] = None
    ganancia_total_estimada_kg: float
    fcr_real: Optional[float] = None
    fcr_objetivo_estandar: float
    desviacion_porcentual: Optional[float] = None
    estado_fcr: str
    observacion_zootecnica: str
