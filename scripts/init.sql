-- =============================================================================
-- ESQUEMA DDL INTEGRAL Y HOMOLOGADO - PORCITECH
-- Sistema Integral Porcino - ADSO SENA
-- Motor: PostgreSQL 16+ / TimescaleDB
-- =============================================================================

-- 1. EXTENSIONES
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "timescaledb" CASCADE;

-- 2. TIPOS ENUMERADOS (CREACIÓN Y EVOLUCIÓN IDEMPOTENTE)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'rol_usuario_enum') THEN
        CREATE TYPE rol_usuario_enum AS ENUM ('administrador', 'veterinario', 'operario');
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'sexo_animal_enum') THEN
        CREATE TYPE sexo_animal_enum AS ENUM ('macho', 'hembra');
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'estado_animal_enum') THEN
        CREATE TYPE estado_animal_enum AS ENUM (
            'activo', 'lactante', 'precebo', 'levante', 'engorde',
            'gestacion', 'cuarentena', 'vendido', 'muerto', 'enfermo',
            'descarte', 'reemplazo'
        );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'metodo_pesaje_enum') THEN
        CREATE TYPE metodo_pesaje_enum AS ENUM ('manual', 'vision_ai', 'ia_vision', 'bascula_digital');
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'tipo_fase_corral_enum') THEN
        CREATE TYPE tipo_fase_corral_enum AS ENUM (
            'maternidad', 'precebo', 'destete', 'levante', 'ceba',
            'engorde', 'cuarentena', 'gestacion'
        );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'estado_sincronizacion_enum') THEN
        CREATE TYPE estado_sincronizacion_enum AS ENUM (
            'pending', 'synced', 'conflict', 'error',
            'pendiente', 'sincronizado', 'conflicto'
        );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'tipo_movimiento_inv_enum') THEN
        CREATE TYPE tipo_movimiento_inv_enum AS ENUM (
            'entrada_compra', 'salida_consumo', 'ajuste_merma', 'devolucion'
        );
    END IF;
END $$;

-- Garantizar que bases de datos preexistentes reciban los nuevos valores ENUM
ALTER TYPE estado_animal_enum ADD VALUE IF NOT EXISTS 'enfermo';
ALTER TYPE estado_animal_enum ADD VALUE IF NOT EXISTS 'descarte';
ALTER TYPE estado_animal_enum ADD VALUE IF NOT EXISTS 'reemplazo';

ALTER TYPE metodo_pesaje_enum ADD VALUE IF NOT EXISTS 'ia_vision';

ALTER TYPE tipo_fase_corral_enum ADD VALUE IF NOT EXISTS 'destete';
ALTER TYPE tipo_fase_corral_enum ADD VALUE IF NOT EXISTS 'engorde';

ALTER TYPE estado_sincronizacion_enum ADD VALUE IF NOT EXISTS 'pendiente';
ALTER TYPE estado_sincronizacion_enum ADD VALUE IF NOT EXISTS 'sincronizado';
ALTER TYPE estado_sincronizacion_enum ADD VALUE IF NOT EXISTS 'conflicto';

-- 3. FUNCIÓN DE AUDITORÍA (TRIGGERS DE UPDATED_AT)
CREATE OR REPLACE FUNCTION fn_actualizar_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = clock_timestamp();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 4. TABLAS PRINCIPALES

