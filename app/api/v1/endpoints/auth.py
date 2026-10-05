from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.security import create_access_token, verify_password
from app.models.user import Usuario
from app.schemas.auth import LoginRequest, TokenResponse
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

    if not user or not verify_password(login_data.password, user.password_hash):
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
