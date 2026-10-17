import json
import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_optional, get_db
from app.models.user import Usuario
from app.schemas.dashboard import (
    DashboardAlertItem,
    DashboardKPIsResponse,
    RecentActivityItem,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/kpis",
    response_model=DashboardKPIsResponse,
    summary="Obtener KPIs consolidados del Dashboard",
    description="Calcula en tiempo real métricas de animales activos, corrales, peso promedio, GMD y alertas del sistema.",
)
async def get_dashboard_kpis(
    db: AsyncSession = Depends(get_db),
    current_user: Optional[Usuario] = Depends(get_current_user_optional),
) -> DashboardKPIsResponse:
    # 1. Ejecutar métricas consolidadas en una sola consulta CTE (reduce 6 round-trips a 1)
    query_kpis = text(
        """
        WITH metricas_animales AS (
            SELECT 
                COUNT(*) FILTER (WHERE deleted_at IS NULL AND estado NOT IN ('muerto', 'vendido')) AS total_activos,
                COALESCE(AVG(peso_actual_kg) FILTER (WHERE deleted_at IS NULL AND estado NOT IN ('muerto', 'vendido') AND peso_actual_kg > 0), 0) AS peso_promedio,
                COUNT(*) FILTER (WHERE corral_id IS NOT NULL AND deleted_at IS NULL AND estado NOT IN ('muerto', 'vendido')) AS asignados
            FROM animales
        ),
        metricas_corrales AS (
            SELECT 
                COUNT(*) AS total_activos,
                COALESCE(SUM(capacidad_maxima), 0) AS capacidad_total
            FROM corrales 
            WHERE activo = true AND deleted_at IS NULL
        ),
        serie_pesos AS (
            SELECT cagg.dia_bucket,
                   cagg.corral_id,
                   cagg.peso_promedio_kg,
                   lag(cagg.peso_promedio_kg) OVER (PARTITION BY cagg.corral_id ORDER BY cagg.dia_bucket) AS peso_anterior_kg,
                   lag(cagg.dia_bucket) OVER (PARTITION BY cagg.corral_id ORDER BY cagg.dia_bucket) AS fecha_anterior
            FROM cagg_pesos_diarios_corral cagg
        ),
        calculos_gmd AS (
            SELECT 
                ROUND(((peso_promedio_kg - peso_anterior_kg) / NULLIF(EXTRACT(epoch FROM (dia_bucket - fecha_anterior)) / 86400, 0))::numeric, 2) AS gmd_diaria
            FROM serie_pesos
            WHERE peso_anterior_kg IS NOT NULL
        ),
        metrica_gmd AS (
            SELECT COALESCE(
                AVG(gmd_diaria) FILTER (WHERE gmd_diaria > 0 AND gmd_diaria <= 2.0),
                0.81
            ) AS gmd_promedio
            FROM calculos_gmd
        ),
        metrica_sanidad AS (
            SELECT COUNT(*) AS alertas_sanitarias
            FROM sanidad_tratamientos 
            WHERE deleted_at IS NULL 
              AND (fecha_tratamiento + (tiempo_retiro_dias || ' days')::interval) > clock_timestamp()
        ),
        metrica_inventario AS (
            SELECT COUNT(*) AS alertas_stock
            FROM inventario_items 
            WHERE deleted_at IS NULL AND stock_actual <= stock_minimo
        )
        SELECT 
            a.total_activos,
            a.peso_promedio,
            a.asignados,
            c.total_activos AS total_corrales,
            c.capacidad_total,
            g.gmd_promedio,
            s.alertas_sanitarias,
            i.alertas_stock
        FROM metricas_animales a
        CROSS JOIN metricas_corrales c
        CROSS JOIN metrica_gmd g
        CROSS JOIN metrica_sanidad s
        CROSS JOIN metrica_inventario i;
        """
    )
    res_kpis = await db.execute(query_kpis)
    kpis = res_kpis.mappings().first() or {}

    total_animales = int(kpis.get("total_activos") or 0)
    peso_promedio_granja_kg = round(float(kpis.get("peso_promedio") or 0.0), 2)
    total_corrales_activos = int(kpis.get("total_corrales") or 0)
    capacidad_total = int(kpis.get("capacidad_total") or 0)
    animales_asignados = int(kpis.get("asignados") or 0)
    gmd_val = kpis.get("gmd_promedio")
    gmd_promedio_kg = round(float(gmd_val if gmd_val is not None else 0.81), 2)
    alertas_sanitarias = int(kpis.get("alertas_sanitarias") or 0)
    alertas_inventario_stock = int(kpis.get("alertas_stock") or 0)

    tasa_ocupacion_porcentaje = (
        round((animales_asignados / capacidad_total * 100), 1)
        if capacidad_total > 0
        else 0.0
    )

    # 2. Desglose de animales por etapa o estado
    query_etapas = text(
        """
        SELECT estado, COUNT(*) AS cantidad 
        FROM animales 
        WHERE deleted_at IS NULL AND estado NOT IN ('muerto', 'vendido')
        GROUP BY estado;
        """
    )
    res_etapas = await db.execute(query_etapas)
    filas_etapas = res_etapas.mappings().all()

    distribucion_etapas: Dict[str, int] = {
        "maternidad": 0,
        "precebo": 0,
        "levante": 0,
        "engorde": 0,
        "gestacion": 0,
    }
    for fe in filas_etapas:
        distribucion_etapas[str(fe["estado"])] = int(fe["cantidad"])

    return DashboardKPIsResponse(
        total_animales=total_animales,
        total_corrales_activos=total_corrales_activos,
        peso_promedio_granja_kg=peso_promedio_granja_kg,
        gmd_promedio_kg=gmd_promedio_kg,
        alertas_sanitarias=alertas_sanitarias,
        alertas_inventario_stock=alertas_inventario_stock,
        tasa_ocupacion_porcentaje=tasa_ocupacion_porcentaje,
        distribucion_etapas=distribucion_etapas,
    )


