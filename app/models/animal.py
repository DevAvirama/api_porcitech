import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional
from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.corral import Corral

sexo_animal_enum = PG_ENUM(
    "macho",
    "hembra",
    name="sexo_animal_enum",
    create_type=False,
)

estado_animal_enum = PG_ENUM(
    "activo",
    "lactante",
    "precebo",
    "levante",
    "engorde",
    "gestacion",
    "cuarentena",
    "vendido",
    "muerto",
    "enfermo",
    name="estado_animal_enum",
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


class Animal(Base):
    __tablename__ = "animales"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    codigo_arete: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        nullable=False,
        index=True,
    )
    codigo_qr: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
        index=True,
    )
    nombre_alias: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    sexo: Mapped[str] = mapped_column(sexo_animal_enum, nullable=False)
    raza: Mapped[str] = mapped_column(String(60), nullable=False)
    fecha_nacimiento: Mapped[date] = mapped_column(Date, nullable=False, default=date.today)
    estado: Mapped[str] = mapped_column(
        estado_animal_enum,
        nullable=False,
        default="activo",
    )
    corral_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("corrales.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    id_padre: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("animales.id", ondelete="SET NULL"),
        nullable=True,
    )
    id_madre: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("animales.id", ondelete="SET NULL"),
        nullable=True,
    )
    peso_actual_kg: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(6, 2),
        nullable=True,
        default=Decimal("0.00"),
    )
    foto_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
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

    # Relación con el corral asignado
    corral: Mapped[Optional["Corral"]] = relationship(
        "Corral",
        back_populates="animales",
        lazy="joined",
    )

    @property
    def corral_codigo(self) -> Optional[str]:
        """Propiedad virtual para exponer el código del corral directamente."""
        return self.corral.codigo if self.corral else None

    def __repr__(self) -> str:
        return f"<Animal(id={self.id}, arete='{self.codigo_arete}', alias='{self.nombre_alias}', corral='{self.corral_codigo}')>"
