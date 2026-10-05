import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.user import Usuario

tipo_movimiento_inv_enum = PG_ENUM(
    "entrada_compra",
    "salida_consumo",
    "ajuste_merma",
    "devolucion",
    name="tipo_movimiento_inv_enum",
    create_type=False,
)

estado_sincronizacion_enum = PG_ENUM(
    "pending",
    "synced",
    "conflict",
    "error",
    "pendiente",
    "sincronizado",
    "conflicto",
    name="estado_sincronizacion_enum",
    create_type=False,
)


class InventarioCategoria(Base):
    __tablename__ = "inventario_categorias"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    codigo: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    nombre: Mapped[str] = mapped_column(String(100), nullable=False)
    color: Mapped[Optional[str]] = mapped_column(String(50), default="blue")
    descripcion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.clock_timestamp(),
    )

    items: Mapped[List["InventarioItem"]] = relationship(
        "InventarioItem",
        back_populates="categoria",
        cascade="all, delete-orphan",
    )


class InventarioItem(Base):
    __tablename__ = "inventario_items"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    categoria_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("inventario_categorias.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    codigo_sku: Mapped[str] = mapped_column(String(60), unique=True, nullable=False, index=True)
    nombre: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    unidad_medida: Mapped[str] = mapped_column(String(50), nullable=False)
    stock_actual: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal("0.00"))
    stock_minimo: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal("0.00"))
    costo_unitario: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    ubicacion_bodega: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    id_local: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    sync_status: Mapped[str] = mapped_column(
        estado_sincronizacion_enum,
        nullable=False,
        default="synced",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.clock_timestamp(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.clock_timestamp(),
        onupdate=func.clock_timestamp(),
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    categoria: Mapped[Optional["InventarioCategoria"]] = relationship(
        "InventarioCategoria",
        back_populates="items",
        lazy="joined",
    )
    lotes: Mapped[List["InventarioLote"]] = relationship(
        "InventarioLote",
        back_populates="item",
        cascade="all, delete-orphan",
    )
    movimientos: Mapped[List["MovimientoInventario"]] = relationship(
        "MovimientoInventario",
        back_populates="item",
        cascade="all, delete-orphan",
    )


class InventarioLote(Base):
    __tablename__ = "inventario_lotes"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("inventario_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    numero_lote: Mapped[str] = mapped_column(String(100), nullable=False)
    fecha_fabricacion: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    fecha_vencimiento: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    cantidad_inicial: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    cantidad_disponible: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    registro_ica: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    id_local: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    sync_status: Mapped[str] = mapped_column(
        estado_sincronizacion_enum,
        nullable=False,
        default="synced",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.clock_timestamp(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.clock_timestamp(),
        onupdate=func.clock_timestamp(),
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    item: Mapped[Optional["InventarioItem"]] = relationship(
        "InventarioItem",
        back_populates="lotes",
    )


class MovimientoInventario(Base):
    __tablename__ = "movimientos_inventario"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("inventario_items.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    lote_inventario_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("inventario_lotes.id", ondelete="SET NULL"),
        nullable=True,
    )
    tipo_movimiento: Mapped[str] = mapped_column(tipo_movimiento_inv_enum, nullable=False)
    cantidad: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    costo_unitario: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    usuario_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="RESTRICT"),
        nullable=False,
    )
    fecha_movimiento: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.clock_timestamp(),
    )
    motivo: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    referencia_modulo: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    referencia_id: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    id_local: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    sync_status: Mapped[str] = mapped_column(
        estado_sincronizacion_enum,
        nullable=False,
        default="synced",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.clock_timestamp(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.clock_timestamp(),
        onupdate=func.clock_timestamp(),
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    item: Mapped[Optional["InventarioItem"]] = relationship("InventarioItem", back_populates="movimientos", lazy="joined")
    usuario: Mapped[Optional["Usuario"]] = relationship("Usuario", lazy="joined")
