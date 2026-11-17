import base64
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2
import numpy as np
from ultralytics import YOLO

from app.core.config import settings

logger = logging.getLogger(__name__)

DEFAULT_REF_WIDTH = settings.DEFAULT_REF_WIDTH
DEFAULT_PIXELS_PER_CM_AT_1280 = settings.DEFAULT_PIXELS_PER_CM_AT_1280
COEF_ALOMETRICO_A = settings.COEF_ALOMETRICO_A
EXPONENTE_ALOMETRICO_B = settings.EXPONENTE_ALOMETRICO_B
FACTOR_OCUPACION_DORSAL = settings.FACTOR_OCUPACION_DORSAL
MIN_AREA_PIXELES = 3000.0
PESO_MAXIMO_FISIOLOGICO_KG = 500.0
DEFAULT_MARKER_SIZE_CM = settings.ARUCO_MARKER_SIZE_CM

# DICT_4X4_100 incluye los IDs 0..49 de DICT_4X4_50 y cumple HU20/HU21
ARUCO_DICTIONARIES = [
    ("DICT_4X4_100", cv2.aruco.DICT_4X4_100),
    ("DICT_4X4_50", cv2.aruco.DICT_4X4_50),
    ("DICT_5X5_100", cv2.aruco.DICT_5X5_100),
]


def _resolve_calibration_path() -> Path:
    cfg_path = settings.CAMERA_CALIBRATION_PATH
    if os.path.isabs(cfg_path):
        return Path(cfg_path)
    backend_root = Path(__file__).resolve().parent.parent.parent
    return backend_root / cfg_path


