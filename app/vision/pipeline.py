import base64
from typing import Dict, Any, Optional
import cv2
import numpy as np
from ultralytics import YOLO

DEFAULT_REF_WIDTH = 1280
DEFAULT_PIXELS_PER_CM_AT_1280 = 15.2
COEF_ALOMETRICO_A = 0.0245
EXPONENTE_ALOMETRICO_B = 1.052
FACTOR_OCUPACION_DORSAL = 0.76

def decodificar_bytes_imagen_sync(file_bytes: bytes) -> np.ndarray:
    arr = np.frombuffer(file_bytes, dtype=np.uint8)
    img_bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img_bgr is None or img_bgr.size == 0:
        raise ValueError("El archivo enviado no es una imagen válida.")
    return img_bgr

def ejecutar_inferencia_morfometrica_sync(
    model: YOLO,
    img_bgr: np.ndarray,
    pixels_per_cm_override: Optional[float] = None,
    conf_threshold: float = 0.45,
    validar_bordes: bool = False
) -> Dict[str, Any]:
    alto_orig, ancho_orig = img_bgr.shape[:2]
    factor_resolucion = ancho_orig / float(DEFAULT_REF_WIDTH)
    base_ppc = pixels_per_cm_override if (pixels_per_cm_override and pixels_per_cm_override > 0) else DEFAULT_PIXELS_PER_CM_AT_1280
    pixels_per_cm_efectivo = base_ppc * factor_resolucion

    resultados = model.predict(source=img_bgr, conf=conf_threshold, verbose=False)
    if not resultados or len(resultados[0].boxes) == 0:
        raise ValueError("No se detectó ningún ejemplar porcino en la imagen.")

    res = resultados[0]
    boxes = res.boxes
    idx_mejor = int(np.argmax(boxes.conf.cpu().numpy()))
    confianza = float(boxes.conf[idx_mejor].cpu().numpy())
    x1, y1, x2, y2 = [float(v) for v in boxes.xyxy[idx_mejor].cpu().numpy()]

    if validar_bordes:
        margen_px = 6.0
        if x1 <= margen_px or y1 <= margen_px or x2 >= (ancho_orig - margen_px) or y2 >= (alto_orig - margen_px):
            raise ValueError("El porcino toca el borde del encuadre cenital. Capture cuando esté centrado.")

    img_anotada = img_bgr.copy()

    if res.masks is not None and len(res.masks.xy) > idx_mejor and len(res.masks.xy[idx_mejor]) >= 3:
        contorno_pts = np.array(res.masks.xy[idx_mejor], dtype=np.int32)
        area_px2 = float(cv2.contourArea(contorno_pts))
        (_, _), (dim1_px, dim2_px), _ = cv2.minAreaRect(contorno_pts)
        largo_px = float(max(dim1_px, dim2_px))
        ancho_px = float(min(dim1_px, dim2_px))
        cv2.polylines(img_anotada, [contorno_pts], isClosed=True, color=(16, 185, 129), thickness=3)
    else:
        dx_px = max(x2 - x1, 1.0)
        dy_px = max(y2 - y1, 1.0)
        largo_px = float(max(dx_px, dy_px))
        ancho_px = float(min(dx_px, dy_px))
        area_px2 = float(dx_px * dy_px * FACTOR_OCUPACION_DORSAL)

    largo_cm = round(largo_px / pixels_per_cm_efectivo, 2)
    ancho_cm = round(ancho_px / pixels_per_cm_efectivo, 2)
    area_cm2 = round(area_px2 / (pixels_per_cm_efectivo ** 2), 2)
    peso_estimado_kg = round(COEF_ALOMETRICO_A * (area_cm2 ** EXPONENTE_ALOMETRICO_B), 2)

    cv2.rectangle(img_anotada, (int(x1), int(y1)), (int(x2), int(y2)), (16, 185, 129), 3)
    etiqueta = f"Porcino: {confianza*100:.1f}% | {peso_estimado_kg} kg"
    cv2.putText(img_anotada, etiqueta, (int(x1), max(int(y1) - 10, 24)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (16, 185, 129), 2)

    _, buffer_jpg = cv2.imencode(".jpg", img_anotada, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    imagen_base64 = f"data:image/jpeg;base64,{base64.b64encode(buffer_jpg).decode('utf-8')}"

    return {
        "peso_estimado_kg": peso_estimado_kg,
        "confianza": round(confianza, 4),
        "area_cm2": area_cm2,
        "largo_cm": largo_cm,
        "ancho_cm": ancho_cm,
        "bbox": {
            "x1": round(x1, 1),
            "y1": round(y1, 1),
            "x2": round(x2, 1),
            "y2": round(y2, 1),
            "resolucion": f"{ancho_orig}x{alto_orig}",
            "pixels_per_cm": round(pixels_per_cm_efectivo, 3)
        },
        "imagen_anotada_base64": imagen_base64
    }

# Compatibilidad con módulos existentes
PESO_MAXIMO_FISIOLOGICO_KG = 500.0

def decode_image(image_bytes: bytes) -> np.ndarray:
    return decodificar_bytes_imagen_sync(image_bytes)

def process_pig_frame(model: Any, image_bgr: np.ndarray, conf_thresh: float = 0.45) -> Dict[str, Any]:
    if model is None:
        return {"detectado": False, "mensaje": "Modelo no cargado"}
    try:
        r = ejecutar_inferencia_morfometrica_sync(model, image_bgr, conf_threshold=conf_thresh)
        b = r["bbox"]
        return {
            "detectado": True,
            "confianza": r["confianza"],
            "peso_kg": r["peso_estimado_kg"],
            "area_cm2": r["area_cm2"],
            "largo_cm": r["largo_cm"],
            "ancho_cm": r["ancho_cm"],
            "bbox": {"x_min": int(b["x1"]), "y_min": int(b["y1"]), "x_max": int(b["x2"]), "y_max": int(b["y2"])},
            "imagen_anotada_base64": r["imagen_anotada_base64"],
            "mensaje": "Detección dorsal y pesaje morfométrico exitoso."
        }
    except Exception as e:
        return {"detectado": False, "mensaje": str(e)}
