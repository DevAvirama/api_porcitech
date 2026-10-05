import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI

from app.core.config import settings
from app.db.session import engine

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- FASE DE ARRANQUE (STARTUP) ---
    logger.info(f"🚀 Iniciando {settings.PROJECT_NAME} v{settings.VERSION}...")
    
    # Carga del Modelo YOLO de Visión Artificial
    backend_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    weights_path = settings.YOLO_WEIGHTS_PATH
    resolved_weights = weights_path if os.path.isabs(weights_path) else os.path.join(backend_root, weights_path)

    if os.path.exists(resolved_weights):
        try:
            logger.info(f"🧠 Cargando modelo de visión artificial YOLO desde: {resolved_weights}...")
            from ultralytics import YOLO
            
            # Se instancia UNA SOLA VEZ en memoria global de la app
            app.state.yolo_model = YOLO(resolved_weights)
            logger.info("✅ Modelo YOLO dorsal cargado exitosamente en app.state.yolo_model")
        except Exception as exc:
            logger.warning(
                f"⚠️ Advertencia: No se pudo cargar el modelo YOLO desde {resolved_weights}: {exc}. "
                "Iniciando en modo contingencia (yolo_model = None)."
            )
            app.state.yolo_model = None
    else:
        logger.warning(
            f"⚠️ Archivo de pesos no encontrado en '{weights_path}' (o '{resolved_weights}'). "
            "El servicio de inferencia operará en modo contingencia (yolo_model = None)."
        )
        app.state.yolo_model = None

    yield

    # --- FASE DE APAGADO (SHUTDOWN) ---
    logger.info(f"🛑 Deteniendo {settings.PROJECT_NAME}, liberando recursos de IA y pool de base de datos...")
    
    # Liberación de memoria del modelo
    if hasattr(app.state, "yolo_model"):
        app.state.yolo_model = None
    
    # Cierre de conexiones a PostgreSQL / TimescaleDB
    await engine.dispose()
    logger.info("👋 Pool de base de datos liberado y servicio detenido limpiamente.")
