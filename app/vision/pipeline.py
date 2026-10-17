import base64
from typing import Dict, Any, Optional, List
import cv2
import numpy as np
from ultralytics import YOLO

DEFAULT_REF_WIDTH = 1280
DEFAULT_PIXELS_PER_CM_AT_1280 = 15.2
COEF_ALOMETRICO_A = 0.0245
EXPONENTE_ALOMETRICO_B = 1.052
FACTOR_OCUPACION_DORSAL = 0.76
MIN_AREA_PIXELES = 3000.0
PESO_MAXIMO_FISIOLOGICO_KG = 500.0


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
    validar_bordes: bool = False,
) -> Dict[str, Any]:
    alto_orig, ancho_orig = img_bgr.shape[:2]
    factor_resolucion = ancho_orig / float(DEFAULT_REF_WIDTH)
    base_ppc = (
        pixels_per_cm_override
        if (pixels_per_cm_override and pixels_per_cm_override > 0)
        else DEFAULT_PIXELS_PER_CM_AT_1280
    )
    pixels_per_cm_efectivo = base_ppc * factor_resolucion

    resultados = model.predict(source=img_bgr, conf=conf_threshold, verbose=False)
    if not resultados or len(resultados[0].boxes) == 0:
        raise ValueError("No se detectó ningún ejemplar porcino en la imagen.")

    res = resultados[0]
    boxes = res.boxes
    tiene_mascaras = res.masks is not None and len(res.masks.xy) > 0

    img_anotada = img_bgr.copy()
    candidatos: List[Dict[str, Any]] = []
    descartados_borde = 0

    for idx in range(len(boxes)):
        confianza = float(boxes.conf[idx].cpu().numpy())
        x1, y1, x2, y2 = [float(v) for v in boxes.xyxy[idx].cpu().numpy()]

        if validar_bordes:
            margen_px = 6.0
            if (
                x1 <= margen_px
                or y1 <= margen_px
                or x2 >= (ancho_orig - margen_px)
                or y2 >= (alto_orig - margen_px)
            ):
                descartados_borde += 1
                continue

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

        largo_cm = round(largo_px / pixels_per_cm_efectivo, 2)
        ancho_cm = round(ancho_px / pixels_per_cm_efectivo, 2)
        area_cm2 = round(area_px2 / (pixels_per_cm_efectivo ** 2), 2)
        peso_estimado_kg = round(COEF_ALOMETRICO_A * (area_cm2 ** EXPONENTE_ALOMETRICO_B), 2)

        candidatos.append(
            {
                "confianza": round(confianza, 4),
                "peso_estimado_kg": peso_estimado_kg,
                "area_cm2": area_cm2,
                "largo_cm": largo_cm,
                "ancho_cm": ancho_cm,
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "contorno_pts": contorno_pts,
            }
        )

    if not candidatos:
        if descartados_borde > 0:
            raise ValueError("El porcino toca el borde del encuadre cenital. Capture cuando esté centrado.")
        raise ValueError("No se detectó ningún ejemplar porcino con área suficiente en la imagen.")

    candidatos.sort(key=lambda c: c["confianza"], reverse=True)
    principal = candidatos[0]

    for cand in candidatos:
        if cand["contorno_pts"] is not None:
            cv2.polylines(img_anotada, [cand["contorno_pts"]], isClosed=True, color=(16, 185, 129), thickness=3)
        cv2.rectangle(
            img_anotada,
            (int(cand["x1"]), int(cand["y1"])),
            (int(cand["x2"]), int(cand["y2"])),
            (16, 185, 129),
            3,
        )
        etiqueta = f"Porcino: {cand['confianza']*100:.1f}% | {cand['peso_estimado_kg']} kg"
        cv2.putText(
            img_anotada,
            etiqueta,
            (int(cand["x1"]), max(int(cand["y1"]) - 10, 24)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (16, 185, 129),
            2,
        )

    _, buffer_jpg = cv2.imencode(".jpg", img_anotada, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    imagen_base64 = f"data:image/jpeg;base64,{base64.b64encode(buffer_jpg).decode('utf-8')}"

    return {
        "peso_estimado_kg": principal["peso_estimado_kg"],
        "confianza": principal["confianza"],
        "area_cm2": principal["area_cm2"],
        "largo_cm": principal["largo_cm"],
        "ancho_cm": principal["ancho_cm"],
        "bbox": {
            "x1": round(principal["x1"], 1),
            "y1": round(principal["y1"], 1),
            "x2": round(principal["x2"], 1),
            "y2": round(principal["y2"], 1),
            "resolucion": f"{ancho_orig}x{alto_orig}",
            "pixels_per_cm": round(pixels_per_cm_efectivo, 3),
        },
        "imagen_anotada_base64": imagen_base64,
    }

