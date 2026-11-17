import asyncio
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, Optional, Tuple

import cv2
import numpy as np

from app.core.config import settings
from app.vision.pipeline import (
    DEFAULT_PIXELS_PER_CM_AT_1280,
    DEFAULT_REF_WIDTH,
    FACTOR_OCUPACION_DORSAL,
    MIN_AREA_PIXELES,
    _estimar_peso_hibrido,
    calcular_ancho_toracico_px,
    calcular_momentos_hu,
    cargar_calibracion_persistente,
    ejecutar_inferencia_morfometrica_sync,
    predict_yolo_safe,
)
from app.vision.rtsp_client import mask_rtsp_url

logger = logging.getLogger(__name__)

# Asegurar configuración FFMPEG para RTSP sobre TCP de Dahua
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|stimeout;5000000"

MAX_STREAM_CLIENTS = 10


class DahuaStreamManager:
    """
    Gestor de transmisión en tiempo real de la cámara industrial Dahua TPC-BF.

    1. Lector desacoplado en segundo plano que drena el buffer RTSP continuamente (0 lag).
    2. Inferencia en vivo thread-safe con XGBoost/Huxley alineada con la captura final.
    3. HUD visual y caché compartida de codificación JPEG para múltiples clientes MJPEG.
    4. Parada limpia con Event + join y pausa real por inactividad sin reconexiones fantasma.
    """

    def __init__(self):
        self.camera_ip: str = getattr(settings, "DAHUA_CAMERA_IP", "192.168.1.108")
        self.camera_port: int = getattr(settings, "DAHUA_CAMERA_PORT", 554)
        self.camera_user: str = getattr(settings, "DAHUA_CAMERA_USER", "admin")
        self.camera_password: str = getattr(settings, "DAHUA_CAMERA_PASSWORD", "admin123")
        self.channel: int = 1
        self.subtype: int = 0
        self.pixels_per_cm: Optional[float] = None

        # Estados de control y sincronización
        self._lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._running = False
        self._read_thread: Optional[threading.Thread] = None
        self._infer_thread: Optional[threading.Thread] = None
        self._active_clients: int = 0
        self._last_client_access: float = 0.0

        # Almacenamiento de frames, caché JPEG y métricas
        self.raw_frame: Optional[np.ndarray] = None
        self.annotated_frame: Optional[np.ndarray] = None
        self._frame_seq: int = 0
        self._cached_jpeg_seq: int = -1
        self._cached_jpeg_bytes: Optional[bytes] = None
        self.connected: bool = False
        self.fps: float = 0.0
        self.last_frame_time: float = 0.0
        self.last_error: Optional[str] = None

        # Métricas de la inferencia en vivo
        self.latest_metrics: Dict[str, Any] = {
            "detectado": False,
            "peso_estimado_kg": None,
            "confianza": None,
            "area_cm2": None,
            "largo_cm": None,
            "ancho_cm": None,
            "total_detectados": 0,
            "mensaje": "Iniciando conexión con cámara Dahua...",
            "timestamp": None,
        }

        # Referencia al modelo YOLO y XGBoost (suministrada desde app.state)
        self.yolo_model: Any = None
        self.yolo_lock: Optional[asyncio.Lock] = None
        self.xgb_model: Any = None

    def set_model(
        self,
        model: Any,
        yolo_lock: Optional[asyncio.Lock] = None,
        xgb_model: Any = None,
    ):
        """Asigna la instancia en memoria de los modelos YOLO y XGBoost."""
        self.yolo_model = model
        self.yolo_lock = yolo_lock
        if xgb_model is not None:
            self.xgb_model = xgb_model

    def _effective_base_ppc(self) -> float:
        if self.pixels_per_cm and self.pixels_per_cm > 0:
            return float(self.pixels_per_cm)
        calib = cargar_calibracion_persistente()
        if calib and calib.get("pixels_per_cm_base1280"):
            return float(calib["pixels_per_cm_base1280"])
        return float(DEFAULT_PIXELS_PER_CM_AT_1280)

    def update_config(
        self,
        ip: Optional[str] = None,
        port: Optional[int] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        channel: Optional[int] = None,
        subtype: Optional[int] = None,
        pixels_per_cm: Optional[float] = None,
    ):
        """Actualiza parámetros de red o canales y reinicia la conexión si hubo cambios de stream."""
        changed_stream = False
        with self._lock:
            if ip and ip != self.camera_ip:
                self.camera_ip = ip
                changed_stream = True
            if port and port != self.camera_port:
                self.camera_port = port
                changed_stream = True
            if user and user != self.camera_user:
                self.camera_user = user
                changed_stream = True
            if password is not None and password != "" and password != self.camera_password:
                self.camera_password = password
                changed_stream = True
            if channel is not None and channel != self.channel:
                self.channel = channel
                changed_stream = True
            if subtype is not None and subtype != self.subtype:
                self.subtype = subtype
                changed_stream = True
            if pixels_per_cm and pixels_per_cm > 0:
                self.pixels_per_cm = float(pixels_per_cm)

        if changed_stream and self._running:
            logger.info("Cambio de parámetros de streaming Dahua. Reiniciando hilo de captura...")
            self.restart()

    def build_rtsp_url(self, channel: Optional[int] = None, subtype: Optional[int] = None) -> str:
        """Construye la URL canónica RTSP oficial de Dahua."""
        with self._lock:
            ch = channel if channel is not None else self.channel
            sub = subtype if subtype is not None else self.subtype
            creds = ""
            if self.camera_user or self.camera_password:
                creds = f"{self.camera_user}:{self.camera_password}@"
            return f"rtsp://{creds}{self.camera_ip}:{self.camera_port}/cam/realmonitor?channel={ch}&subtype={sub}"

    def start(self):
        """Inicia los hilos de lectura RTSP e inferencia en vivo."""
        with self._lifecycle_lock:
            with self._lock:
                if self._running:
                    return
                self._stop_event.clear()
                self._running = True
                self._last_client_access = time.time()

                self._read_thread = threading.Thread(
                    target=self._read_loop,
                    name="DahuaRTSPReader",
                    daemon=True,
                )
                self._read_thread.start()

                self._infer_thread = threading.Thread(
                    target=self._inference_loop,
                    name="DahuaLiveInference",
                    daemon=True,
                )
                self._infer_thread.start()
            safe_url = mask_rtsp_url(self.build_rtsp_url(self.channel, self.subtype))
            logger.info("Hilos de transmisión Dahua iniciados -> %s", safe_url)

    def stop(self):
        """Detiene de forma limpia los hilos de captura esperando su finalización."""
        with self._lifecycle_lock:
            with self._lock:
                if not self._running and self._read_thread is None and self._infer_thread is None:
                    return
                self._running = False
                self._stop_event.set()
                self.connected = False
                read_t = self._read_thread
                infer_t = self._infer_thread
                self._read_thread = None
                self._infer_thread = None

            current = threading.current_thread()
            if read_t is not None and read_t is not current and read_t.is_alive():
                read_t.join(timeout=6.0)
            if infer_t is not None and infer_t is not current and infer_t.is_alive():
                infer_t.join(timeout=3.0)

            with self._lock:
                self.raw_frame = None
                self.annotated_frame = None
                self._cached_jpeg_bytes = None
                self._cached_jpeg_seq = -1

    def restart(self):
        """Reinicia limpiamente el ciclo de captura RTSP sin dejar hilos huérfanos."""
        self.stop()
        self.start()

    def _read_loop(self):
        """Hilo continuo: mantiene la conexión RTSP abierta y drena el buffer sin acumular retraso."""
        reconnect_delay = 2.0
        while self._running and not self._stop_event.is_set():
            url = self.build_rtsp_url()
            cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
            idle_paused = False

            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                if not cap.isOpened():
                    with self._lock:
                        self.connected = False
                        self.last_error = f"No se pudo conectar a Dahua RTSP en {self.camera_ip}:{self.camera_port}."
                    logger.warning(
                        "Error abriendo stream Dahua: %s. Reintentando en %.1fs...",
                        self.last_error,
                        reconnect_delay,
                    )
                    self._stop_event.wait(timeout=reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 1.5, 30.0)
                    continue

                reconnect_delay = 2.0
                with self._lock:
                    self.connected = True
                    self.last_error = None
                logger.info("Conexión RTSP Dahua establecida exitosamente en %s (Canal %s).", self.camera_ip, self.channel)

                frame_count = 0
                start_time = time.monotonic()

                while self._running and not self._stop_event.is_set():
                    ret = cap.grab()
                    if not ret:
                        logger.warning("Fallo en cap.grab() de Dahua. Stream interrumpido.")
                        break

                    ret, frame = cap.retrieve()
                    if not ret or frame is None or frame.size == 0:
                        continue

                    now = time.monotonic()
                    frame_count += 1
                    elapsed = now - start_time
                    if elapsed >= 1.0:
                        self.fps = round(frame_count / elapsed, 1)
                        frame_count = 0
                        start_time = now

                    with self._lock:
                        self.raw_frame = frame
                        self._frame_seq += 1
                        self.last_frame_time = time.time()
                        self.connected = True
                        should_pause = (
                            self._active_clients <= 0
                            and (time.time() - self._last_client_access) > 45.0
                        )

                    if should_pause:
                        logger.info("Sin clientes activos en stream Dahua. Deteniendo lectura para conservar recursos.")
                        idle_paused = True
                        with self._lock:
                            self._running = False
                            self._stop_event.set()
                            self.raw_frame = None
                            self.annotated_frame = None
                            self._cached_jpeg_bytes = None
                            self._cached_jpeg_seq = -1
                        break

                    time.sleep(0.01)

            except Exception as e:
                logger.exception("Excepción en bucle de lectura Dahua: %s", e)
            finally:
                cap.release()
                with self._lock:
                    self.connected = False

            if idle_paused:
                break

            if self._running and not self._stop_event.is_set():
                self._stop_event.wait(timeout=reconnect_delay)
                reconnect_delay = min(reconnect_delay * 1.5, 30.0)

    def _inference_loop(self):
        """Hilo de inferencia YOLO: calcula en tiempo real métricas morfométricas sobre el último frame."""
        while self._running and not self._stop_event.is_set():
            raw = None
            with self._lock:
                if self.raw_frame is not None and self.connected:
                    raw = self.raw_frame.copy()

            if raw is None or self.yolo_model is None:
                self._stop_event.wait(timeout=0.1)
                continue

            try:
                annotated, metrics = self._procesar_morfometria_en_vivo(raw)
                with self._lock:
                    self.annotated_frame = annotated
                    self.latest_metrics = metrics
                    self._frame_seq += 1
            except Exception as exc:
                logger.debug("Error procesando inferencia en vivo: %s", exc)

            self._stop_event.wait(timeout=0.18)

    def _procesar_morfometria_en_vivo(self, frame: np.ndarray) -> Tuple[np.ndarray, Dict[str, Any]]:
        """
        Ejecuta predicción YOLO thread-safe + XGBoost/Alometría en tiempo real y pinta HUD.
        """
        alto_orig, ancho_orig = frame.shape[:2]
        factor_resolucion = ancho_orig / float(DEFAULT_REF_WIDTH)
        pixels_per_cm_efectivo = self._effective_base_ppc() * factor_resolucion

        resultados = predict_yolo_safe(self.yolo_model, frame, conf=0.35, imgsz=640)

        img_anotada = frame.copy()
        canal_nombre = "Canal 1 - Óptico RGB" if self.channel == 1 else "Canal 2 - Sensor Térmico"
        resolucion_str = f"{ancho_orig}x{alto_orig}"

        if not resultados or len(resultados[0].boxes) == 0:
            self._dibujar_hud_base(img_anotada, canal_nombre, resolucion_str, False)
            return img_anotada, {
                "detectado": False,
                "peso_estimado_kg": None,
                "confianza": None,
                "area_cm2": None,
                "largo_cm": None,
                "ancho_cm": None,
                "total_detectados": 0,
                "mensaje": "Buscando silueta dorsal de porcino...",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        res = resultados[0]
        boxes = res.boxes
        tiene_mascaras = res.masks is not None and len(res.masks.xy) > 0

        candidatos = []
        for idx in range(len(boxes)):
            conf = float(boxes.conf[idx].cpu().numpy())
            x1, y1, x2, y2 = [float(v) for v in boxes.xyxy[idx].cpu().numpy()]

            contorno_pts = None
            if tiene_mascaras and idx < len(res.masks.xy) and len(res.masks.xy[idx]) >= 3:
                contorno_pts = np.array(res.masks.xy[idx], dtype=np.int32)
                area_px2 = float(cv2.contourArea(contorno_pts))
                if area_px2 < MIN_AREA_PIXELES:
                    continue
                (_, _), (dim1_px, dim2_px), _ = cv2.minAreaRect(contorno_pts)
                largo_px = float(max(dim1_px, dim2_px))
                ancho_px = float(min(dim1_px, dim2_px))
                ancho_torax_px = calcular_ancho_toracico_px(contorno_pts, largo_px, ancho_px)
                momentos_hu = calcular_momentos_hu(contorno_pts)
            else:
                dx_px = max(x2 - x1, 1.0)
                dy_px = max(y2 - y1, 1.0)
                area_bbox = float(dx_px * dy_px)
                if area_bbox < MIN_AREA_PIXELES:
                    continue
                largo_px = float(max(dx_px, dy_px))
                ancho_px = float(min(dx_px, dy_px))
                area_px2 = area_bbox * FACTOR_OCUPACION_DORSAL
                ancho_torax_px = float(ancho_px * 0.92)
                momentos_hu = [0.0] * 7

            largo_cm = round(largo_px / pixels_per_cm_efectivo, 2)
            ancho_cm = round(ancho_px / pixels_per_cm_efectivo, 2)
            ancho_toracico_cm = round(ancho_torax_px / pixels_per_cm_efectivo, 2)
            area_cm2 = round(area_px2 / (pixels_per_cm_efectivo ** 2), 2)
            ratio_aspecto = round(largo_cm / max(ancho_cm, 0.01), 2)

            vector_xgboost = [
                area_cm2,
                largo_cm,
                ancho_cm,
                ancho_toracico_cm,
                ratio_aspecto,
            ] + momentos_hu
            est = _estimar_peso_hibrido(
                self.xgb_model if contorno_pts is not None else None,
                vector_xgboost,
                area_cm2,
            )

            candidatos.append({
                "confianza": conf,
                "peso_estimado_kg": est["peso_estimado_kg"],
                "area_cm2": area_cm2,
                "largo_cm": largo_cm,
                "ancho_cm": ancho_cm,
                "x1": int(x1), "y1": int(y1), "x2": int(x2), "y2": int(y2),
                "contorno_pts": contorno_pts,
            })

        if not candidatos:
            self._dibujar_hud_base(img_anotada, canal_nombre, resolucion_str, False)
            return img_anotada, {
                "detectado": False,
                "peso_estimado_kg": None,
                "confianza": None,
                "area_cm2": None,
                "largo_cm": None,
                "ancho_cm": None,
                "total_detectados": 0,
                "mensaje": "Silueta porcina por debajo de área mínima.",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        candidatos.sort(key=lambda c: c["confianza"], reverse=True)
        principal = candidatos[0]

        for cand in candidatos:
            color_bgr = (16, 185, 129)
            if cand["contorno_pts"] is not None:
                cv2.polylines(img_anotada, [cand["contorno_pts"]], isClosed=True, color=color_bgr, thickness=3)

            cv2.rectangle(img_anotada, (cand["x1"], cand["y1"]), (cand["x2"], cand["y2"]), color_bgr, 3)

            etiqueta = f"Porcino {cand['confianza']*100:.1f}% | {cand['peso_estimado_kg']} kg"
            etiqueta_sub = f"Area: {cand['area_cm2']} cm2 | L:{cand['largo_cm']}cm A:{cand['ancho_cm']}cm"

            y_base = max(cand["y1"] - 12, 45)
            cv2.rectangle(img_anotada, (cand["x1"], y_base - 38), (cand["x1"] + 340, y_base + 6), (15, 23, 42), -1)
            cv2.rectangle(img_anotada, (cand["x1"], y_base - 38), (cand["x1"] + 340, y_base + 6), color_bgr, 2)
            cv2.putText(img_anotada, etiqueta, (cand["x1"] + 8, y_base - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)
            cv2.putText(img_anotada, etiqueta_sub, (cand["x1"] + 8, y_base - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (16, 185, 129), 1)

        self._dibujar_hud_base(img_anotada, canal_nombre, resolucion_str, True, principal["peso_estimado_kg"])

        return img_anotada, {
            "detectado": True,
            "peso_estimado_kg": principal["peso_estimado_kg"],
            "confianza": round(principal["confianza"], 4),
            "area_cm2": principal["area_cm2"],
            "largo_cm": principal["largo_cm"],
            "ancho_cm": principal["ancho_cm"],
            "bbox": {
                "x1": principal["x1"],
                "y1": principal["y1"],
                "x2": principal["x2"],
                "y2": principal["y2"],
                "resolucion": resolucion_str,
                "pixels_per_cm": round(pixels_per_cm_efectivo, 3),
            },
            "total_detectados": len(candidatos),
            "mensaje": f"Ejemplar detectado: {principal['peso_estimado_kg']} kg ({principal['confianza']*100:.1f}%)",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def _dibujar_hud_base(
        self,
        img: np.ndarray,
        canal_nombre: str,
        resolucion: str,
        detectado: bool,
        peso_kg: Optional[float] = None,
    ):
        """Dibuja encabezado de estado tipo CCTV profesional."""
        cv2.rectangle(img, (0, 0), (img.shape[1], 44), (10, 15, 29), -1)
        cv2.line(img, (0, 44), (img.shape[1], 44), (51, 65, 85), 1)

        cv2.circle(img, (22, 22), 7, (0, 0, 230), -1)
        cv2.putText(img, "REC", (35, 27), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)

        info_cctv = f"DAHUA TPC-BF | {canal_nombre} | {resolucion} | {self.fps} FPS"
        cv2.putText(img, info_cctv, (90, 27), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (203, 213, 225), 1)

        if detectado and peso_kg is not None:
            texto_estado = f"EJEMPLAR DETECTADO: {peso_kg:.2f} KG [LISTO PARA CAPTURA]"
            cv2.putText(
                img,
                texto_estado,
                (max(img.shape[1] - 520, 10), 27),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (16, 185, 129),
                2,
            )
        else:
            texto_estado = "BUSCANDO ENCUADRE CENITAL..."
            cv2.putText(
                img,
                texto_estado,
                (max(img.shape[1] - 380, 10), 27),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (245, 158, 11),
                1,
            )

    def get_latest_jpeg(self) -> Optional[bytes]:
        """Obtiene el último fotograma codificado en JPEG reutilizando caché por secuencia."""
        with self._lock:
            self._last_client_access = time.time()
            if (
                self._cached_jpeg_bytes is not None
                and self._cached_jpeg_seq == self._frame_seq
            ):
                return self._cached_jpeg_bytes
            frame = self.annotated_frame if self.annotated_frame is not None else self.raw_frame
            seq = self._frame_seq

        if frame is None:
            return None

        exito, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
        if not exito:
            return None
        jpg_bytes = buffer.tobytes()

        with self._lock:
            if seq >= self._cached_jpeg_seq:
                self._cached_jpeg_bytes = jpg_bytes
                self._cached_jpeg_seq = seq
        return jpg_bytes

    async def generate_mjpeg_stream(self) -> AsyncGenerator[bytes, None]:
        """
        Generador asíncrono MJPEG compatible con FastAPI StreamingResponse
        sin bloquear hilos del threadpool por cada cliente.
        """
        with self._lock:
            if self._active_clients >= MAX_STREAM_CLIENTS:
                raise RuntimeError("Se alcanzó el límite máximo de clientes simultáneos de video en vivo.")
            self._active_clients += 1
            self._last_client_access = time.time()

        await asyncio.to_thread(self.start)

        try:
            for _ in range(50):
                with self._lock:
                    has_frame = self.raw_frame is not None
                if has_frame:
                    break
                await asyncio.sleep(0.05)

            blank_frame = None

            while True:
                jpeg_bytes = await asyncio.to_thread(self.get_latest_jpeg)
                if jpeg_bytes is None:
                    if blank_frame is None:
                        canvas = np.zeros((480, 854, 3), dtype=np.uint8)
                        cv2.putText(
                            canvas,
                            "Conectando con Dahua TPC-BF...",
                            (180, 240),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.75,
                            (16, 185, 129),
                            2,
                        )
                        _, buf = cv2.imencode(".jpg", canvas, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
                        blank_frame = buf.tobytes()
                    jpeg_bytes = blank_frame

                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + jpeg_bytes + b"\r\n"
                )
                await asyncio.sleep(0.05)
        finally:
            with self._lock:
                self._active_clients = max(0, self._active_clients - 1)
                self._last_client_access = time.time()
            logger.info("Cliente desconectado de streaming Dahua. Clientes activos: %s", self._active_clients)

    def capture_snapshot(
        self,
        model: Any,
        pixels_per_cm_override: Optional[float] = None,
        validar_bordes: bool = False,
    ) -> Dict[str, Any]:
        """
        Captura un fotograma fresco (o conexión directa RTSP si el stream no está activo/fresco)
        y ejecuta inferencia morfométrica estricta.
        """
        frame_to_process = None
        with self._lock:
            is_fresh = (
                self.connected
                and self.raw_frame is not None
                and (time.time() - self.last_frame_time) <= 2.0
            )
            if is_fresh:
                frame_to_process = self.raw_frame.copy()

        if frame_to_process is None:
            url = self.build_rtsp_url()
            safe_url = mask_rtsp_url(url)
            inicio = time.monotonic()
            cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                if not cap.isOpened():
                    raise ConnectionError(f"No se pudo conectar a la cámara Dahua ({safe_url}).")
                for _ in range(8):
                    if (time.monotonic() - inicio) > 6.0:
                        break
                    cap.grab()
                ret, frame = cap.retrieve()
                if not ret or frame is None:
                    ret, frame = cap.read()
                if not ret or frame is None or frame.size == 0:
                    raise ValueError("Fotograma Dahua vacío o corrupto.")
                frame_to_process = frame
            finally:
                cap.release()

        # Solo aplicar override manual si se envió explícitamente (respetando ArUco y calibracion_camara.json)
        return ejecutar_inferencia_morfometrica_sync(
            model=model,
            img_bgr=frame_to_process,
            pixels_per_cm_override=pixels_per_cm_override,
            conf_threshold=0.45,
            validar_bordes=validar_bordes,
            xgb_model=self.xgb_model,
        )

    def get_status(self) -> Dict[str, Any]:
        """Retorna el estado de conexión y métricas en vivo en formato JSON."""
        with self._lock:
            return {
                "connected": self.connected,
                "camera_ip": self.camera_ip,
                "camera_port": self.camera_port,
                "channel": self.channel,
                "subtype": self.subtype,
                "channel_name": (
                    "Canal 1 - Óptico RGB (1080P)"
                    if (self.channel == 1 and self.subtype == 0)
                    else "Canal 1 - Óptico Substream"
                    if (self.channel == 1 and self.subtype == 1)
                    else "Canal 2 - Sensor Térmico"
                ),
                "fps": self.fps,
                "active_clients": self._active_clients,
                "last_error": self.last_error,
                "metrics": self.latest_metrics,
            }


# Instancia Singleton global para compartir entre routers y lifespan
dahua_stream_manager = DahuaStreamManager()
