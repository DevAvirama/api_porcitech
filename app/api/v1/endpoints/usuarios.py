import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, require_roles
from app.core.security import get_password_hash_async
from app.models.user import Usuario
from app.schemas.user import UserCreate, UserResponse, UserUpdate

router = APIRouter()


def verify_admin_role(current_user: Usuario):
    if current_user.rol != "administrador":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permisos insuficientes. Requiere rol de administrador.",
        )


@router.get("", response_model=List[UserResponse])
async def list_usuarios(
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(require_roles(["administrador"])),
):
    """
    Retorna la lista de usuarios activos y registrados en la plataforma.
    Requiere rol de administrador.
    """
    query = (
        select(Usuario)
        .where(Usuario.deleted_at.is_(None))
        .order_by(Usuario.created_at.desc())
    )
    result = await db.execute(query)
    return result.scalars().all()


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_usuario(
    user_in: UserCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(require_roles(["administrador"])),
):
    """
    Crea un nuevo usuario en la base de datos con contraseña hasheada con Bcrypt.
    Requiere rol de administrador.
    """

    # Validar rol permitido
    allowed_roles = ["administrador", "veterinario", "operario"]
    clean_role = user_in.rol.strip().lower()
    if clean_role not in allowed_roles:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Rol '{user_in.rol}' no válido. Roles permitidos: {', '.join(allowed_roles)}",
        )

    # Validar unicidad de correo
    clean_email = user_in.email.strip().lower()
    check_email = select(Usuario).where(
        Usuario.email == clean_email,
        Usuario.deleted_at.is_(None),
    )
    res_email = await db.execute(check_email)
    if res_email.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"El correo electrónico '{clean_email}' ya se encuentra registrado.",
        )

    hashed_pw = await get_password_hash_async(user_in.password)

    new_user = Usuario(
        nombre=user_in.nombre.strip(),
        apellido=user_in.apellido.strip(),
        email=clean_email,
        password_hash=hashed_pw,
        rol=clean_role,
        telefono=user_in.telefono.strip() if user_in.telefono else None,
        activo=True,
        sync_status="synced",
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    return new_user


@router.put("/{id}", response_model=UserResponse)
async def update_usuario(
    id: uuid.UUID,
    user_update: UserUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """
    Actualiza la información de un usuario registrado.
    Los administradores pueden actualizar cualquier usuario; los demás roles solo su propio perfil.
    """
    is_admin = (current_user.rol or "").lower() == "administrador"
    if id != current_user.id and not is_admin:
        verify_admin_role(current_user)

    query = select(Usuario).where(Usuario.id == id, Usuario.deleted_at.is_(None))
    res = await db.execute(query)
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Usuario con id '{id}' no encontrado",
        )

    # Si se actualiza email, verificar que no esté ocupado por otro usuario
    if user_update.email:
        clean_email = user_update.email.strip().lower()
        if clean_email != user.email:
            check_email = select(Usuario).where(
                Usuario.email == clean_email,
                Usuario.id != id,
                Usuario.deleted_at.is_(None),
            )
            res_email = await db.execute(check_email)
            if res_email.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"El correo electrónico '{clean_email}' ya está en uso por otro usuario.",
                )
            user.email = clean_email

    if user_update.nombre is not None:
        user.nombre = user_update.nombre.strip()
    if user_update.apellido is not None:
        user.apellido = user_update.apellido.strip()
    if user_update.rol is not None:
        if not is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Solo un administrador puede modificar el rol de un usuario.",
            )
        clean_role = user_update.rol.strip().lower()
        allowed_roles = ["administrador", "veterinario", "operario"]
        if clean_role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Rol '{user_update.rol}' no válido. Roles permitidos: {', '.join(allowed_roles)}",
            )
        user.rol = clean_role
    if user_update.telefono is not None:
        user.telefono = user_update.telefono.strip() if user_update.telefono else None
    if user_update.activo is not None:
        if not is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Solo un administrador puede modificar el estado activo de un usuario.",
            )
        user.activo = user_update.activo
    if user_update.password:
        user.password_hash = await get_password_hash_async(user_update.password)

    await db.commit()
    await db.refresh(user)

    return user


@router.delete("/{id}")
async def delete_usuario(
    id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(require_roles(["administrador"])),
):
    """
    Aplica soft delete al usuario, marcándolo como inactivo.
    Evita que el administrador autenticado se elimine a sí mismo.
    """

    if id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No puede eliminar su propio usuario administrador",
        )

    query = select(Usuario).where(Usuario.id == id, Usuario.deleted_at.is_(None))
    res = await db.execute(query)
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Usuario con id '{id}' no encontrado",
        )

    user.deleted_at = func.clock_timestamp()
    user.activo = False
    await db.commit()

    return {"message": f"Usuario '{user.nombre} {user.apellido}' desactivado correctamente"}