def cargar_calibracion_persistente() -> Optional[Dict[str, Any]]:
    """Carga los parámetros persistidos de calibración métrica ArUco desde disco."""
    calib_path = _resolve_calibration_path()
    if calib_path.exists():
        try:
            with open(calib_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("pixels_per_cm_base1280"):
                return data
        except Exception as exc:
            logger.warning("No se pudo leer calibración persistente %s: %s", calib_path, exc)
    return None


def guardar_calibracion_persistente(
    pixels_per_cm_1280: float,
    tamano_marcador_cm: float,
    marcador_id: int,
    resolucion: str,
    diccionario: str = "DICT_4X4_100",
) -> Dict[str, Any]:
    """Persiste la calibración métrica ArUco en weights/calibracion_camara.json."""
    calib_path = _resolve_calibration_path()
    calib_path.parent.mkdir(parents=True, exist_ok=True)
    datos = {
        "pixels_per_cm_base1280": round(float(pixels_per_cm_1280), 4),
        "tamano_marcador_cm": float(tamano_marcador_cm),
        "marcador_id": int(marcador_id),
        "diccionario_aruco": diccionario,
        "resolucion_calibracion": resolucion,
        "ancho_referencia": DEFAULT_REF_WIDTH,
        "fecha_calibracion": datetime.now(timezone.utc).isoformat(),
        "estado": "calibrado",
    }
    with open(calib_path, "w", encoding="utf-8") as f:
        json.dump(datos, f, indent=2, ensure_ascii=False)
    return datos


def decodificar_bytes_imagen_sync(file_bytes: bytes) -> np.ndarray:
    arr = np.frombuffer(file_bytes, dtype=np.uint8)
    img_bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img_bgr is None or img_bgr.size == 0:
        raise ValueError("El archivo enviado no es una imagen válida.")
    return img_bgr


def detectar_marcador_aruco(
    frame: np.ndarray,
    tamano_marcador_cm: float = DEFAULT_MARKER_SIZE_CM,
    diccionario_id: Optional[int] = None,
    dibujar: bool = True,
) -> Optional[Dict[str, Any]]:
    """
    Detecta un marcador fiducial ArUco (DICT_4X4_100 / DICT_4X4_50) en el fotograma
    y calcula el factor de escala exacto px/cm normalizado a base 1280px.
    """
    if frame is None or frame.size == 0 or tamano_marcador_cm <= 0:
        return None

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    detector_params = cv2.aruco.DetectorParameters()

    candidates = (
        [("CUSTOM", diccionario_id)]
        if diccionario_id is not None
        else ARUCO_DICTIONARIES
    )

    for dict_name, dict_const in candidates:
        try:
            aruco_dict = cv2.aruco.getPredefinedDictionary(dict_const)
            detector = cv2.aruco.ArucoDetector(aruco_dict, detector_params)
            esquinas, ids, _ = detector.detectMarkers(gray)
        except Exception:
            continue

        if ids is not None and len(esquinas) > 0:
            pts = esquinas[0][0]
            marker_id = int(ids.flatten()[0])

            lado_px_1 = float(np.linalg.norm(pts[0] - pts[1]))
            lado_px_2 = float(np.linalg.norm(pts[1] - pts[2]))
            lado_px_3 = float(np.linalg.norm(pts[2] - pts[3]))
            lado_px_4 = float(np.linalg.norm(pts[3] - pts[0]))
            lado_px_promedio = (lado_px_1 + lado_px_2 + lado_px_3 + lado_px_4) / 4.0

            if lado_px_promedio > 10.0:
                ppc_medido = float(lado_px_promedio / tamano_marcador_cm)
                _, ancho = frame.shape[:2]
                ppc_base1280 = ppc_medido / (ancho / float(DEFAULT_REF_WIDTH))

                if dibujar:
                    cv2.aruco.drawDetectedMarkers(frame, esquinas, ids, borderColor=(255, 255, 0))
                    cv2.putText(
                        frame,
                        f"ArUco ID {marker_id}: {ppc_medido:.2f} px/cm",
                        (int(pts[0][0]), max(int(pts[0][1]) - 15, 25)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.65,
                        (255, 255, 0),
                        2,
                    )

                return {
                    "pixels_per_cm_medido": round(ppc_medido, 4),
                    "pixels_per_cm_base1280": round(ppc_base1280, 4),
                    "marcador_id": marker_id,
                    "diccionario": dict_name,
                    "lado_px_promedio": round(lado_px_promedio, 2),
                }

    return None


def calcular_momentos_hu(contorno: np.ndarray) -> List[float]:
    """Calcula los 7 Momentos Invariantes de Hu en escala logarítmica simétrica."""
    m = cv2.moments(contorno)
    hu = cv2.HuMoments(m).flatten()
    hu_log: List[float] = []
    for h in hu:
        if abs(h) > 1e-15:
            hu_log.append(round(-float(np.sign(h) * np.log10(abs(h))), 4))
        else:
            hu_log.append(0.0)
    return hu_log


def calcular_ancho_toracico_px(contorno: np.ndarray, largo_px: float, ancho_px: float) -> float:
    """
    Calcula el ancho torácico dorsal mediante corte transversal en la franja
    anterior-media de la caja torácica (entre el 30% y 45% de la longitud corporal).
    """
    try:
        rect = cv2.minAreaRect(contorno)
        (cx, cy), (w, h), angulo = rect

        if w < h:
            angulo += 90.0

        mat_rot = cv2.getRotationMatrix2D((cx, cy), angulo, 1.0)
        pts = contorno.reshape(-1, 2)
        pts_rot = cv2.transform(np.array([pts]), mat_rot)[0]

        x_min, x_max = np.min(pts_rot[:, 0]), np.max(pts_rot[:, 0])
        longitud_real = x_max - x_min
        if longitud_real <= 0:
            return float(ancho_px)

        x_inicio = x_min + 0.30 * longitud_real
        x_fin = x_min + 0.45 * longitud_real
        pts_torax = pts_rot[(pts_rot[:, 0] >= x_inicio) & (pts_rot[:, 0] <= x_fin)]

        if len(pts_torax) >= 2:
            ancho_torax_px = float(np.max(pts_torax[:, 1]) - np.min(pts_torax[:, 1]))
            if 0.4 * ancho_px <= ancho_torax_px <= 1.4 * ancho_px:
                return round(ancho_torax_px, 2)

        return round(float(ancho_px) * 0.92, 2)
    except Exception:
        return round(float(ancho_px) * 0.92, 2)


def _estimar_peso_hibrido(
    xgb_model: Optional[Any],
    vector_xgboost: List[float],
    area_cm2: float,
) -> Dict[str, Any]:
    """
    Ejecuta regresión con XGBoost entrenado (12 features morfométricas) con
    fallback automático a la ecuación alométrica de Huxley si XGBoost no está cargado
    o retorna un valor fuera de rango fisiológico.
    """
    peso_huxley = round(COEF_ALOMETRICO_A * (area_cm2 ** EXPONENTE_ALOMETRICO_B), 2)

    if xgb_model is not None:
        try:
            import xgboost as xgb

            dmatrix = xgb.DMatrix(np.array([vector_xgboost], dtype=np.float32))
            peso_predicho = float(xgb_model.predict(dmatrix)[0])
            if 1.0 <= peso_predicho <= PESO_MAXIMO_FISIOLOGICO_KG:
                return {
                    "peso_estimado_kg": round(peso_predicho, 2),
                    "algoritmo_peso": "xgboost",
                    "peso_huxley_referencia_kg": peso_huxley,
                }
        except Exception as exc:
            logger.warning("Fallo en predicción XGBoost (%s); usando fallback Huxley.", exc)

    return {
        "peso_estimado_kg": peso_huxley,
        "algoritmo_peso": "alomorfico_huxley",
        "peso_huxley_referencia_kg": peso_huxley,
    }


def ejecutar_inferencia_morfometrica_sync(
    model: YOLO,
    img_bgr: np.ndarray,
    pixels_per_cm_override: Optional[float] = None,
    conf_threshold: float = 0.45,
    validar_bordes: bool = False,
    xgb_model: Optional[Any] = None,
    tamano_marcador_cm: float = DEFAULT_MARKER_SIZE_CM,
) -> Dict[str, Any]:
    alto_orig, ancho_orig = img_bgr.shape[:2]
    img_anotada = img_bgr.copy()

    # 1. Resolución de escala métrica (Prioridad: ArUco en vivo > Override manual > Calibración guardada > Fábrica)
    metodo_calibracion = "fabrica_cenital"
    base_ppc = DEFAULT_PIXELS_PER_CM_AT_1280
    aruco_detectado = False
    aruco_id: Optional[int] = None

    resultado_aruco = detectar_marcador_aruco(
        img_anotada,
        tamano_marcador_cm=tamano_marcador_cm,
        dibujar=True,
    )
    if resultado_aruco is not None:
        base_ppc = resultado_aruco["pixels_per_cm_base1280"]
        metodo_calibracion = "aruco_en_vivo"
        aruco_detectado = True
        aruco_id = resultado_aruco["marcador_id"]
    elif pixels_per_cm_override and pixels_per_cm_override > 0:
        base_ppc = float(pixels_per_cm_override)
        metodo_calibracion = "manual_parametro"
    else:
        calib = cargar_calibracion_persistente()
        if calib and calib.get("pixels_per_cm_base1280"):
            base_ppc = float(calib["pixels_per_cm_base1280"])
            metodo_calibracion = "aruco_guardado"
            aruco_id = calib.get("marcador_id")

    factor_resolucion = ancho_orig / float(DEFAULT_REF_WIDTH)
    pixels_per_cm_efectivo = base_ppc * factor_resolucion

    # 2. Segmentación dorsal con YOLO
    resultados = model.predict(source=img_bgr, conf=conf_threshold, verbose=False)
    if not resultados or len(resultados[0].boxes) == 0:
        raise ValueError("No se detectó ningún ejemplar porcino en la imagen.")

    res = resultados[0]
    boxes = res.boxes
    tiene_mascaras = res.masks is not None and len(res.masks.xy) > 0

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

        est = _estimar_peso_hibrido(xgb_model, vector_xgboost, area_cm2)

        candidatos.append(
            {
                "confianza": round(confianza, 4),
                "peso_estimado_kg": est["peso_estimado_kg"],
                "algoritmo_peso": est["algoritmo_peso"],
                "peso_huxley_referencia_kg": est["peso_huxley_referencia_kg"],
                "area_cm2": area_cm2,
                "largo_cm": largo_cm,
                "ancho_cm": ancho_cm,
                "ancho_toracico_cm": ancho_toracico_cm,
                "ratio_aspecto": ratio_aspecto,
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
        algo_tag = "XGB" if cand["algoritmo_peso"] == "xgboost" else "Huxley"
        etiqueta = f"Porcino: {cand['confianza']*100:.1f}% | {cand['peso_estimado_kg']} kg ({algo_tag})"
        cv2.putText(
            img_anotada,
            etiqueta,
            (int(cand["x1"]), max(int(cand["y1"]) - 10, 24)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (16, 185, 129),
            2,
        )

    _, buffer_jpg = cv2.imencode(".jpg", img_anotada, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    imagen_base64 = f"data:image/jpeg;base64,{base64.b64encode(buffer_jpg).decode('utf-8')}"

    return {
        "peso_estimado_kg": principal["peso_estimado_kg"],
        "algoritmo_peso": principal["algoritmo_peso"],
        "peso_huxley_referencia_kg": principal["peso_huxley_referencia_kg"],
        "metodo_calibracion": metodo_calibracion,
        "pixels_per_cm_utilizado": round(pixels_per_cm_efectivo, 3),
        "aruco_detectado": aruco_detectado,
        "aruco_id": aruco_id,
        "confianza": principal["confianza"],
        "area_cm2": principal["area_cm2"],
        "largo_cm": principal["largo_cm"],
        "ancho_cm": principal["ancho_cm"],
        "ancho_toracico_cm": principal["ancho_toracico_cm"],
        "bbox": {
            "x1": round(principal["x1"], 1),
            "y1": round(principal["y1"], 1),
            "x2": round(principal["x2"], 1),
            "y2": round(principal["y2"], 1),
            "resolucion": f"{ancho_orig}x{alto_orig}",
            "pixels_per_cm": round(pixels_per_cm_efectivo, 3),
            "algoritmo_peso": principal["algoritmo_peso"],
            "metodo_calibracion": metodo_calibracion,
            "aruco_detectado": aruco_detectado,
            "aruco_id": aruco_id,
            "ancho_toracico_cm": principal["ancho_toracico_cm"],
        },
        "imagen_anotada_base64": imagen_base64,
    }
