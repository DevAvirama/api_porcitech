import logging
import uuid
from typing import AsyncGenerator, Callable, List, Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.user import Usuario

logger = logging.getLogger(__name__)

# Esquema de autenticación Bearer para tokens JWT en cabeceras HTTP
security_scheme = HTTPBearer(auto_error=True)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security_scheme),
    db: AsyncSession = Depends(get_db),
) -> Usuario:
    """
    Dependencia que extrae y valida el token Bearer JWT, obteniendo la instancia del
    usuario activo directamente desde PostgreSQL.
    """
    token = credentials.credentials
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido o expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id_str = payload.get("sub")
    email = payload.get("email")

    if not user_id_str and not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token no contiene identidad de usuario válida",
            headers={"WWW-Authenticate": "Bearer"},
        )

    query = select(Usuario).where(Usuario.deleted_at.is_(None))
    if email:
        query = query.where(Usuario.email == email)
    else:
        try:
            user_uuid = uuid.UUID(user_id_str)
            query = query.where(Usuario.id == user_uuid)
        except ValueError:
            query = query.where(Usuario.email == user_id_str)

    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario no encontrado en el sistema",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.activo:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Usuario inactivo en el sistema",
        )

    return user


def require_roles(allowed_roles: List[str]) -> Callable:
    """
    Fábrica de dependencias para Control de Acceso Basado en Roles (RBAC).
    Ejemplo: Depends(require_roles(["administrador", "veterinario"]))
    """
    async def role_dependency(
        current_user: Usuario = Depends(get_current_user),
    ) -> Usuario:
        if current_user.rol not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permiso insuficiente. Requiere uno de los siguientes roles: {', '.join(allowed_roles)}",
            )
        return current_user

    return role_dependency


optional_security_scheme = HTTPBearer(auto_error=False)


async def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(optional_security_scheme),
    db: AsyncSession = Depends(get_db),
) -> Optional[Usuario]:
    """
    Dependencia opcional: Si se provee token Bearer, lo valida y retorna el usuario.
    Si no se envía cabecera de autorización, retorna None sin arrojar 401.
    """
    if not credentials or not credentials.credentials:
        return None

    token = credentials.credentials
    payload = decode_access_token(token)
    if not payload:
        return None

    user_id_str = payload.get("sub")
    email = payload.get("email")

    if not user_id_str and not email:
        return None

    query = select(Usuario).where(Usuario.deleted_at.is_(None))
    if email:
        query = query.where(Usuario.email == email)
    else:
        try:
            user_uuid = uuid.UUID(user_id_str)
            query = query.where(Usuario.id == user_uuid)
        except ValueError:
            query = query.where(Usuario.email == user_id_str)

    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if user and user.activo:
        return user
    return None


async def resolve_user_or_fallback(
    db: AsyncSession,
    current_user: Optional[Usuario] = None,
    role: Optional[str] = None,
) -> uuid.UUID:
    """
    Resuelve el UUID del usuario para transacciones de auditoría.
    Si hay usuario autenticado vía JWT, devuelve su ID de forma prioritaria.
    Si la petición carece de cabecera de autenticación (pruebas locales o contingencia):
    - En modo producción (ENVIRONMENT == "production"): Arroja HTTP 401 Unauthorized estricto.
    - En modo desarrollo/pruebas: Busca el primer usuario activo en la BD con el rol indicado
      para salvaguardar la integridad referencial sin interrumpir las operaciones.
    """
    if current_user and current_user.id:
        return current_user.id

    if settings.ENVIRONMENT.lower() == "production":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Autenticación requerida. No se permite fallback anónimo en entorno de producción.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    logger.warning(
        "Aviso de seguridad [DEV]: Petición sin token JWT resuelta con usuario fallback para rol=%s",
        role or "cualquiera",
    )

    query = select(Usuario.id).where(Usuario.activo.is_(True), Usuario.deleted_at.is_(None))
    if role:
        query = query.where(Usuario.rol == role)

    result = await db.execute(query.limit(1))
    user_id = result.scalar_one_or_none()
    if user_id:
        return user_id

    # Fallback ID semilla (admin general)
    return uuid.UUID("b1eebc99-9c0b-4ef8-bb6d-6bb9bd380a22")


__all__ = [
    "get_db",
    "get_current_user",
    "get_current_user_optional",
    "resolve_user_or_fallback",
    "require_roles",
    "security_scheme",
    "optional_security_scheme",
]