-- 4.1 Usuarios
CREATE TABLE IF NOT EXISTS usuarios (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    nombre VARCHAR(100) NOT NULL,
    apellido VARCHAR(100) NOT NULL,
    email VARCHAR(150) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    rol rol_usuario_enum NOT NULL DEFAULT 'operario',
    telefono VARCHAR(20),
    activo BOOLEAN NOT NULL DEFAULT TRUE,
    id_local UUID,
    sync_status estado_sincronizacion_enum NOT NULL DEFAULT 'synced',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    deleted_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_usuarios_sync ON usuarios (sync_status, updated_at);
CREATE INDEX IF NOT EXISTS idx_usuarios_email_activo ON usuarios (email) WHERE deleted_at IS NULL;

-- 4.2 Corrales
CREATE TABLE IF NOT EXISTS corrales (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    codigo VARCHAR(50) NOT NULL UNIQUE,
    descripcion TEXT,
    fase tipo_fase_corral_enum NOT NULL,
    capacidad_maxima INT NOT NULL CHECK (capacidad_maxima > 0),
    fecha_inicio DATE NOT NULL DEFAULT CURRENT_DATE,
    fecha_cierre DATE,
    activo BOOLEAN NOT NULL DEFAULT TRUE,
    id_local UUID,
    sync_status estado_sincronizacion_enum NOT NULL DEFAULT 'synced',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    deleted_at TIMESTAMPTZ,
    CONSTRAINT chk_fechas_corral CHECK (fecha_cierre IS NULL OR fecha_cierre >= fecha_inicio)
);
CREATE INDEX IF NOT EXISTS idx_corrales_fase ON corrales (fase, activo) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_corrales_codigo ON corrales (codigo) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_corrales_sync ON corrales (sync_status, updated_at);

-- 4.3 Animales
CREATE TABLE IF NOT EXISTS animales (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    codigo_arete VARCHAR(50) NOT NULL UNIQUE,
    codigo_qr VARCHAR(100) NOT NULL UNIQUE,
    nombre_alias VARCHAR(80),
    sexo sexo_animal_enum NOT NULL,
    raza VARCHAR(60) NOT NULL,
    fecha_nacimiento DATE NOT NULL,
    estado estado_animal_enum NOT NULL DEFAULT 'activo',
    corral_id UUID REFERENCES corrales(id) ON DELETE SET NULL,
    id_padre UUID REFERENCES animales(id) ON DELETE SET NULL,
    id_madre UUID REFERENCES animales(id) ON DELETE SET NULL,
    peso_actual_kg NUMERIC(6,2) DEFAULT 0.00 CHECK (peso_actual_kg >= 0),
    foto_url TEXT,
    id_local UUID,
    sync_status estado_sincronizacion_enum NOT NULL DEFAULT 'synced',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    deleted_at TIMESTAMPTZ,
    CONSTRAINT chk_genealogia_distinta CHECK (id <> id_padre AND id <> id_madre)
);
CREATE INDEX IF NOT EXISTS idx_animales_corral ON animales (corral_id, estado) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_animales_arete ON animales (codigo_arete) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_animales_qr ON animales (codigo_qr) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_animales_sync ON animales (sync_status, updated_at);

-- 4.4 Categorías de Inventario
CREATE TABLE IF NOT EXISTS inventario_categorias (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    codigo VARCHAR(50) NOT NULL UNIQUE,
    nombre VARCHAR(100) NOT NULL,
    color VARCHAR(50) DEFAULT 'blue',
    descripcion TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

-- 4.5 Items de Inventario
CREATE TABLE IF NOT EXISTS inventario_items (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    categoria_id UUID NOT NULL REFERENCES inventario_categorias(id) ON DELETE RESTRICT,
    codigo_sku VARCHAR(60) NOT NULL UNIQUE,
    nombre VARCHAR(120) NOT NULL,
    unidad_medida VARCHAR(50) NOT NULL,
    factor_conversion_kg NUMERIC(8,3) NOT NULL DEFAULT 1.000 CHECK (factor_conversion_kg > 0),
    stock_actual NUMERIC(10,2) NOT NULL DEFAULT 0.00 CHECK (stock_actual >= 0),
    stock_minimo NUMERIC(10,2) NOT NULL DEFAULT 0.00 CHECK (stock_minimo >= 0),
    costo_unitario NUMERIC(12,2) NOT NULL DEFAULT 0.00 CHECK (costo_unitario >= 0),
    ubicacion_bodega VARCHAR(80),
    id_local UUID,
    sync_status estado_sincronizacion_enum NOT NULL DEFAULT 'synced',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    deleted_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_inventario_items_bajo_stock ON inventario_items (stock_actual, stock_minimo) WHERE deleted_at IS NULL;

-- 4.6 Lotes de Inventario
CREATE TABLE IF NOT EXISTS inventario_lotes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    item_id UUID NOT NULL REFERENCES inventario_items(id) ON DELETE CASCADE,
    numero_lote VARCHAR(100) NOT NULL,
    fecha_fabricacion DATE,
    fecha_vencimiento DATE,
    cantidad_inicial NUMERIC(10,2) NOT NULL CHECK (cantidad_inicial > 0),
    cantidad_disponible NUMERIC(10,2) NOT NULL DEFAULT 0.00 CHECK (cantidad_disponible >= 0),
    registro_ica VARCHAR(60),
    id_local UUID,
    sync_status estado_sincronizacion_enum NOT NULL DEFAULT 'synced',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    deleted_at TIMESTAMPTZ,
    CONSTRAINT chk_fechas_lote_inventario CHECK (
        fecha_vencimiento IS NULL OR fecha_fabricacion IS NULL OR fecha_vencimiento >= fecha_fabricacion
    )
);

-- 4.7 Movimientos de Inventario (Kardex)
CREATE TABLE IF NOT EXISTS movimientos_inventario (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    item_id UUID NOT NULL REFERENCES inventario_items(id) ON DELETE RESTRICT,
    lote_inventario_id UUID REFERENCES inventario_lotes(id) ON DELETE SET NULL,
    tipo_movimiento tipo_movimiento_inv_enum NOT NULL,
    cantidad NUMERIC(10,2) NOT NULL CHECK (cantidad > 0),
    costo_unitario NUMERIC(12,2) NOT NULL DEFAULT 0.00 CHECK (costo_unitario >= 0),
    usuario_id UUID NOT NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    fecha_movimiento TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    motivo VARCHAR(255),
    referencia_modulo VARCHAR(50),
    referencia_id UUID,
    id_local UUID,
    sync_status estado_sincronizacion_enum NOT NULL DEFAULT 'synced',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    deleted_at TIMESTAMPTZ
);

-- 4.8 Protocolos de Bioseguridad
CREATE TABLE IF NOT EXISTS protocolos_bioseguridad (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    codigo VARCHAR(30) NOT NULL UNIQUE,
    tipo_protocolo VARCHAR(30) NOT NULL CHECK (tipo_protocolo = ANY (ARRAY['estructural', 'operacional'])),
    tarea VARCHAR(120) NOT NULL,
    descripcion TEXT,
    icono VARCHAR(50),
    frecuencia VARCHAR(50) NOT NULL DEFAULT 'diaria',
    activo BOOLEAN NOT NULL DEFAULT TRUE,
    id_local UUID,
    sync_status estado_sincronizacion_enum NOT NULL DEFAULT 'synced',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    deleted_at TIMESTAMPTZ
);

-- 4.9 Ejecución de Bioseguridad
CREATE TABLE IF NOT EXISTS ejecucion_bioseguridad (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    protocolo_id UUID NOT NULL REFERENCES protocolos_bioseguridad(id) ON DELETE RESTRICT,
    operario_id UUID NOT NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    corral_id UUID REFERENCES corrales(id) ON DELETE SET NULL,
    fecha_registro TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    estado VARCHAR(50) NOT NULL DEFAULT 'completado',
    observaciones TEXT,
    id_local UUID,
    sync_status estado_sincronizacion_enum NOT NULL DEFAULT 'synced',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    deleted_at TIMESTAMPTZ
);

-- 4.10 Tratamientos y Sanidad
CREATE TABLE IF NOT EXISTS sanidad_tratamientos (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    animal_id UUID REFERENCES animales(id) ON DELETE CASCADE,
    corral_id UUID REFERENCES corrales(id) ON DELETE SET NULL,
    veterinario_id UUID NOT NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    medicamento_id UUID REFERENCES inventario_items(id) ON DELETE RESTRICT,
    lote_inventario_id UUID REFERENCES inventario_lotes(id) ON DELETE SET NULL,
    lote_biologico VARCHAR(100),
    tipo_evento VARCHAR(50) NOT NULL,
    producto_nombre VARCHAR(120) NOT NULL,
    diagnostico VARCHAR(150),
    dosis NUMERIC(8,2) CHECK (dosis IS NULL OR dosis >= 0),
    unidad_dosis VARCHAR(30),
    via_administracion VARCHAR(50),
    tiempo_retiro_dias INT NOT NULL DEFAULT 0 CHECK (tiempo_retiro_dias >= 0),
    fecha_tratamiento TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    fecha_proxima_dosis DATE,
    observaciones TEXT,
    id_local UUID,
    sync_status estado_sincronizacion_enum NOT NULL DEFAULT 'synced',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    deleted_at TIMESTAMPTZ,
    CONSTRAINT chk_sanidad_objetivo_valido CHECK (animal_id IS NOT NULL OR corral_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_tratamientos_animal ON sanidad_tratamientos (animal_id, fecha_tratamiento DESC);
CREATE INDEX IF NOT EXISTS idx_sanidad_retiro_activo ON sanidad_tratamientos (fecha_tratamiento DESC, tiempo_retiro_dias) WHERE deleted_at IS NULL AND tiempo_retiro_dias > 0;

-- 4.11 Alimentación y Raciones
CREATE TABLE IF NOT EXISTS alimentacion_raciones (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    corral_id UUID NOT NULL REFERENCES corrales(id) ON DELETE CASCADE,
    alimento_item_id UUID NOT NULL REFERENCES inventario_items(id) ON DELETE RESTRICT,
    operario_id UUID NOT NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    fase_alimentacion VARCHAR(50) NOT NULL,
    cantidad_kg NUMERIC(10,2) NOT NULL CHECK (cantidad_kg > 0),
    fecha_suministro TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    costo_total NUMERIC(12,2) NOT NULL DEFAULT 0.00 CHECK (costo_total >= 0),
    observaciones TEXT,
    id_local UUID,
    sync_status estado_sincronizacion_enum NOT NULL DEFAULT 'synced',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    deleted_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_alimentacion_corral ON alimentacion_raciones (corral_id, fecha_suministro DESC);
CREATE INDEX IF NOT EXISTS idx_alimentacion_brin_fecha ON alimentacion_raciones USING BRIN (fecha_suministro);

-- 4.12 Reproducción: Servicios
CREATE TABLE IF NOT EXISTS reproduccion_servicios (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    hembra_id UUID NOT NULL REFERENCES animales(id) ON DELETE CASCADE,
    macho_id UUID REFERENCES animales(id) ON DELETE SET NULL,
    tecnico_id UUID NOT NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    tipo_servicio VARCHAR(30) NOT NULL CHECK (tipo_servicio = ANY (ARRAY['inseminacion_artificial', 'monta_natural'])),
    codigo_pajilla_macho VARCHAR(80),
    fecha_servicio DATE NOT NULL,
    fecha_probable_parto DATE GENERATED ALWAYS AS ((fecha_servicio + '114 days'::interval)) STORED,
    estado_confirmacion VARCHAR(30) NOT NULL DEFAULT 'pendiente' CHECK (estado_confirmacion = ANY (ARRAY['pendiente', 'positiva', 'negativa', 'repetida'])),
    fecha_diagnostico DATE,
    observaciones TEXT,
    id_local UUID,
    sync_status estado_sincronizacion_enum NOT NULL DEFAULT 'synced',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    deleted_at TIMESTAMPTZ,
    CONSTRAINT chk_servicio_progenitores_distintos CHECK (macho_id IS NULL OR hembra_id <> macho_id)
);

-- 4.13 Reproducción: Partos
CREATE TABLE IF NOT EXISTS reproduccion_partos (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    servicio_id UUID UNIQUE REFERENCES reproduccion_servicios(id) ON DELETE CASCADE,
    hembra_id UUID NOT NULL REFERENCES animales(id) ON DELETE CASCADE,
    corral_maternidad_id UUID REFERENCES corrales(id) ON DELETE SET NULL,
    atendido_por UUID NOT NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    fecha_parto TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    numero_parto INT NOT NULL DEFAULT 1 CHECK (numero_parto >= 1),
    nacidos_vivos INT NOT NULL DEFAULT 0 CHECK (nacidos_vivos >= 0),
    nacidos_muertos INT NOT NULL DEFAULT 0 CHECK (nacidos_muertos >= 0),
    momias INT NOT NULL DEFAULT 0 CHECK (momias >= 0),
    peso_camada_total_kg NUMERIC(6,2) NOT NULL DEFAULT 0.00 CHECK (peso_camada_total_kg >= 0),
    observaciones TEXT,
    id_local UUID,
    sync_status estado_sincronizacion_enum NOT NULL DEFAULT 'synced',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    deleted_at TIMESTAMPTZ
);

-- 4.14 Reproducción: Destetes
CREATE TABLE IF NOT EXISTS reproduccion_destetes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    parto_id UUID UNIQUE REFERENCES reproduccion_partos(id) ON DELETE CASCADE,
    hembra_id UUID NOT NULL REFERENCES animales(id) ON DELETE CASCADE,
    corral_destino_id UUID REFERENCES corrales(id) ON DELETE SET NULL,
    operario_id UUID NOT NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    fecha_destete DATE NOT NULL DEFAULT CURRENT_DATE,
    lechones_destetados INT NOT NULL CHECK (lechones_destetados >= 0),
    peso_total_kg NUMERIC(6,2) NOT NULL CHECK (peso_total_kg >= 0),
    dias_lactancia INT CHECK (dias_lactancia >= 0),
    observaciones TEXT,
    id_local UUID,
    sync_status estado_sincronizacion_enum NOT NULL DEFAULT 'synced',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    deleted_at TIMESTAMPTZ
);

-- 4.15 Registro de Pesos (HIPERTABLA TIMESCALEDB)
CREATE TABLE IF NOT EXISTS registro_pesos (
    tiempo TIMESTAMPTZ NOT NULL,
    id_registro UUID NOT NULL DEFAULT uuid_generate_v4(),
    id_cerdo UUID NOT NULL REFERENCES animales(id) ON DELETE CASCADE,
    corral_id UUID NOT NULL REFERENCES corrales(id) ON DELETE CASCADE,
    peso_kg DOUBLE PRECISION NOT NULL CHECK (peso_kg > 0),
    metodo metodo_pesaje_enum NOT NULL,
    confianza_ia DOUBLE PRECISION CHECK (confianza_ia IS NULL OR (confianza_ia >= 0.0 AND confianza_ia <= 1.0)),
    area_cm2 DOUBLE PRECISION,
    largo_cm DOUBLE PRECISION,
    ancho_cm DOUBLE PRECISION,
    bbox JSONB,
    foto_evidencia_url TEXT,
    observaciones TEXT,
    id_local UUID,
    sync_status estado_sincronizacion_enum NOT NULL DEFAULT 'synced',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tiempo, id_registro, id_cerdo)
);

-- Conversión a Hipertabla de 7 días
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM timescaledb_information.hypertables WHERE hypertable_name = 'registro_pesos'
    ) THEN
        PERFORM create_hypertable('registro_pesos', by_range('tiempo', INTERVAL '7 days'));
    END IF;
END $$;

-- 4.16 Homologación Idempotente de Columnas para Bases de Datos Preexistentes
DO $$
BEGIN
    -- ejecucion_bioseguridad: migrar fecha_ejecucion -> fecha_registro y cumplido -> estado si vienen de versión previa
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'ejecucion_bioseguridad' AND column_name = 'fecha_ejecucion'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'ejecucion_bioseguridad' AND column_name = 'fecha_registro'
    ) THEN
        ALTER TABLE ejecucion_bioseguridad RENAME COLUMN fecha_ejecucion TO fecha_registro;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'ejecucion_bioseguridad' AND column_name = 'fecha_registro'
    ) THEN
        ALTER TABLE ejecucion_bioseguridad ADD COLUMN fecha_registro TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp();
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'ejecucion_bioseguridad' AND column_name = 'estado'
    ) THEN
        ALTER TABLE ejecucion_bioseguridad ADD COLUMN estado VARCHAR(50) NOT NULL DEFAULT 'completado';
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'ejecucion_bioseguridad' AND column_name = 'cumplido'
        ) THEN
            EXECUTE 'UPDATE ejecucion_bioseguridad SET estado = CASE WHEN cumplido THEN ''completado'' ELSE ''pendiente'' END';
        END IF;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'ejecucion_bioseguridad' AND column_name = 'updated_at'
    ) THEN
        ALTER TABLE ejecucion_bioseguridad ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp();
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'ejecucion_bioseguridad' AND column_name = 'deleted_at'
    ) THEN
        ALTER TABLE ejecucion_bioseguridad ADD COLUMN deleted_at TIMESTAMPTZ;
    END IF;

    -- Asegurar columnas añadidas en tablas preexistentes
    ALTER TABLE reproduccion_partos ALTER COLUMN servicio_id DROP NOT NULL;
    ALTER TABLE reproduccion_destetes ALTER COLUMN parto_id DROP NOT NULL;
    ALTER TABLE movimientos_inventario ALTER COLUMN motivo TYPE VARCHAR(255);

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'inventario_items' AND column_name = 'factor_conversion_kg'
    ) THEN
        ALTER TABLE inventario_items ADD COLUMN factor_conversion_kg NUMERIC(8,3) NOT NULL DEFAULT 1.000 CHECK (factor_conversion_kg > 0);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'sanidad_tratamientos' AND column_name = 'lote_inventario_id'
    ) THEN
        ALTER TABLE sanidad_tratamientos ADD COLUMN lote_inventario_id UUID REFERENCES inventario_lotes(id) ON DELETE SET NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'sanidad_tratamientos' AND column_name = 'lote_biologico'
    ) THEN
        ALTER TABLE sanidad_tratamientos ADD COLUMN lote_biologico VARCHAR(100);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'reproduccion_servicios' AND column_name = 'observaciones'
    ) THEN
        ALTER TABLE reproduccion_servicios ADD COLUMN observaciones TEXT;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'reproduccion_partos' AND column_name = 'numero_parto'
    ) THEN
        ALTER TABLE reproduccion_partos ADD COLUMN numero_parto INT NOT NULL DEFAULT 1 CHECK (numero_parto >= 1);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'reproduccion_destetes' AND column_name = 'observaciones'
    ) THEN
        ALTER TABLE reproduccion_destetes ADD COLUMN observaciones TEXT;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'registro_pesos' AND column_name = 'observaciones'
    ) THEN
        ALTER TABLE registro_pesos ADD COLUMN observaciones TEXT;
    END IF;
