import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import Boolean, Date, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.animal import Animal

tipo_fase_corral_enum = PG_ENUM(
    "maternidad",
    "precebo",
    "levante",
    "ceba",
    "cuarentena",
    "gestacion",
    name="tipo_fase_corral_enum",
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


class Corral(Base):
    __tablename__ = "corrales"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    codigo: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        nullable=False,
        index=True,
    )
    descripcion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    fase: Mapped[str] = mapped_column(
        tipo_fase_corral_enum,
        nullable=False,
    )
    capacidad_maxima: Mapped[int] = mapped_column(Integer, nullable=False)
    fecha_inicio: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        default=date.today,
    )
    fecha_cierre: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
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
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.clock_timestamp(),
        onupdate=func.clock_timestamp(),
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relación bidireccional con animales en el corral
    animales: Mapped[List["Animal"]] = relationship(
        "Animal",
        back_populates="corral",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Corral(id={self.id}, codigo='{self.codigo}', fase='{self.fase}', activo={self.activo})>"
