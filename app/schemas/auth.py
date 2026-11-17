from typing import Optional
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


class ForgotPasswordRequest(BaseModel):
    email: EmailStr = Field(..., description="Correo electrónico asociado a la cuenta")


class ForgotPasswordResponse(BaseModel):
    message: str = Field(..., description="Mensaje de estado de la solicitud")
    expires_in_minutes: int = Field(..., description="Minutos de vigencia del enlace o código")
    email_sent: bool = Field(False, description="Indica si se despachó correo vía SMTP real")
    reset_url_preview: Optional[str] = Field(
        None,
        description="Enlace directo de recuperación (solo disponible en entorno development sin SMTP)",
    )
    reset_token_preview: Optional[str] = Field(
        None,
        description="Token temporal de recuperación (solo disponible en entorno development sin SMTP)",
    )


class ResetPasswordRequest(BaseModel):
    token: str = Field(..., min_length=10, description="Token temporal recibido en el enlace de recuperación")
    new_password: str = Field(..., min_length=6, max_length=128, description="Nueva contraseña (mínimo 6 caracteres)")


class ResetPasswordResponse(BaseModel):
    message: str = Field(..., description="Confirmación del cambio de contraseña")