END $$;

-- Índices de alto rendimiento para Pesaje
CREATE INDEX IF NOT EXISTS idx_registro_pesos_busqueda ON registro_pesos (id_cerdo, tiempo DESC);
CREATE INDEX IF NOT EXISTS idx_registro_pesos_corral_tiempo ON registro_pesos (corral_id, tiempo DESC);
CREATE INDEX IF NOT EXISTS idx_registro_pesos_brin_tiempo ON registro_pesos USING BRIN (tiempo);

-- 5. VISTAS ANALÍTICAS Y CONTINUAS TIMESCALEDB

-- 5.1 Continuous Aggregate: cagg_pesos_diarios_corral
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM timescaledb_information.continuous_aggregates WHERE view_name = 'cagg_pesos_diarios_corral'
    ) THEN
        EXECUTE 'CREATE MATERIALIZED VIEW cagg_pesos_diarios_corral
        WITH (timescaledb.continuous) AS
        SELECT time_bucket(''1 day''::interval, tiempo) AS dia_bucket,
               corral_id,
               count(*) AS total_pesajes,
               round((avg(peso_kg))::numeric, 3) AS peso_promedio_kg,
               round((min(peso_kg))::numeric, 2) AS peso_minimo_kg,
               round((max(peso_kg))::numeric, 2) AS peso_maximo_kg
        FROM registro_pesos
        GROUP BY time_bucket(''1 day''::interval, tiempo), corral_id
        WITH NO DATA;';

        PERFORM add_continuous_aggregate_policy('cagg_pesos_diarios_corral',
            start_offset => INTERVAL '30 days',
            end_offset => INTERVAL '1 hour',
            schedule_interval => INTERVAL '1 hour');
    END IF;
