import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.animal import Animal
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


class ProtocoloBioseguridad(Base):
    __tablename__ = "protocolos_bioseguridad"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    codigo: Mapped[str] = mapped_column(String(30), unique=True, nullable=False, index=True)
    tipo_protocolo: Mapped[str] = mapped_column(String(30), nullable=False)
    tarea: Mapped[str] = mapped_column(String(120), nullable=False)
    descripcion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    icono: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    frecuencia: Mapped[str] = mapped_column(String(50), nullable=False, default="diaria")
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
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

    ejecuciones: Mapped[List["EjecucionBioseguridad"]] = relationship(
        "EjecucionBioseguridad",
        back_populates="protocolo",
        cascade="all, delete-orphan",
    )


class EjecucionBioseguridad(Base):
    __tablename__ = "ejecucion_bioseguridad"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    protocolo_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("protocolos_bioseguridad.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    corral_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("corrales.id", ondelete="SET NULL"),
        nullable=True,
    )
    operario_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="RESTRICT"),
        nullable=False,
    )
    fecha_ejecucion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.clock_timestamp(),
    )
    cumplido: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
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

    protocolo: Mapped[Optional["ProtocoloBioseguridad"]] = relationship("ProtocoloBioseguridad", back_populates="ejecuciones", lazy="joined")
    operario: Mapped[Optional["Usuario"]] = relationship("Usuario", lazy="joined")
    corral: Mapped[Optional["Corral"]] = relationship("Corral", lazy="joined")


class SanidadTratamiento(Base):
    __tablename__ = "sanidad_tratamientos"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    animal_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("animales.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    corral_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("corrales.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    veterinario_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="RESTRICT"),
        nullable=False,
    )
    medicamento_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("inventario_items.id", ondelete="RESTRICT"),
        nullable=True,
    )
    tipo_evento: Mapped[str] = mapped_column(String(50), nullable=False)
    producto_nombre: Mapped[str] = mapped_column(String(120), nullable=False)
    diagnostico: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    dosis: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 2), nullable=True)
    unidad_dosis: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    via_administracion: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    tiempo_retiro_dias: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fecha_tratamiento: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.clock_timestamp(),
    )
    fecha_proxima_dosis: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
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

    animal: Mapped[Optional["Animal"]] = relationship("Animal", lazy="joined")
    corral: Mapped[Optional["Corral"]] = relationship("Corral", lazy="joined")
    veterinario: Mapped[Optional["Usuario"]] = relationship("Usuario", lazy="joined")
    medicamento: Mapped[Optional["InventarioItem"]] = relationship("InventarioItem", lazy="joined")
