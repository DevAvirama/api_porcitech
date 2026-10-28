import json
from typing import List, Union
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "PorciTech API"
    VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    API_V1_STR: str = "/api/v1"
    SECRET_KEY: str = "clave_secreta_jwt_porcitech_desarrollo_sena_2026"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/porcitech_db"
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_TIMEOUT: float = 30.0
    DB_POOL_RECYCLE: int = 1800
    CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://192.168.32.134:3000",
    ]
    CORS_ORIGIN_REGEX: str = r"https?://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[0-1])\.\d+\.\d+|[a-zA-Z0-9-]+(\.local)?)(:\d+)?"
    YOLO_WEIGHTS_PATH: str = "weights/best.pt"

    # =========================================================================
    # Configuración de Cámara Dahua TPC-BF (Bi-spectrum Térmica y Óptica)
    # =========================================================================
    DAHUA_CAMERA_IP: str = "192.168.1.108"
    DAHUA_CAMERA_PORT: int = 554
    DAHUA_CAMERA_USER: str = "admin"
    DAHUA_CAMERA_PASSWORD: str = "admin123"
    DAHUA_RTSP_URL: str = "rtsp://admin:admin123@192.168.1.108:554/cam/realmonitor?channel=1&subtype=0"
    DAHUA_SUBSTREAM_RTSP_URL: str = "rtsp://admin:admin123@192.168.1.108:554/cam/realmonitor?channel=1&subtype=1"
    DAHUA_THERMAL_RTSP_URL: str = "rtsp://admin:admin123@192.168.1.108:554/cam/realmonitor?channel=2&subtype=0"

    # Parámetros de Calibración Geométrica y Alométrica
    DEFAULT_REF_WIDTH: int = 1280
    DEFAULT_PIXELS_PER_CM_AT_1280: float = 15.2
    COEF_ALOMETRICO_A: float = 0.0245
    EXPONENTE_ALOMETRICO_B: float = 1.052
    FACTOR_OCUPACION_DORSAL: float = 0.76

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str):
            if v.startswith("[") and v.endswith("]"):
                try:
                    return json.loads(v)
                except Exception:
                    pass
            return [i.strip() for i in v.split(",") if i.strip()]
        elif isinstance(v, (list, tuple)):
            return [str(i) for i in v]
        return v

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


settings = Settings()
