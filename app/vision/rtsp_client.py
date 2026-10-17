import os
import time
import cv2
import numpy as np

os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|stimeout;5000000"

def capturar_fotograma_v380_sync(
    rtsp_url: str,
    warmup_grabs: int = 12,
    timeout_sec: float = 6.0
) -> np.ndarray:
    inicio = time.monotonic()
    cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    if not cap.isOpened():
        raise ConnectionError(f"No se pudo conectar a la cámara RTSP: {rtsp_url}")

    try:
        for _ in range(warmup_grabs):
            if (time.monotonic() - inicio) > timeout_sec:
                break
            cap.grab()

        exito, frame = cap.retrieve()
        if not exito or frame is None or frame.size == 0:
            exito, frame = cap.read()
            if not exito or frame is None or frame.size == 0:
                raise ValueError("Fotograma RTSP vacío o corrupto.")
        return frame
    finally:
        cap.release()
