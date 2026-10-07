from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.lifespan import lifespan
from app.api.deps import get_db
from app.api.v1.api import api_router


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="API RESTful de PorciTech para Gestión Integral Porcina, Visión Artificial y Analítica TimescaleDB",
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url=f"{settings.API_V1_STR}/docs",
    redoc_url=f"{settings.API_V1_STR}/redoc",
    lifespan=lifespan,
)

# Middleware de CORS con soporte para localhost y subredes locales dinámicas
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2[0-9]|3[0-1])\.\d+\.\d+)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Registro del APIRouter maestro bajo el prefijo configurado (/api/v1)
app.include_router(api_router, prefix=settings.API_V1_STR)


@app.get("/", tags=["Raíz"])
async def root():
    return {
        "app": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "docs": f"{settings.API_V1_STR}/docs",
        "health": "/health",
    }


@app.get("/health", tags=["Salud y Diagnóstico"])
async def health_check(db: AsyncSession = Depends(get_db)):
    """
    Endpoint de validación de salud:
    Ejecuta SELECT 1 sobre la sesión asíncrona de base de datos para confirmar conectividad real.
    """
    try:
        result = await db.execute(text("SELECT 1"))
        result.scalar()
        return {
            "status": "online",
            "database": "connected",
            "service": f"{settings.PROJECT_NAME} v{settings.VERSION}",
            "ai_vision_loaded": hasattr(app.state, "yolo_model") and app.state.yolo_model is not None,
        }
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={
                "status": "degraded",
                "database": "disconnected",
                "error": str(exc),
                "service": f"{settings.PROJECT_NAME} v{settings.VERSION}",
            },
        )
