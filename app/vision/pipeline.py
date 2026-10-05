import logging
from typing import Any, Dict, Optional
import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Calibración: Escala de píxeles a centímetros para cámara cenital a 2.2 metros
PIXELS_PER_CM = 4.2
# Área mínima en píxeles para filtrar ruido visual
MIN_CONTOUR_AREA_PX = 3000.0


def decode_image(image_bytes: bytes) -> np.ndarray:
    """
    Decodifica bytes binarios de imagen en memoria usando OpenCV (cv2.imdecode).
    Retorna arreglo numpy en formato BGR (HxWxC).
    Lanza ValueError si la imagen está vacía o el formato no es soportado.
    """
    if not image_bytes:
        raise ValueError("Buffer de imagen vacío.")

    np_arr = np.frombuffer(image_bytes, np.uint8)
    image_bgr = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    if image_bgr is None:
        raise ValueError("No se pudo decodificar el formato de imagen proporcionado.")

    return image_bgr


def calculate_allometric_weight(area_cm2: float, largo_cm: float = 0.0, ancho_cm: float = 0.0) -> float:
    """
    Fórmula alométrica para cerdos de ceba/levante:
    W = round(0.0245 * (area_cm2 ** 1.052), 2)
    """
    if area_cm2 <= 0:
        return 0.0

    peso = 0.0245 * (area_cm2 ** 1.052)
    # Limitar al rango válido de Numeric(6,2) en base de datos (máximo 9999.99)
    return round(float(min(peso, 9999.99)), 2)


def process_pig_frame(model: Any, image_bgr: np.ndarray, conf_thresh: float = 0.50) -> Dict[str, Any]:
    """
    Procesa un fotograma síncronamente con el modelo YOLO dorsal.
    Extrae la detección con mayor confianza, su bounding box, contorno dorsal y calcula
    largo_cm, ancho_cm, area_cm2 y peso_kg.
    """
    if model is None:
        return {
            "detectado": False,
            "mensaje": "El modelo de visión artificial YOLO no se encuentra cargado en el servidor.",
        }

    try:
        # Inferencia síncrona sin verbose
        results = model.predict(source=image_bgr, conf=conf_thresh, verbose=False)
    except Exception as exc:
        logger.error(f"Error durante inferencia YOLO: {exc}")
        return {
            "detectado": False,
            "mensaje": f"Error en inferencia del modelo: {str(exc)}",
        }

    if not results or len(results) == 0:
        return {
            "detectado": False,
            "mensaje": "No se detectó ningún cerdo en el plano dorsal.",
        }

    res = results[0]
    boxes = getattr(res, "boxes", None)
    if boxes is None or len(boxes) == 0:
        return {
            "detectado": False,
            "mensaje": "No se detectó ningún cerdo en el plano dorsal.",
        }

    # Seleccionar la detección con mayor confianza
    confs = boxes.conf.cpu().numpy()
    best_idx = int(np.argmax(confs))
    best_conf = float(confs[best_idx])

    if best_conf < conf_thresh:
        return {
            "detectado": False,
            "mensaje": "No se detectó ningún cerdo en el plano dorsal.",
        }

    # Coordenadas Bounding Box
    xyxy = boxes.xyxy[best_idx].cpu().numpy().astype(int)
    x1, y1, x2, y2 = int(xyxy[0]), int(xyxy[1]), int(xyxy[2]), int(xyxy[3])

    # Asegurar límites dentro de la imagen
    h_img, w_img = image_bgr.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w_img, x2), min(h_img, y2)

    ancho_box_px = abs(x2 - x1)
    alto_box_px = abs(y2 - y1)

    # Evaluar si existen máscaras de segmentación (YOLOv8-seg)
    masks = getattr(res, "masks", None)
    area_px = 0.0
    largo_px = max(ancho_box_px, alto_box_px)
    ancho_px = min(ancho_box_px, alto_box_px)

    if masks is not None and masks.xy is not None and len(masks.xy) > best_idx:
        polygon = masks.xy[best_idx]
        if len(polygon) >= 3:
            contour = np.array(polygon, dtype=np.int32)
            contour_area = cv2.contourArea(contour)
            if contour_area > MIN_CONTOUR_AREA_PX:
                area_px = float(contour_area)
                # Ajustar elipse si el contorno tiene suficientes vértices
                if len(contour) >= 5:
                    try:
                        ellipse = cv2.fitEllipse(contour)
                        (_, (e_w, e_h), _) = ellipse
                        largo_px = max(e_w, e_h)
                        ancho_px = min(e_w, e_h)
                    except Exception:
                        pass

    if area_px <= 0.0:
        # Fallback morfológico elíptico en caso de sólo bbox o contorno ruidoso
        area_px = np.pi * (largo_px / 2.0) * (ancho_px / 2.0)

    # Conversión de píxeles a escala métrica real
    largo_cm = round(float(largo_px / PIXELS_PER_CM), 1)
    ancho_cm = round(float(ancho_px / PIXELS_PER_CM), 1)
    area_cm2 = round(float(area_px / (PIXELS_PER_CM ** 2)), 2)

    # Cálculo alométrico de peso estimado
    peso_kg = calculate_allometric_weight(area_cm2, largo_cm, ancho_cm)

    return {
        "detectado": True,
        "confianza": round(best_conf, 4),
        "peso_kg": peso_kg,
        "area_cm2": area_cm2,
        "largo_cm": largo_cm,
        "ancho_cm": ancho_cm,
        "bbox": {
            "x_min": x1,
            "y_min": y1,
            "x_max": x2,
            "y_max": y2,
        },
        "mensaje": "Detección dorsal y pesaje morfométrico exitoso.",
    }
