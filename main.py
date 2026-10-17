import logging
from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.lifespan import lifespan
from app.api.deps import get_db
from app.api.v1.api import api_router

logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="API RESTful de PorciTech para Gestión Integral Porcina, Visión Artificial y Analítica TimescaleDB",
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url=f"{settings.API_V1_STR}/docs",
    redoc_url=f"{settings.API_V1_STR}/redoc",
    lifespan=lifespan,
)

# Configuración de CORS con orígenes explícitos y regex para redes locales
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_origin_regex=settings.CORS_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Manejador global de excepciones para garantizar que, incluso ante fallos no capturados,
    FastAPI adjunte siempre cabeceras CORS y devuelva JSON con detalle en lugar de provocar 'Failed to fetch'.
    """
    logger.exception(f"Unhandled error en {request.method} {request.url.path}: {exc}")
    is_prod = settings.ENVIRONMENT.lower() == "production"
    detail = "Error interno del servidor." if is_prod else str(exc)
    return JSONResponse(
        status_code=500,
        content={"detail": detail},
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
        logger.exception("Error de conectividad en /health: %s", exc)
        is_prod = settings.ENVIRONMENT.lower() == "production"
        error_msg = "Base de datos no disponible." if is_prod else str(exc)
        return JSONResponse(
            status_code=503,
            content={
                "status": "degraded",
                "database": "disconnected",
                "error": error_msg,
                "service": f"{settings.PROJECT_NAME} v{settings.VERSION}",
            },
        )

