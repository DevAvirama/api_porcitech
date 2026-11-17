import asyncio
import hashlib
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


def compute_password_fingerprint(password_hash: str) -> str:
    """Genera una huella corta del hash actual para invalidar tokens de recuperación tras su primer uso."""
    return hashlib.sha256((password_hash or "").encode("utf-8")).hexdigest()[:16]


def create_password_reset_token(
    user_id: str,
    email: str,
    password_hash: str,
    expires_minutes: Optional[int] = None,
) -> str:
    """
    Crea un token JWT temporal de un solo uso para restablecimiento de contraseña (HU02).
    """
    minutes = expires_minutes or settings.PASSWORD_RESET_EXPIRE_MINUTES
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=minutes)
    payload = {
        "sub": str(user_id),
        "email": email,
        "purpose": "password_reset",
        "pwd_fp": compute_password_fingerprint(password_hash),
        "iat": now,
        "exp": expire,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_password_reset_token(token: str) -> Optional[Dict[str, Any]]:
    """Decodifica y valida que el token corresponda a un flujo vigente de password_reset."""
    payload = decode_access_token(token)
    if not payload or payload.get("purpose") != "password_reset":
        return None
    if not payload.get("sub") or not payload.get("pwd_fp"):
        return None
    return payload
