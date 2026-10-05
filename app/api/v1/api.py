from fastapi import APIRouter
from app.api.v1.endpoints import (
    alimentacion,
    animales,
    auth,
    corrales,
    dashboard,
    inventario,
    pesajes,
    reproduccion,
    sanidad,
    usuarios,
    vision,
)

api_router = APIRouter()

# Registro de routers modulares v1
api_router.include_router(auth.router, prefix="/auth", tags=["Autenticación"])
api_router.include_router(usuarios.router, prefix="/usuarios", tags=["Usuarios"])
api_router.include_router(corrales.router, prefix="/corrales", tags=["Corrales"])
api_router.include_router(animales.router, prefix="/animales", tags=["Animales"])
api_router.include_router(vision.router, prefix="/vision", tags=["Visión Artificial"])
api_router.include_router(pesajes.router, prefix="/pesajes", tags=["Pesajes"])
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["Dashboard"])
api_router.include_router(inventario.router, prefix="/inventario", tags=["Inventario"])
api_router.include_router(sanidad.router, prefix="/sanidad", tags=["Sanidad y Bioseguridad"])
api_router.include_router(alimentacion.router, prefix="/alimentacion", tags=["Alimentación"])
api_router.include_router(reproduccion.router, prefix="/reproduccion", tags=["Reproducción"])


@api_router.get("/status", tags=["Sistema"])
async def api_status():
    return {
        "status": "online",
        "api_version": "v1",
        "message": "Router central v1 de PorciTech inicializado y operativo",
    }
