import uuid
from datetime import date, datetime
from typing import Optional, Union
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AnimalBase(BaseModel):
    codigo_arete: str = Field(..., min_length=1, max_length=50, description="Código de arete o caravana física")
    codigo_qr: Optional[str] = Field(None, max_length=100, description="Código QR para trazabilidad")
    nombre_alias: Optional[str] = Field(None, max_length=80, description="Nombre o apodo del animal")
    sexo: str = Field(..., description="Sexo biológico: 'macho' o 'hembra'")
    raza: str = Field(..., min_length=1, max_length=60, description="Raza o cruce genético")
    fecha_nacimiento: date = Field(..., description="Fecha de nacimiento del animal")
    estado: str = Field("activo", description="Estado o fase actual del animal ('activo', 'lactante', 'precebo', 'levante', 'engorde', 'gestacion', 'cuarentena', 'vendido', 'muerto', 'enfermo')")
    corral_id: Optional[uuid.UUID] = Field(None, description="UUID del corral asignado")
    peso_actual_kg: Optional[float] = Field(0.0, ge=0.0, description="Peso corporal actual en kilogramos")

    @field_validator("fecha_nacimiento", mode="before")
    @classmethod
    def parse_birth_date(cls, v: Union[str, date]) -> date:
        if isinstance(v, date):
            return v
        if isinstance(v, str):
            clean_str = v.strip()
            # Si contiene /, convertir a formato ISO
            if "/" in clean_str:
                parts = clean_str.split("/")
                if len(parts) == 3:
                    if len(parts[0]) == 4:  # YYYY/MM/DD
                        return date(int(parts[0]), int(parts[1]), int(parts[2]))
                    elif len(parts[2]) == 4:  # DD/MM/YYYY
                        return date(int(parts[2]), int(parts[1]), int(parts[0]))
            try:
                return date.fromisoformat(clean_str)
            except Exception:
                pass
        return v


class AnimalCreate(AnimalBase):
    foto_url: Optional[str] = Field(None, description="URL de la fotografía del animal")
    id_padre: Optional[uuid.UUID] = Field(None, description="UUID del progenitor macho")
    id_madre: Optional[uuid.UUID] = Field(None, description="UUID de la progenitora hembra")

    @model_validator(mode="after")
    def auto_generate_qr(self):
        if not self.codigo_qr or not self.codigo_qr.strip():
            self.codigo_qr = f"QR-{self.codigo_arete.strip()}"
        return self


class AnimalUpdate(BaseModel):
    codigo_arete: Optional[str] = Field(None, min_length=1, max_length=50)
    codigo_qr: Optional[str] = Field(None, max_length=100)
    nombre_alias: Optional[str] = None
    sexo: Optional[str] = None
    raza: Optional[str] = None
    fecha_nacimiento: Optional[date] = None
    estado: Optional[str] = None
    corral_id: Optional[uuid.UUID] = None
    peso_actual_kg: Optional[float] = Field(None, ge=0.0)
    foto_url: Optional[str] = None
    id_padre: Optional[uuid.UUID] = None
    id_madre: Optional[uuid.UUID] = None

    @field_validator("fecha_nacimiento", mode="before")
    @classmethod
    def parse_birth_date(cls, v):
        if v is None or isinstance(v, date):
            return v
        if isinstance(v, str):
            clean_str = v.strip()
            if "/" in clean_str:
                parts = clean_str.split("/")
                if len(parts) == 3:
                    if len(parts[0]) == 4:
                        return date(int(parts[0]), int(parts[1]), int(parts[2]))
                    elif len(parts[2]) == 4:
                        return date(int(parts[2]), int(parts[1]), int(parts[0]))
            try:
                return date.fromisoformat(clean_str)
            except Exception:
                pass
        return v


class AnimalResponse(AnimalBase):
    id: uuid.UUID = Field(..., description="Identificador único UUID del animal")
    codigo_qr: str = Field(..., description="Código QR asegurado")
    corral_codigo: Optional[str] = Field(None, description="Código legible del corral asignado (ej: 'LOTE-42')")
    foto_url: Optional[str] = Field(None, description="URL de foto o evidencia visual")
    id_padre: Optional[uuid.UUID] = None
    id_madre: Optional[uuid.UUID] = None
    created_at: datetime = Field(..., description="Fecha de creación del registro")

    model_config = ConfigDict(from_attributes=True)
