import asyncio
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
    app.state.yolo_lock = asyncio.Lock()
    
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
            from app.vision.pipeline import resolve_pig_class_ids
            pig_ids = resolve_pig_class_ids(app.state.yolo_model)
            model_names = getattr(app.state.yolo_model, "names", {})
            if pig_ids is None:
                logger.warning(
                    "⚠️ El modelo YOLO cargado (%s) no expone la clase 'pig' (clases=%s). Verifique weights/best.pt.",
                    resolved_weights,
                    model_names,
                )
            else:
                logger.info(
                    "✅ Modelo YOLO dorsal cargado exitosamente en app.state.yolo_model (clases porcino=%s)",
                    pig_ids,
                )

            # Warmup ligero: inicializa grafos/kernels para que la primera petición no pague la latencia
            try:
                import numpy as np

                app.state.yolo_model.predict(
                    source=np.zeros((640, 640, 3), dtype=np.uint8), verbose=False
                )
                logger.info("🔥 Warmup YOLO completado (640x640).")
            except Exception as warm_exc:
                logger.warning(f"⚠️ Warmup YOLO omitido: {warm_exc}")

            # Vincular modelo y lock al gestor de streaming en vivo Dahua
            from app.vision.dahua_stream import dahua_stream_manager
            dahua_stream_manager.set_model(app.state.yolo_model, app.state.yolo_lock)
            logger.info("📹 DahuaStreamManager inicializado con modelo YOLO en memoria.")
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

    # Alias canónico: misma instancia (no se recarga el modelo)
    app.state.model = app.state.yolo_model

    # Carga del Modelo XGBoost para regresión biométrica de peso (HU21)
    xgb_path = settings.XGBOOST_WEIGHTS_PATH
    resolved_xgb = xgb_path if os.path.isabs(xgb_path) else os.path.join(backend_root, xgb_path)
    app.state.xgb_model = None
    if os.path.exists(resolved_xgb):
        try:
            import xgboost as xgb

            booster = xgb.Booster()
            booster.load_model(resolved_xgb)
            app.state.xgb_model = booster
            logger.info("✅ Modelo XGBoost cargado exitosamente desde: %s", resolved_xgb)
        except Exception as xgb_exc:
            logger.warning(
                "⚠️ No se pudo cargar el modelo XGBoost desde %s (%s). Se usará ecuación alométrica de Huxley.",
                resolved_xgb,
                xgb_exc,
            )
    else:
        logger.info("ℹ️ Modelo XGBoost no encontrado en %s; operando con ecuación alométrica de Huxley.", resolved_xgb)

    # Vincular XGBoost y calibración persistente al gestor de streaming en vivo Dahua
    try:
        from app.vision.dahua_stream import dahua_stream_manager
        from app.vision.pipeline import cargar_calibracion_persistente

        if app.state.yolo_model is not None:
            dahua_stream_manager.set_model(app.state.yolo_model, app.state.yolo_lock, app.state.xgb_model)
        calib = await asyncio.to_thread(cargar_calibracion_persistente)
        if calib and calib.get("pixels_per_cm_base1280"):
            await asyncio.to_thread(
                dahua_stream_manager.update_config,
                pixels_per_cm=float(calib["pixels_per_cm_base1280"]),
            )
            logger.info(
                "📏 Calibración ArUco cargada desde disco: %s px/cm (marcador ID %s).",
                calib.get("pixels_per_cm_base1280"),
                calib.get("marcador_id"),
            )
    except Exception as stream_exc:
        logger.warning("⚠️ Aviso configurando DahuaStreamManager con XGBoost/ArUco: %s", stream_exc)

    yield

    # --- FASE DE APAGADO (SHUTDOWN) ---
    logger.info(f"🛑 Deteniendo {settings.PROJECT_NAME}, liberando recursos de IA y pool de base de datos...")
    
    # Liberación de memoria del modelo y detención de streams
    try:
        from app.vision.dahua_stream import dahua_stream_manager
        await asyncio.to_thread(dahua_stream_manager.stop)
    except Exception:
        pass

    if hasattr(app.state, "yolo_model"):
        app.state.yolo_model = None
    app.state.model = None
    app.state.xgb_model = None
    
    # Cierre de conexiones a PostgreSQL / TimescaleDB
    await engine.dispose()
    logger.info("👋 Pool de base de datos liberado y servicio detenido limpiamente.")
