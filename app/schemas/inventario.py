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
    factor_conversion_kg: Optional[float] = 1.0
    stock_actual: Optional[float] = 0.0
    stock_minimo: float = 0.0
    costo_unitario: float = 0.0
    ubicacion_bodega: Optional[str] = None


class InventarioItemCreate(InventarioItemBase):
    """Alta de insumo. Si `stock_actual > 0` se registra también su lote inicial y la entrada en Kardex."""

    codigo_sku: Optional[str] = None
    numero_lote: Optional[str] = Field(None, max_length=100)
    fecha_vencimiento: Optional[date] = None
    registro_ica: Optional[str] = Field(None, max_length=60)


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
    dias_para_vencer: Optional[int] = None
    estado_vencimiento: Optional[str] = None  # 'vencido' | 'proximo_vencer' | 'vigente' | 'sin_fecha'
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AlertaVencimientoLoteResponse(BaseModel):
    id: UUID
    item_id: UUID
    item_nombre: str
    codigo_sku: str
    categoria_nombre: Optional[str] = None
    numero_lote: str
    fecha_vencimiento: date
    dias_para_vencer: int
    cantidad_disponible: float
    unidad_medida: str
    registro_ica: Optional[str] = None
    estado: str  # 'vencido' | 'proximo_vencer'


class InventarioItemResponse(BaseModel):
    id: UUID
    categoria_id: UUID
    categoria_codigo: Optional[str] = None
    categoria_nombre: Optional[str] = None
    codigo_sku: str
    nombre: str
    unidad_medida: str
    factor_conversion_kg: float = 1.0
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
    # Solo para 'entrada_compra': crea o incrementa el lote indicado
    numero_lote: Optional[str] = Field(None, max_length=100)
    fecha_vencimiento: Optional[date] = None
    registro_ica: Optional[str] = Field(None, max_length=60)


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
