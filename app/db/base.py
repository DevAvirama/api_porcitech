from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.orm import DeclarativeBase

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


class Base(DeclarativeBase):
    """Clase base declarativa para los modelos ORM de SQLAlchemy 2.0."""
    pass

