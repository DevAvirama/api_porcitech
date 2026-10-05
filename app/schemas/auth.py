from pydantic import BaseModel, EmailStr, Field
from app.schemas.user import UserResponse


class LoginRequest(BaseModel):
    email: EmailStr = Field(..., description="Correo electrónico registrado")
    password: str = Field(..., min_length=1, description="Contraseña en texto plano")


class TokenResponse(BaseModel):
    access_token: str = Field(..., description="Token de acceso JWT firmado")
    token_type: str = Field("bearer", description="Tipo de autorización HTTP")
    usuario: UserResponse = Field(..., description="Datos del usuario autenticado")


class TokenData(BaseModel):
    user_id: str
    email: str
    rol: str