END $$;

-- 5.2 Vista Analítica GMD: vista_gmd_diaria_corral (con cálculo real por días transcurridos)
CREATE OR REPLACE VIEW vista_gmd_diaria_corral AS
WITH serie_pesos AS (
    SELECT cagg.dia_bucket,
           cagg.corral_id,
           cagg.total_pesajes,
           cagg.peso_promedio_kg,
           cagg.peso_minimo_kg,
           cagg.peso_maximo_kg,
           lag(cagg.peso_promedio_kg) OVER (PARTITION BY cagg.corral_id ORDER BY cagg.dia_bucket) AS peso_anterior_kg,
           lag(cagg.dia_bucket) OVER (PARTITION BY cagg.corral_id ORDER BY cagg.dia_bucket) AS fecha_anterior
    FROM cagg_pesos_diarios_corral cagg
)
SELECT dia_bucket AS fecha,
       corral_id,
       total_pesajes,
       peso_promedio_kg,
       peso_minimo_kg,
       peso_maximo_kg,
       CASE
           WHEN peso_anterior_kg IS NOT NULL
                AND fecha_anterior IS NOT NULL
                AND EXTRACT(EPOCH FROM (dia_bucket - fecha_anterior)) > 0
           THEN round(
               (peso_promedio_kg - peso_anterior_kg)
               / GREATEST(1::numeric, (EXTRACT(EPOCH FROM (dia_bucket - fecha_anterior)) / 86400.0)::numeric),
               3
           )
           ELSE NULL
       END AS gmd_kg,
       CASE
           WHEN peso_anterior_kg IS NOT NULL AND peso_anterior_kg > 0::numeric
           THEN round((peso_promedio_kg - peso_anterior_kg) / peso_anterior_kg * 100::numeric, 2)
           ELSE 0.00
       END AS variacion_porcentual
