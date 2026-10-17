import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
import jwt
from passlib.context import CryptContext
from app.core.config import settings

# Contexto seguro de Bcrypt
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
ALGORITHM = "HS256"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifica si la contraseña en texto plano coincide con el hash almacenado."""
    return pwd_context.verify(plain_password, hashed_password)


async def verify_password_async(plain_password: str, hashed_password: str) -> bool:
    """Verifica la contraseña en un hilo separado para no bloquear el event loop de asyncio."""
    return await asyncio.to_thread(pwd_context.verify, plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Genera el hash Bcrypt para una contraseña."""
    return pwd_context.hash(password)


async def get_password_hash_async(password: str) -> str:
    """Genera el hash Bcrypt en un hilo separado para no bloquear el event loop de asyncio."""
    return await asyncio.to_thread(pwd_context.hash, password)



def create_access_token(
    data: Dict[str, Any],
    expires_delta: Optional[timedelta] = None,
) -> str:
    """
    Crea un token JWT con tiempo de expiración y claims personalizados (sub, email, rol).
    """
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode.update({
        "exp": expire,
        "iat": now,
    })
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def decode_access_token(token: str) -> Optional[Dict[str, Any]]:
    """Decodifica y valida un token JWT; retorna el payload o None si es inválido/expirado."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.PyJWTError:
        return None
