import logging
import os
import time
import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Configuración obligatoria para streaming RTSP de Dahua sobre TCP
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|stimeout;5000000"


def capturar_fotograma_dahua_sync(
    rtsp_url: str,
    warmup_grabs: int = 10,
    timeout_sec: float = 6.0,
) -> np.ndarray:
    """
    Se conecta al flujo RTSP de la cámara industrial Dahua TPC-BF,
    drena los paquetes acumulados para obtener el frame más reciente y lo retorna.
    """
    inicio = time.monotonic()
    cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    if not cap.isOpened():
        raise ConnectionError(
            f"No se pudo conectar a la cámara Dahua en: {rtsp_url}. "
            "Verifique que la cámara tenga alimentación, la IP 192.168.1.108 sea alcanzable y las credenciales sean correctas."
        )

    try:
        for _ in range(warmup_grabs):
            if (time.monotonic() - inicio) > timeout_sec:
                break
            cap.grab()

        exito, frame = cap.retrieve()
        if not exito or frame is None or frame.size == 0:
            exito, frame = cap.read()
            if not exito or frame is None or frame.size == 0:
                raise ValueError("Fotograma RTSP de Dahua vacío o corrupto.")
        return frame
    finally:
        cap.release()


# Alias retrocompatible
capturar_fotograma_v380_sync = capturar_fotograma_dahua_sync
