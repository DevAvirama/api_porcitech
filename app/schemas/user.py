import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserBase(BaseModel):
    email: str = Field(..., description="Correo electrónico único del usuario")
    nombre: str = Field(..., min_length=1, max_length=100, description="Nombre de pila")
    apellido: str = Field(..., min_length=1, max_length=100, description="Apellidos")
    rol: str = Field(..., description="Rol operativo o administrativo ('administrador', 'veterinario', 'operario')")
    telefono: Optional[str] = Field(None, max_length=20, description="Teléfono de contacto")


class UserCreate(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=100)
    apellido: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., min_length=3, max_length=150)
    password: str = Field(..., min_length=4, max_length=100)
    rol: str = Field(default="operario", description="'administrador' | 'veterinario' | 'operario'")
    telefono: Optional[str] = Field(None, max_length=20)


class UserUpdate(BaseModel):
    nombre: Optional[str] = Field(None, min_length=1, max_length=100)
    apellido: Optional[str] = Field(None, min_length=1, max_length=100)
    email: Optional[str] = Field(None, min_length=3, max_length=150)
    rol: Optional[str] = Field(None, description="'administrador' | 'veterinario' | 'operario'")
    telefono: Optional[str] = Field(None, max_length=20)
    activo: Optional[bool] = None
    password: Optional[str] = Field(None, min_length=4, max_length=100)


class UserResponse(BaseModel):
    id: uuid.UUID = Field(..., description="Identificador único UUID del usuario")
    nombre: str
    apellido: str
    email: str
    rol: str
    telefono: Optional[str] = None
    activo: bool = True
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
