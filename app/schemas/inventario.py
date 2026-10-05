from datetime import date, datetime
from typing import List, Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class InventarioCategoriaResponse(BaseModel):
    id: UUID
    codigo: str
    nombre: str
    color: Optional[str] = None
    descripcion: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InventarioItemBase(BaseModel):
    categoria_id: UUID
    codigo_sku: str
    nombre: str
    unidad_medida: str
    stock_actual: Optional[float] = 0.0
    stock_minimo: float = 0.0
    costo_unitario: float = 0.0
    ubicacion_bodega: Optional[str] = None


class InventarioItemCreate(InventarioItemBase):
    pass


class InventarioLoteResponse(BaseModel):
    id: UUID
    item_id: UUID
    numero_lote: str
    fecha_fabricacion: Optional[date] = None
    fecha_vencimiento: Optional[date] = None
    cantidad_inicial: float
    cantidad_actual: float = 0.0
    cantidad_disponible: Optional[float] = 0.0
    registro_ica: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InventarioItemResponse(BaseModel):
    id: UUID
    categoria_id: UUID
    categoria_codigo: Optional[str] = None
    categoria_nombre: Optional[str] = None
    codigo_sku: str
    nombre: str
    unidad_medida: str
    stock_actual: float
    stock_minimo: float
    costo_unitario: float
    ubicacion_bodega: Optional[str] = None
    sync_status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InventarioItemDetailResponse(InventarioItemResponse):
    lotes: List[InventarioLoteResponse] = []


class MovimientoInventarioCreate(BaseModel):
    item_id: UUID
    lote_id: Optional[UUID] = None
    lote_inventario_id: Optional[UUID] = None
    tipo_movimiento: str = Field(..., description="'entrada_compra' | 'salida_consumo' | 'ajuste_merma' | 'devolucion'")
    cantidad: float = Field(..., gt=0, description="Cantidad involucrada positiva")
    costo_unitario: Optional[float] = None
    motivo: Optional[str] = None


class MovimientoInventarioResponse(BaseModel):
    id: UUID
    item_id: UUID
    item_nombre: Optional[str] = None
    lote_id: Optional[UUID] = None
    tipo_movimiento: str
    cantidad: float
    costo_unitario: Optional[float] = 0.0
    costo_total: float
    fecha_movimiento: datetime
    motivo: Optional[str] = None
    usuario_id: UUID
    usuario_nombre: Optional[str] = None
    sync_status: str

    model_config = ConfigDict(from_attributes=True)
