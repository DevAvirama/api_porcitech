import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional
from sqlalchemy import (
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
    from app.models.corral import Corral
    from app.models.inventario import InventarioItem
    from app.models.user import Usuario

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


class AlimentacionRacion(Base):
    __tablename__ = "alimentacion_raciones"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    corral_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("corrales.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    alimento_item_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("inventario_items.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    operario_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="RESTRICT"),
        nullable=False,
    )
    fase_alimentacion: Mapped[str] = mapped_column(String(50), nullable=False)
    cantidad_kg: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    fecha_suministro: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.clock_timestamp(),
    )
    costo_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    observaciones: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
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

    corral: Mapped[Optional["Corral"]] = relationship("Corral", lazy="joined")
    alimento: Mapped[Optional["InventarioItem"]] = relationship("InventarioItem", lazy="joined")
    operario: Mapped[Optional["Usuario"]] = relationship("Usuario", lazy="joined")
