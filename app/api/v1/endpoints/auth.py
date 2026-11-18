import re
import uuid
from urllib.parse import urlparse
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.config import settings
from app.core.email import send_password_reset_email
from app.core.security import (
    compute_password_fingerprint,
    create_access_token,
    create_password_reset_token,
    decode_password_reset_token,
    get_password_hash_async,
    verify_password_async,
)
from app.models.user import Usuario
from app.schemas.auth import (
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LoginRequest,
    ResetPasswordRequest,
    ResetPasswordResponse,
    TokenResponse,
)
from app.schemas.user import UserResponse

router = APIRouter()


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Iniciar sesión y obtener token JWT",
    description="Valida credenciales contra PostgreSQL y retorna token Bearer JWT con los datos del usuario.",
)
async def login(
    login_data: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    # Búsqueda insensible a mayúsculas para el correo
    email_clean = str(login_data.email).strip().lower()

    query = select(Usuario).where(
        Usuario.email == email_clean,
        Usuario.deleted_at.is_(None),
    )
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user or not await verify_password_async(login_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Credenciales incorrectas",
        )


    if not user.activo:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Usuario inactivo en el sistema",
        )

    access_token = create_access_token(
        data={
            "sub": str(user.id),
            "email": user.email,
            "rol": user.rol,
            "nombre": user.nombre,
            "apellido": user.apellido,
        }
    )

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        usuario=UserResponse.model_validate(user),
    )


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Obtener perfil del usuario autenticado",
    description="Retorna la información del usuario correspondiente al token Bearer provisto.",
)
async def get_me(
    current_user: Usuario = Depends(get_current_user),
) -> UserResponse:
    return UserResponse.model_validate(current_user)


@router.post(
    "/logout",
    summary="Cerrar sesión",
    description="Notifica al backend la finalización de sesión por parte del cliente.",
)
async def logout(
    current_user: Usuario = Depends(get_current_user),
):
    return {"message": f"Sesión de {current_user.email} finalizada exitosamente"}


def _resolve_safe_frontend_base_url(request: Request) -> str:
    """Valida la cabecera Origin contra orígenes confiables para evitar Header Poisoning."""
    fallback = settings.FRONTEND_BASE_URL.rstrip("/")
    raw_origin = (request.headers.get("origin") or "").strip().rstrip("/")
    if not raw_origin:
        return fallback
    parsed = urlparse(raw_origin)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.path or parsed.query:
        return fallback
    normalized = f"{parsed.scheme}://{parsed.netloc}"
    if normalized in [o.rstrip("/") for o in settings.CORS_ORIGINS]:
        return normalized
    if settings.CORS_ORIGIN_REGEX and re.fullmatch(settings.CORS_ORIGIN_REGEX, normalized):
        return normalized
    return fallback


@router.post(
    "/forgot-password",
    response_model=ForgotPasswordResponse,
    summary="Solicitar recuperación de contraseña (HU02)",
    description=(
        "Genera un token temporal de un solo uso (caduca automáticamente y se invalida al cambiar la contraseña) "
        "y envía el enlace de restablecimiento al correo registrado mediante SMTP."
    ),
)
async def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> ForgotPasswordResponse:
    email_clean = str(payload.email).strip().lower()
    expires_min = settings.PASSWORD_RESET_EXPIRE_MINUTES
    generic_msg = (
        "Si el correo está registrado y activo en PorciTech, hemos enviado las instrucciones "
        f"de recuperación. El enlace caduca en {expires_min} minutos."
    )

    query = select(Usuario).where(
        Usuario.email == email_clean,
        Usuario.deleted_at.is_(None),
        Usuario.activo.is_(True),
    )
    user = (await db.execute(query)).scalar_one_or_none()

    if not user:
        return ForgotPasswordResponse(
            message=generic_msg,
            expires_in_minutes=expires_min,
            email_sent=False,
        )

    reset_token = create_password_reset_token(
        user_id=str(user.id),
        email=user.email,
        password_hash=user.password_hash,
        expires_minutes=expires_min,
    )

    base_url = _resolve_safe_frontend_base_url(request)
    reset_url = f"{base_url}/reset-password?token={reset_token}"

    user_name = f"{user.nombre} {user.apellido}".strip() or user.email
    background_tasks.add_task(
        send_password_reset_email,
        to_email=user.email,
        user_name=user_name,
        reset_url=reset_url,
        expires_minutes=expires_min,
    )

    smtp_configured = bool(settings.SMTP_HOST and settings.SMTP_HOST.strip())
    return ForgotPasswordResponse(
        message=generic_msg,
        expires_in_minutes=expires_min,
        email_sent=smtp_configured,
        reset_url_preview=None,
        reset_token_preview=None,
    )


@router.post(
    "/reset-password",
    response_model=ResetPasswordResponse,
    summary="Confirmar restablecimiento de contraseña (HU02)",
    description="Valida el token temporal de recuperación y actualiza la contraseña cifrada con Bcrypt.",
)
async def reset_password(
    payload: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
) -> ResetPasswordResponse:
    token_data = decode_password_reset_token(payload.token.strip())
    if not token_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El enlace o código de recuperación es inválido o ha caducado.",
        )

    try:
        user_uuid = uuid.UUID(str(token_data["sub"]))
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token de recuperación malformado.",
        )

    query = select(Usuario).where(
        Usuario.id == user_uuid,
        Usuario.deleted_at.is_(None),
    )
    user = (await db.execute(query)).scalar_one_or_none()
    if not user or not user.activo:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fue posible validar la cuenta asociada a este enlace.",
        )

    # Verificar huella del hash actual para garantizar que el token sea de un solo uso
    current_fp = compute_password_fingerprint(user.password_hash)
    if token_data.get("pwd_fp") != current_fp:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Este enlace de recuperación ya fue utilizado o ha sido invalidado.",
        )

    user.password_hash = await get_password_hash_async(payload.new_password)
    await db.commit()

    return ResetPasswordResponse(
        message="Contraseña actualizada exitosamente. Ya puedes iniciar sesión."
    )
