import asyncio
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Generator, Optional, Tuple

import cv2
import numpy as np

from app.core.config import settings
from app.vision.pipeline import (
    DEFAULT_PIXELS_PER_CM_AT_1280,
    DEFAULT_REF_WIDTH,
    COEF_ALOMETRICO_A,
    EXPONENTE_ALOMETRICO_B,
    FACTOR_OCUPACION_DORSAL,
    MIN_AREA_PIXELES,
    ejecutar_inferencia_morfometrica_sync,
)

logger = logging.getLogger(__name__)

# Asegurar configuración FFMPEG para RTSP sobre TCP de Dahua
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|stimeout;5000000"


class DahuaStreamManager:
    """
    Gestor de transmisión en tiempo real de la cámara industrial Dahua TPC-BF.
    
    Características clave:
    1. Lector desacoplado en segundo plano (Daemon Thread) que drena el buffer RTSP continuamente (0 lag).
    2. Inferencia en vivo desacoplada que calcula peso alométrico, confianza, área y dimensiones dorsales.
    3. HUD visual con métricas instantáneas dibujadas directamente sobre el frame.
    4. Generador de streaming MJPEG optimizado para etiquetas <img /> en navegadores web.
    5. Soporte multicanal: Canal 1 Óptico (Main 1080P y Substream) y Canal 2 Térmico.
    6. Captura de alta resolución bajo demanda para persistencia en base de datos.
    """

    def __init__(self):
        self.camera_ip: str = getattr(settings, "DAHUA_CAMERA_IP", "192.168.1.108")
        self.camera_port: int = getattr(settings, "DAHUA_CAMERA_PORT", 554)
        self.camera_user: str = getattr(settings, "DAHUA_CAMERA_USER", "admin")
        self.camera_password: str = getattr(settings, "DAHUA_CAMERA_PASSWORD", "admin123")
        self.channel: int = 1
        self.subtype: int = 0
        self.pixels_per_cm: float = DEFAULT_PIXELS_PER_CM_AT_1280

        # Estados de control y sincronización
        self._lock = threading.Lock()
        self._running = False
        self._read_thread: Optional[threading.Thread] = None
        self._infer_thread: Optional[threading.Thread] = None
        self._active_clients: int = 0
        self._last_client_access: float = 0.0

        # Almacenamiento de frames y métricas
        self.raw_frame: Optional[np.ndarray] = None
        self.annotated_frame: Optional[np.ndarray] = None
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

        # Referencia al modelo YOLO (suministrada desde app.state)
        self.yolo_model: Any = None
        self.yolo_lock: Optional[asyncio.Lock] = None

    def set_model(self, model: Any, yolo_lock: Optional[asyncio.Lock] = None):
        """Asigna la instancia en memoria del modelo YOLO."""
        self.yolo_model = model
        self.yolo_lock = yolo_lock

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
            if password is not None and password != self.camera_password:
                self.camera_password = password
                changed_stream = True
            if channel is not None and channel != self.channel:
                self.channel = channel
                changed_stream = True
            if subtype is not None and subtype != self.subtype:
                self.subtype = subtype
                changed_stream = True
            if pixels_per_cm and pixels_per_cm > 0:
                self.pixels_per_cm = pixels_per_cm

        if changed_stream and self._running:
            logger.info("Cambio de parámetros de streaming Dahua. Reiniciando hilo de captura...")
            self.restart()

    def build_rtsp_url(self, channel: Optional[int] = None, subtype: Optional[int] = None) -> str:
        """Construye la URL canónica RTSP oficial de Dahua."""
        ch = channel if channel is not None else self.channel
        sub = subtype if subtype is not None else self.subtype
        creds = ""
        if self.camera_user or self.camera_password:
            creds = f"{self.camera_user}:{self.camera_password}@"
        return f"rtsp://{creds}{self.camera_ip}:{self.camera_port}/cam/realmonitor?channel={ch}&subtype={sub}"

    def start(self):
        """Inicia los hilos de lectura RTSP e inferencia en vivo."""
        with self._lock:
            if self._running:
                return
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
            logger.info(f"Hilos de transmisión Dahua iniciados -> {self.build_rtsp_url(self.channel, self.subtype)}")

    def stop(self):
        """Detiene de forma limpia los hilos de captura."""
        with self._lock:
            self._running = False
            self.connected = False

    def restart(self):
        """Reinicia el ciclo de captura RTSP."""
        self.stop()
        time.sleep(0.3)
        self.start()

    def _read_loop(self):
        """Hilo continuo: mantiene la conexión RTSP abierta y drena el buffer sin acumular retraso."""
        reconnect_delay = 2.0
        while self._running:
            url = self.build_rtsp_url()
            cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            if not cap.isOpened():
                self.connected = False
                self.last_error = f"No se pudo conectar a Dahua RTSP en {self.camera_ip}:{self.camera_port}."
                logger.warning(f"Error abriendo stream Dahua: {self.last_error}. Reintentando en {reconnect_delay}s...")
                time.sleep(reconnect_delay)
                continue

            self.connected = True
            self.last_error = None
            logger.info(f"Conexión RTSP Dahua establecida exitosamente en {self.camera_ip} (Canal {self.channel}).")

            frame_count = 0
            start_time = time.monotonic()

            try:
                while self._running:
                    # Descartar frames acumulados para garantizar baja latencia
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
                        self.last_frame_time = time.time()
                        self.connected = True

                    # Pausar si no hay clientes visualizando después de 45 segundos de inactividad
                    if self._active_clients <= 0 and (time.time() - self._last_client_access) > 45.0:
                        logger.info("Sin clientes activos en stream Dahua. Pausando lectura para conservar recursos.")
                        break

                    # Breve pausa para no saturar CPU en decodificación excesiva
                    time.sleep(0.01)

            except Exception as e:
                logger.exception(f"Excepción en bucle de lectura Dahua: {e}")
            finally:
                cap.release()
                self.connected = False

            if self._running:
                time.sleep(reconnect_delay)

    def _inference_loop(self):
        """Hilo de inferencia YOLO: calcula en tiempo real métricas morfométricas sobre el último frame."""
        while self._running:
            raw = None
            with self._lock:
                if self.raw_frame is not None:
                    raw = self.raw_frame.copy()

            if raw is None or not self.connected or self.yolo_model is None:
                time.sleep(0.1)
                continue

            try:
                annotated, metrics = self._procesar_morfometria_en_vivo(raw)
                with self._lock:
                    self.annotated_frame = annotated
                    self.latest_metrics = metrics
            except Exception as exc:
                logger.debug(f"Error procesando inferencia en vivo: {exc}")

            # Frecuencia de inferencia ~3 a 5 Hz (suficiente para pesos en vivo y muy ligera en CPU)
            time.sleep(0.18)

    def _procesar_morfometria_en_vivo(self, frame: np.ndarray) -> Tuple[np.ndarray, Dict[str, Any]]:
        """
        Ejecuta predicción YOLO + Alometría en tiempo real y pinta anotaciones de estilo ciber-agro.
        """
        alto_orig, ancho_orig = frame.shape[:2]
        factor_resolucion = ancho_orig / float(DEFAULT_REF_WIDTH)
        pixels_per_cm_efectivo = self.pixels_per_cm * factor_resolucion

        # Inferencia con límite de tamaño de imagen para velocidad óptima
        resultados = self.yolo_model.predict(
            source=frame,
            conf=0.35,
            imgsz=640,
            verbose=False,
        )

        img_anotada = frame.copy()
        canal_nombre = "Canal 1 - Óptico RGB" if self.channel == 1 else "Canal 2 - Sensor Térmico"
        resolucion_str = f"{ancho_orig}x{alto_orig}"

        if not resultados or len(resultados[0].boxes) == 0:
            # HUD cuando no hay cerdo en encuadre
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
            else:
                dx_px = max(x2 - x1, 1.0)
                dy_px = max(y2 - y1, 1.0)
                area_bbox = float(dx_px * dy_px)
                if area_bbox < MIN_AREA_PIXELES:
                    continue
                largo_px = float(max(dx_px, dy_px))
                ancho_px = float(min(dx_px, dy_px))
                area_px2 = area_bbox * FACTOR_OCUPACION_DORSAL

            largo_cm = round(largo_px / pixels_per_cm_efectivo, 1)
            ancho_cm = round(ancho_px / pixels_per_cm_efectivo, 1)
            area_cm2 = round(area_px2 / (pixels_per_cm_efectivo ** 2), 1)
            peso_estimado_kg = round(COEF_ALOMETRICO_A * (area_cm2 ** EXPONENTE_ALOMETRICO_B), 2)

            candidatos.append({
                "confianza": conf,
                "peso_estimado_kg": peso_estimado_kg,
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

        # Pinta los ejemplares detectados
        for cand in candidatos:
            color_bgr = (16, 185, 129)  # Verde esmeralda vivo
            if cand["contorno_pts"] is not None:
                cv2.polylines(img_anotada, [cand["contorno_pts"]], isClosed=True, color=color_bgr, thickness=3)

            # Rectángulo delimitador
            cv2.rectangle(img_anotada, (cand["x1"], cand["y1"]), (cand["x2"], cand["y2"]), color_bgr, 3)

            # Etiqueta con peso destacado
            etiqueta = f"Porcino {cand['confianza']*100:.1f}% | {cand['peso_estimado_kg']} kg"
            etiqueta_sub = f"Area: {cand['area_cm2']} cm2 | L:{cand['largo_cm']}cm A:{cand['ancho_cm']}cm"

            # Fondo para el texto
            y_base = max(cand["y1"] - 12, 45)
            cv2.rectangle(img_anotada, (cand["x1"], y_base - 38), (cand["x1"] + 340, y_base + 6), (15, 23, 42), -1)
            cv2.rectangle(img_anotada, (cand["x1"], y_base - 38), (cand["x1"] + 340, y_base + 6), color_bgr, 2)
            cv2.putText(img_anotada, etiqueta, (cand["x1"] + 8, y_base - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)
            cv2.putText(img_anotada, etiqueta_sub, (cand["x1"] + 8, y_base - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (16, 185, 129), 1)

        # HUD superior
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
        # Barra superior semitransparente
        cv2.rectangle(img, (0, 0), (img.shape[1], 44), (10, 15, 29), -1)
        cv2.line(img, (0, 44), (img.shape[1], 44), (51, 65, 85), 1)

        # Círculo indicador EN VIVO (rojo pulsante)
        cv2.circle(img, (22, 22), 7, (0, 0, 230), -1)
        cv2.putText(img, "REC", (35, 27), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)

        # Texto canal y resolución
        info_cctv = f"DAHUA TPC-BF | {canal_nombre} | {resolucion} | {self.fps} FPS"
        cv2.putText(img, info_cctv, (90, 27), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (203, 213, 225), 1)

        # Indicador de estado al extremo derecho
        if detectado and peso_kg is not None:
            texto_estado = f"EJEMPLAR DETECTADO: {peso_kg:.2f} KG [LISTO PARA CAPTURA]"
            cv2.putText(
                img,
                texto_estado,
                (img.shape[1] - 520, 27),
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
                (img.shape[1] - 380, 27),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (245, 158, 11),
                1,
            )

    def get_latest_jpeg(self) -> Optional[bytes]:
        """Obtiene el último fotograma anotado codificado en JPEG."""
        with self._lock:
            self._last_client_access = time.time()
            frame = self.annotated_frame if self.annotated_frame is not None else self.raw_frame

        if frame is None:
            return None

        exito, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
        if not exito:
            return None
        return buffer.tobytes()

    def generate_mjpeg_stream(self) -> Generator[bytes, None, None]:
        """
        Generador síncrono/asíncrono compatible con FastAPI StreamingResponse
        (multipart/x-mixed-replace).
        """
        self.start()
        with self._lock:
            self._active_clients += 1
            self._last_client_access = time.time()

        try:
            # Esperar a que el primer frame esté listo
            for _ in range(50):
                if self.raw_frame is not None:
                    break
                time.sleep(0.05)

            # Frame fallback si la cámara aún conecta
            blank_frame = None

            while True:
                jpeg_bytes = self.get_latest_jpeg()
                if jpeg_bytes is None:
                    if blank_frame is None:
                        # Imagen de carga informativa
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
                time.sleep(0.05)  # ~20 FPS de streaming hacia el navegador

        finally:
            with self._lock:
                self._active_clients = max(0, self._active_clients - 1)
                self._last_client_access = time.time()
            logger.info(f"Cliente desconectado de streaming Dahua. Clientes activos: {self._active_clients}")

    def capture_snapshot(
        self,
        model: Any,
        pixels_per_cm_override: Optional[float] = None,
        validar_bordes: bool = False,
    ) -> Dict[str, Any]:
        """
        Captura el fotograma actual de alta definición y ejecuta inferencia morfométrica estricta.
        """
        frame_to_process = None
        with self._lock:
            if self.raw_frame is not None:
                frame_to_process = self.raw_frame.copy()

        if frame_to_process is None:
            # Si no hay stream activo, conectar puntualmente para captura
            url = self.build_rtsp_url()
            cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if not cap.isOpened():
                raise ConnectionError(f"No se pudo conectar a la cámara Dahua en {url}")
            try:
                for _ in range(8):
                    cap.grab()
                ret, frame = cap.retrieve()
                if not ret or frame is None:
                    ret, frame = cap.read()
                if not ret or frame is None or frame.size == 0:
                    raise ValueError("Fotograma Dahua vacío o corrupto.")
                frame_to_process = frame
            finally:
                cap.release()

        ppc = pixels_per_cm_override if pixels_per_cm_override else self.pixels_per_cm
        return ejecutar_inferencia_morfometrica_sync(
            model=model,
            img_bgr=frame_to_process,
            pixels_per_cm_override=ppc,
            conf_threshold=0.35,
            validar_bordes=validar_bordes,
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


# Instancia única global (Singleton)
dahua_stream_manager = DahuaStreamManager()
