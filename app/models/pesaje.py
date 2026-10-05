import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional, Any, Dict
from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM, UUID as PG_UUID, JSONB, DOUBLE_PRECISION
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.animal import Animal
    from app.models.corral import Corral

metodo_pesaje_enum = PG_ENUM(
    "manual",
    "vision_ai",
    "bascula_digital",
    name="metodo_pesaje_enum",
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


class RegistroPeso(Base):
    __tablename__ = "registro_pesos"

    tiempo: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        primary_key=True,
        nullable=False,
        default=func.clock_timestamp(),
    )
    id_registro: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        nullable=False,
        default=uuid.uuid4,
    )
    id_cerdo: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("animales.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
        index=True,
    )
    corral_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("corrales.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    peso_kg: Mapped[float] = mapped_column(
        DOUBLE_PRECISION,
        nullable=False,
    )
    metodo: Mapped[str] = mapped_column(
        metodo_pesaje_enum,
        nullable=False,
        default="vision_ai",
    )
    confianza_ia: Mapped[Optional[float]] = mapped_column(
        DOUBLE_PRECISION,
        nullable=True,
    )
    area_cm2: Mapped[Optional[float]] = mapped_column(
        DOUBLE_PRECISION,
        nullable=True,
    )
    largo_cm: Mapped[Optional[float]] = mapped_column(
        DOUBLE_PRECISION,
        nullable=True,
    )
    ancho_cm: Mapped[Optional[float]] = mapped_column(
        DOUBLE_PRECISION,
        nullable=True,
    )
    bbox: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=True,
    )
    foto_evidencia_url: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    id_local: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
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

    animal: Mapped[Optional["Animal"]] = relationship("Animal", lazy="selectin")
    corral: Mapped[Optional["Corral"]] = relationship("Corral", lazy="selectin")

    def __repr__(self) -> str:
        return f"<RegistroPeso(id={self.id_registro}, cerdo={self.id_cerdo}, peso={self.peso_kg}kg, metodo='{self.metodo}')>"
