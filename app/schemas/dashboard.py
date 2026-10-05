from datetime import datetime
from typing import Any, Dict, Optional
from pydantic import BaseModel, ConfigDict, Field


class DashboardKPIsResponse(BaseModel):
    total_animales: int = Field(..., description="Total de cerdos activos en la granja")
    total_corrales_activos: int = Field(..., description="Cantidad de corrales activos")
    peso_promedio_granja_kg: float = Field(..., description="Peso promedio actual de los animales en la granja")
    gmd_promedio_kg: float = Field(..., description="Ganancia Media Diaria promedio en kg/día")
    alertas_sanitarias: int = Field(..., description="Cantidad de tratamientos con retiro farmacológico activo")
    alertas_inventario_stock: int = Field(..., description="Cantidad de referencias con stock crítico")
    tasa_ocupacion_porcentaje: float = Field(..., description="Porcentaje de ocupación de corrales activos")
    distribucion_etapas: Dict[str, int] = Field(..., description="Conteo de animales por etapa productiva o estado")

    model_config = ConfigDict(from_attributes=True)


class RecentActivityItem(BaseModel):
    id: str = Field(..., description="Identificador único del evento o registro")
    tipo: str = Field(..., description="Categoría: 'pesaje' | 'sanidad' | 'alimentacion' | 'inventario' | 'animal'")
    titulo: str = Field(..., description="Título legible del evento")
    descripcion: str = Field(..., description="Descripción detallada de la acción realizada")
    tiempo: datetime = Field(..., description="Marca temporal del evento en zona horaria UTC")
    usuario: str = Field(..., description="Nombre del responsable o sistema que generó el evento")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Metadatos contextuales adicionales")

    model_config = ConfigDict(from_attributes=True)


class DashboardAlertItem(BaseModel):
    id: str = Field(..., description="Identificador único de la alerta")
    nivel: str = Field(..., description="Nivel de severidad: 'warning' | 'danger' | 'info'")
    modulo: str = Field(..., description="Módulo afectado: 'sanidad' | 'inventario' | 'manejo'")
    mensaje: str = Field(..., description="Mensaje explicativo de la condición de alerta")
    accion_sugerida: str = Field(..., description="Recomendación o acción operativa a tomar")

    model_config = ConfigDict(from_attributes=True)