FROM serie_pesos;

-- 6. ÍNDICES DE RENDIMIENTO ADICIONALES Y TRIGGERS DE AUDITORÍA
CREATE INDEX IF NOT EXISTS idx_inventario_lotes_item ON inventario_lotes (item_id);
CREATE INDEX IF NOT EXISTS idx_movimientos_inv_item_fecha ON movimientos_inventario (item_id, fecha_movimiento DESC);
CREATE INDEX IF NOT EXISTS idx_ejecucion_bio_fecha ON ejecucion_bioseguridad (protocolo_id, fecha_registro DESC);
CREATE INDEX IF NOT EXISTS idx_reprod_servicios_hembra ON reproduccion_servicios (hembra_id, estado_confirmacion, fecha_probable_parto);
CREATE INDEX IF NOT EXISTS idx_reprod_partos_hembra_fecha ON reproduccion_partos (hembra_id, fecha_parto DESC);
CREATE INDEX IF NOT EXISTS idx_reprod_destetes_hembra_fecha ON reproduccion_destetes (hembra_id, fecha_destete DESC);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_usuarios_updated_at') THEN
        CREATE TRIGGER trg_usuarios_updated_at BEFORE UPDATE ON usuarios FOR EACH ROW EXECUTE FUNCTION fn_actualizar_updated_at();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_corrales_updated_at') THEN
        CREATE TRIGGER trg_corrales_updated_at BEFORE UPDATE ON corrales FOR EACH ROW EXECUTE FUNCTION fn_actualizar_updated_at();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_animales_updated_at') THEN
        CREATE TRIGGER trg_animales_updated_at BEFORE UPDATE ON animales FOR EACH ROW EXECUTE FUNCTION fn_actualizar_updated_at();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_inventario_items_updated_at') THEN
        CREATE TRIGGER trg_inventario_items_updated_at BEFORE UPDATE ON inventario_items FOR EACH ROW EXECUTE FUNCTION fn_actualizar_updated_at();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_inventario_lotes_updated_at') THEN
        CREATE TRIGGER trg_inventario_lotes_updated_at BEFORE UPDATE ON inventario_lotes FOR EACH ROW EXECUTE FUNCTION fn_actualizar_updated_at();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_movimientos_inventario_updated_at') THEN
        CREATE TRIGGER trg_movimientos_inventario_updated_at BEFORE UPDATE ON movimientos_inventario FOR EACH ROW EXECUTE FUNCTION fn_actualizar_updated_at();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_protocolos_bioseguridad_updated_at') THEN
        CREATE TRIGGER trg_protocolos_bioseguridad_updated_at BEFORE UPDATE ON protocolos_bioseguridad FOR EACH ROW EXECUTE FUNCTION fn_actualizar_updated_at();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_ejecucion_bioseguridad_updated_at') THEN
        CREATE TRIGGER trg_ejecucion_bioseguridad_updated_at BEFORE UPDATE ON ejecucion_bioseguridad FOR EACH ROW EXECUTE FUNCTION fn_actualizar_updated_at();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_sanidad_tratamientos_updated_at') THEN
        CREATE TRIGGER trg_sanidad_tratamientos_updated_at BEFORE UPDATE ON sanidad_tratamientos FOR EACH ROW EXECUTE FUNCTION fn_actualizar_updated_at();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_alimentacion_raciones_updated_at') THEN
        CREATE TRIGGER trg_alimentacion_raciones_updated_at BEFORE UPDATE ON alimentacion_raciones FOR EACH ROW EXECUTE FUNCTION fn_actualizar_updated_at();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_reproduccion_servicios_updated_at') THEN
        CREATE TRIGGER trg_reproduccion_servicios_updated_at BEFORE UPDATE ON reproduccion_servicios FOR EACH ROW EXECUTE FUNCTION fn_actualizar_updated_at();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_reproduccion_partos_updated_at') THEN
        CREATE TRIGGER trg_reproduccion_partos_updated_at BEFORE UPDATE ON reproduccion_partos FOR EACH ROW EXECUTE FUNCTION fn_actualizar_updated_at();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_reproduccion_destetes_updated_at') THEN
        CREATE TRIGGER trg_reproduccion_destetes_updated_at BEFORE UPDATE ON reproduccion_destetes FOR EACH ROW EXECUTE FUNCTION fn_actualizar_updated_at();
    END IF;
END $$;

-- 7. USUARIO ADMINISTRADOR INICIAL (ÚNICA SEMILLA DEL SISTEMA)
INSERT INTO usuarios (id, nombre, apellido, email, password_hash, rol, telefono, activo, sync_status)
VALUES
    ('b1eebc99-9c0b-4ef8-bb6d-6bb9bd380a22', 'Julian', 'Barco', 'admin@sigep.com', '$2b$12$ikzt/pdSvBnKVf9bCVBXm.IjSTmoBDznRny/z8zi4Hjhd3M4p7CHC', 'administrador', '3101112233', true, 'synced')
ON CONFLICT (id) DO UPDATE SET
    nombre = EXCLUDED.nombre,
    apellido = EXCLUDED.apellido,
    email = EXCLUDED.email,
    password_hash = EXCLUDED.password_hash,
    rol = EXCLUDED.rol;
