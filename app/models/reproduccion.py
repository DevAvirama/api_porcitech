import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional
from sqlalchemy import (
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


class ReproduccionServicio(Base):
    __tablename__ = "reproduccion_servicios"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    hembra_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("animales.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    macho_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("animales.id", ondelete="SET NULL"),
        nullable=True,
    )
    tecnico_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="RESTRICT"),
        nullable=False,
    )
    tipo_servicio: Mapped[str] = mapped_column(String(30), nullable=False)
    codigo_pajilla_macho: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    fecha_servicio: Mapped[date] = mapped_column(Date, nullable=False)
    fecha_probable_parto: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
        server_default=func.now(),
    )
    estado_confirmacion: Mapped[str] = mapped_column(String(30), nullable=False, default="pendiente")
    fecha_diagnostico: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
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

    hembra: Mapped[Optional["Animal"]] = relationship("Animal", foreign_keys=[hembra_id], lazy="joined")
    macho: Mapped[Optional["Animal"]] = relationship("Animal", foreign_keys=[macho_id], lazy="joined")
    tecnico: Mapped[Optional["Usuario"]] = relationship("Usuario", lazy="joined")
    parto: Mapped[Optional["ReproduccionParto"]] = relationship("ReproduccionParto", back_populates="servicio", uselist=False)


class ReproduccionParto(Base):
    __tablename__ = "reproduccion_partos"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    servicio_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("reproduccion_servicios.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    hembra_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("animales.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    corral_maternidad_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("corrales.id", ondelete="SET NULL"),
        nullable=True,
    )
    atendido_por: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="RESTRICT"),
        nullable=False,
    )
    fecha_parto: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.clock_timestamp(),
    )
    nacidos_vivos: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    nacidos_muertos: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    momias: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    peso_camada_total_kg: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False, default=Decimal("0.00"))
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

    servicio: Mapped[Optional["ReproduccionServicio"]] = relationship("ReproduccionServicio", back_populates="parto", lazy="joined")
    hembra: Mapped[Optional["Animal"]] = relationship("Animal", lazy="joined")
    corral_maternidad: Mapped[Optional["Corral"]] = relationship("Corral", lazy="joined")
    atendido_por_usuario: Mapped[Optional["Usuario"]] = relationship("Usuario", lazy="joined")
    destete: Mapped[Optional["ReproduccionDestete"]] = relationship("ReproduccionDestete", back_populates="parto", uselist=False)


class ReproduccionDestete(Base):
    __tablename__ = "reproduccion_destetes"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    parto_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("reproduccion_partos.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    hembra_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("animales.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    corral_destino_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("corrales.id", ondelete="SET NULL"),
        nullable=True,
    )
    operario_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="RESTRICT"),
        nullable=False,
    )
    fecha_destete: Mapped[date] = mapped_column(Date, nullable=False, default=date.today)
    lechones_destetados: Mapped[int] = mapped_column(Integer, nullable=False)
    peso_total_kg: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    dias_lactancia: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
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

    parto: Mapped[Optional["ReproduccionParto"]] = relationship("ReproduccionParto", back_populates="destete", lazy="joined")
    hembra: Mapped[Optional["Animal"]] = relationship("Animal", lazy="joined")
    corral_destino: Mapped[Optional["Corral"]] = relationship("Corral", lazy="joined")
    operario: Mapped[Optional["Usuario"]] = relationship("Usuario", lazy="joined")
