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