@router.get(
    "/recent-activity",
    response_model=List[RecentActivityItem],
    summary="Obtener registro de actividad reciente unificada",
    description="Retorna el historial unificado y cronológico de pesajes, sanidad, movimientos de inventario y altas de animales.",
)
async def get_recent_activity(
    limite: int = Query(10, ge=1, le=50, description="Cantidad máxima de actividades"),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[Usuario] = Depends(get_current_user_optional),
) -> List[RecentActivityItem]:
    query_activity = text(
        """
        WITH eventos AS (
            -- 1. Pesajes de animales
            SELECT 
                rp.id_registro::text AS id,
                'pesaje' AS tipo,
                CASE 
                    WHEN rp.metodo = 'vision_ai' THEN 'Pesaje con IA: #' || a.codigo_arete || COALESCE(' · ' || a.nombre_alias, '')
                    ELSE 'Pesaje Manual: #' || a.codigo_arete || COALESCE(' · ' || a.nombre_alias, '')
                END AS titulo,
                'Peso registrado: ' || ROUND(rp.peso_kg::numeric, 2) || ' kg en ' || COALESCE(c.codigo, 'Corral') AS descripcion,
                rp.tiempo AS tiempo,
                CASE WHEN rp.metodo = 'vision_ai' THEN 'Visión Artificial' ELSE 'Báscula Digital' END AS usuario,
                json_build_object(
                    'peso_kg', rp.peso_kg, 
                    'metodo', rp.metodo::text,
                    'confianza_ia', rp.confianza_ia
                ) AS metadata
            FROM registro_pesos rp
            LEFT JOIN animales a ON rp.id_cerdo = a.id
            LEFT JOIN corrales c ON rp.corral_id = c.id

            UNION ALL

            -- 2. Tratamientos y sanidad
            SELECT 
                st.id::text AS id,
                'sanidad' AS tipo,
                'Tratamiento: ' || st.producto_nombre AS titulo,
                COALESCE(st.tipo_evento, 'Evento sanitario') || ' - ' || COALESCE(st.diagnostico, 'Aplicación') AS descripcion,
                st.fecha_tratamiento AS tiempo,
                COALESCE(u.nombre || ' ' || u.apellido, 'Veterinario') AS usuario,
                json_build_object(
                    'producto', st.producto_nombre,
                    'dosis', st.dosis,
                    'unidad', st.unidad_dosis,
                    'retiro_dias', st.tiempo_retiro_dias
                ) AS metadata
            FROM sanidad_tratamientos st
            LEFT JOIN usuarios u ON st.veterinario_id = u.id
            WHERE st.deleted_at IS NULL

            UNION ALL

            -- 3. Movimientos de inventario
            SELECT 
                mi.id::text AS id,
                'inventario' AS tipo,
                'Movimiento de ' || ii.nombre AS titulo,
                CASE 
                    WHEN mi.tipo_movimiento = 'salida_consumo' THEN 'Consumo de ' || mi.cantidad || ' ' || ii.unidad_medida
                    WHEN mi.tipo_movimiento = 'entrada_compra' THEN 'Ingreso de ' || mi.cantidad || ' ' || ii.unidad_medida
                    ELSE mi.tipo_movimiento::text || ': ' || mi.cantidad || ' ' || ii.unidad_medida
                END AS descripcion,
                mi.fecha_movimiento AS tiempo,
                COALESCE(u.nombre || ' ' || u.apellido, 'Almacén') AS usuario,
                json_build_object(
                    'item', ii.nombre,
                    'cantidad', mi.cantidad,
                    'tipo_movimiento', mi.tipo_movimiento::text
                ) AS metadata
            FROM movimientos_inventario mi
            LEFT JOIN inventario_items ii ON mi.item_id = ii.id
            LEFT JOIN usuarios u ON mi.usuario_id = u.id

            UNION ALL

            -- 4. Nuevos animales registrados
            SELECT 
                a.id::text AS id,
                'animal' AS tipo,
                'Ingreso de animal #' || a.codigo_arete || COALESCE(' · ' || a.nombre_alias, '') AS titulo,
                'Raza: ' || a.raza || ', Arete: ' || a.codigo_arete || ', Sexo: ' || a.sexo::text AS descripcion,
                a.created_at AS tiempo,
                'Zootecnia' AS usuario,
                json_build_object(
                    'codigo_arete', a.codigo_arete,
                    'raza', a.raza,
                    'sexo', a.sexo::text
                ) AS metadata
            FROM animales a
            WHERE a.deleted_at IS NULL
        )
        SELECT id, tipo, titulo, descripcion, tiempo, usuario, metadata
        FROM eventos
        ORDER BY tiempo DESC
        LIMIT :limite;
        """
    )
    result = await db.execute(query_activity, {"limite": limite})
    rows = result.mappings().all()

    actividades: List[RecentActivityItem] = []
    for r in rows:
        meta = r["metadata"]
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = None

        actividades.append(
            RecentActivityItem(
                id=str(r["id"]),
                tipo=str(r["tipo"]),
                titulo=str(r["titulo"]),
                descripcion=str(r["descripcion"]),
                tiempo=r["tiempo"],
                usuario=str(r["usuario"]),
                metadata=meta,
            )
        )

    return actividades


@router.get(
    "/alerts",
    response_model=List[DashboardAlertItem],
    summary="Obtener alertas críticas y sugerencias operativas",
    description="Evalúa periodos de retiro farmacológico e inventario bajo mínimos, retornando alertas de acción inmediata.",
)
async def get_dashboard_alerts(
    db: AsyncSession = Depends(get_db),
    current_user: Optional[Usuario] = Depends(get_current_user_optional),
) -> List[DashboardAlertItem]:
    alerts: List[DashboardAlertItem] = []

    # 1. Alerta de Retiro Farmacológico Activo
    query_retiro = text(
        """
        SELECT COUNT(DISTINCT animal_id) AS animales_en_retiro
        FROM sanidad_tratamientos
        WHERE deleted_at IS NULL 
          AND (fecha_tratamiento + (tiempo_retiro_dias || ' days')::interval) > clock_timestamp();
        """
    )
    res_retiro = await db.execute(query_retiro)
    animales_en_retiro = int(res_retiro.scalar() or 0)

    if animales_en_retiro > 0:
        alerts.append(
            DashboardAlertItem(
                id="alert-sanidad-retiro",
                nivel="danger",
                modulo="sanidad",
                mensaje=f"{animales_en_retiro} animales con tiempo de retiro farmacológico activo.",
                accion_sugerida="Verificar en módulo de sanidad antes de programar beneficio o faenado.",
            )
        )

    # 2. Alerta de Stock Crítico en Inventario
    query_stock = text(
        """
        SELECT COUNT(*) AS items_criticos
        FROM inventario_items
        WHERE deleted_at IS NULL AND stock_actual <= stock_minimo;
        """
    )
    res_stock = await db.execute(query_stock)
    items_criticos = int(res_stock.scalar() or 0)

    if items_criticos > 0:
        alerts.append(
            DashboardAlertItem(
                id="alert-inventario-stock",
                nivel="warning",
                modulo="inventario",
                mensaje=f"{items_criticos} referencias con stock crítico en bodega.",
                accion_sugerida="Generar orden de compra de alimento o medicamentos de forma prioritaria.",
            )
        )

    # 3. Alerta de Próximos Partos y Traslado a Maternidad (Estrictamente Hembras)
    query_partos = text(
        """
        SELECT COUNT(DISTINCT s.hembra_id) AS hembras_por_parir
        FROM reproduccion_servicios s
        JOIN animales a ON s.hembra_id = a.id
        WHERE s.deleted_at IS NULL 
          AND a.deleted_at IS NULL
          AND LOWER(a.sexo::text) = 'hembra'
          AND s.estado_confirmacion = 'positiva'
          AND s.fecha_probable_parto BETWEEN CURRENT_DATE AND (CURRENT_DATE + INTERVAL '7 days');
        """
    )
    res_partos = await db.execute(query_partos)
    hembras_por_parir = int(res_partos.scalar() or 0)

    if hembras_por_parir > 0:
        alerts.append(
            DashboardAlertItem(
                id="alert-reproduccion-maternidad",
                nivel="warning",
                modulo="reproduccion",
                mensaje=f"{hembras_por_parir} hembras en ventana crítica de parto (próximos 7 días).",
                accion_sugerida="Programar traslado preventivo a corrales de paridera / maternidad.",
            )
        )

    return alerts
