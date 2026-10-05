-- =============================================================================
-- ESQUEMA DDL INTEGRAL Y DATOS SEMILLA - PORCITECH
-- Sistema Integral Porcino - ADSO SENA
-- =============================================================================

-- 1. EXTENSIONES
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "timescaledb" CASCADE;

-- 2. TIPOS ENUMERADOS (IDEMPOTENTES)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'rol_usuario_enum') THEN
        CREATE TYPE rol_usuario_enum AS ENUM ('administrador', 'veterinario', 'operario');
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'sexo_animal_enum') THEN
        CREATE TYPE sexo_animal_enum AS ENUM ('macho', 'hembra');
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'estado_animal_enum') THEN
        CREATE TYPE estado_animal_enum AS ENUM ('activo', 'lactante', 'precebo', 'levante', 'engorde', 'gestacion', 'cuarentena', 'vendido', 'muerto', 'enfermo');
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'metodo_pesaje_enum') THEN
        CREATE TYPE metodo_pesaje_enum AS ENUM ('manual', 'vision_ai', 'ia_vision', 'bascula_digital');
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'tipo_fase_corral_enum') THEN
        CREATE TYPE tipo_fase_corral_enum AS ENUM ('maternidad', 'precebo', 'levante', 'ceba', 'cuarentena', 'gestacion');
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'estado_sincronizacion_enum') THEN
        CREATE TYPE estado_sincronizacion_enum AS ENUM ('pending', 'synced', 'conflict', 'error', 'pendiente', 'sincronizado', 'conflicto');
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'tipo_movimiento_inv_enum') THEN
        CREATE TYPE tipo_movimiento_inv_enum AS ENUM ('entrada_compra', 'salida_consumo', 'ajuste_merma', 'devolucion');
    END IF;
END $$;

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
CREATE INDEX IF NOT EXISTS idx_corrales_fase ON corrales (fase, activo);
CREATE INDEX IF NOT EXISTS idx_corrales_sync ON corrales (sync_status, updated_at);

-- 4.3 Animales
CREATE TABLE IF NOT EXISTS animales (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    codigo_arete VARCHAR(50) NOT NULL UNIQUE,
    codigo_qr VARCHAR(100) UNIQUE,
    nombre_alias VARCHAR(80),
    sexo sexo_animal_enum NOT NULL,
    raza VARCHAR(60) NOT NULL,
    fecha_nacimiento DATE NOT NULL,
    estado estado_animal_enum NOT NULL DEFAULT 'activo',
    corral_id UUID REFERENCES corrales(id) ON DELETE SET NULL,
    id_padre UUID REFERENCES animales(id) ON DELETE SET NULL,
    id_madre UUID REFERENCES animales(id) ON DELETE SET NULL,
    peso_actual_kg NUMERIC(6,2) DEFAULT 0.00,
    foto_url TEXT,
    id_local UUID,
    sync_status estado_sincronizacion_enum NOT NULL DEFAULT 'synced',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    deleted_at TIMESTAMPTZ,
    CONSTRAINT chk_genealogia_distinta CHECK (id <> id_padre AND id <> id_madre)
);
CREATE INDEX IF NOT EXISTS idx_animales_corral ON animales (corral_id, estado);
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
    stock_actual NUMERIC(10,2) NOT NULL DEFAULT 0.00 CHECK (stock_actual >= 0),
    stock_minimo NUMERIC(10,2) NOT NULL DEFAULT 0.00 CHECK (stock_minimo >= 0),
    costo_unitario NUMERIC(12,2) NOT NULL DEFAULT 0.00,
    ubicacion_bodega VARCHAR(80),
    id_local UUID,
    sync_status estado_sincronizacion_enum NOT NULL DEFAULT 'synced',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    deleted_at TIMESTAMPTZ
);

-- 4.6 Lotes de Inventario
CREATE TABLE IF NOT EXISTS inventario_lotes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    item_id UUID NOT NULL REFERENCES inventario_items(id) ON DELETE CASCADE,
    numero_lote VARCHAR(100) NOT NULL,
    fecha_fabricacion DATE,
    fecha_vencimiento DATE,
    cantidad_inicial NUMERIC(10,2) NOT NULL,
    cantidad_actual NUMERIC(10,2) NOT NULL CHECK (cantidad_actual >= 0),
    costo_unitario NUMERIC(12,2) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

-- 4.7 Movimientos de Inventario
CREATE TABLE IF NOT EXISTS movimientos_inventario (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    item_id UUID NOT NULL REFERENCES inventario_items(id) ON DELETE RESTRICT,
    lote_id UUID REFERENCES inventario_lotes(id) ON DELETE SET NULL,
    tipo_movimiento tipo_movimiento_inv_enum NOT NULL,
    cantidad NUMERIC(10,2) NOT NULL CHECK (cantidad > 0),
    costo_total NUMERIC(12,2) NOT NULL DEFAULT 0.00,
    usuario_id UUID NOT NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    fecha_movimiento TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    motivo TEXT,
    id_local UUID,
    sync_status estado_sincronizacion_enum NOT NULL DEFAULT 'synced',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
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
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

-- 4.10 Tratamientos y Sanidad
CREATE TABLE IF NOT EXISTS sanidad_tratamientos (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    animal_id UUID REFERENCES animales(id) ON DELETE CASCADE,
    corral_id UUID REFERENCES corrales(id) ON DELETE SET NULL,
    veterinario_id UUID NOT NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    medicamento_id UUID REFERENCES inventario_items(id) ON DELETE RESTRICT,
    tipo_evento VARCHAR(50) NOT NULL,
    producto_nombre VARCHAR(120) NOT NULL,
    diagnostico VARCHAR(150),
    dosis NUMERIC(8,2),
    unidad_dosis VARCHAR(30),
    via_administracion VARCHAR(50),
    tiempo_retiro_dias INT DEFAULT 0 CHECK (tiempo_retiro_dias >= 0),
    fecha_tratamiento TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    fecha_proxima_dosis DATE,
    observaciones TEXT,
    id_local UUID,
    sync_status estado_sincronizacion_enum NOT NULL DEFAULT 'synced',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    deleted_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_tratamientos_animal ON sanidad_tratamientos (animal_id, fecha_tratamiento DESC);

-- 4.11 Alimentación y Raciones
CREATE TABLE IF NOT EXISTS alimentacion_raciones (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    corral_id UUID NOT NULL REFERENCES corrales(id) ON DELETE CASCADE,
    alimento_item_id UUID NOT NULL REFERENCES inventario_items(id) ON DELETE RESTRICT,
    operario_id UUID NOT NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    fase_alimentacion VARCHAR(50) NOT NULL,
    cantidad_kg NUMERIC(10,2) NOT NULL CHECK (cantidad_kg > 0),
    fecha_suministro TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    costo_total NUMERIC(12,2) DEFAULT 0.00,
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
    id_local UUID,
    sync_status estado_sincronizacion_enum NOT NULL DEFAULT 'synced',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    deleted_at TIMESTAMPTZ
);

-- 4.13 Reproducción: Partos
CREATE TABLE IF NOT EXISTS reproduccion_partos (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    servicio_id UUID UNIQUE REFERENCES reproduccion_servicios(id) ON DELETE CASCADE,
    hembra_id UUID NOT NULL REFERENCES animales(id) ON DELETE CASCADE,
    corral_maternidad_id UUID REFERENCES corrales(id) ON DELETE SET NULL,
    atendido_por UUID NOT NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    fecha_parto TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    nacidos_vivos INT NOT NULL DEFAULT 0 CHECK (nacidos_vivos >= 0),
    nacidos_muertos INT NOT NULL DEFAULT 0 CHECK (nacidos_muertos >= 0),
    momias INT NOT NULL DEFAULT 0 CHECK (momias >= 0),
    peso_camada_total_kg NUMERIC(6,2) NOT NULL DEFAULT 0.00,
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

-- 5.2 Vista Analítica GMD: vista_gmd_diaria_corral
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
       round(peso_promedio_kg - peso_anterior_kg, 3) AS gmd_kg,
       CASE
           WHEN peso_anterior_kg IS NOT NULL AND peso_anterior_kg > 0::numeric 
           THEN round((peso_promedio_kg - peso_anterior_kg) / peso_anterior_kg * 100::numeric, 2)
           ELSE 0.00
       END AS variacion_porcentual
FROM serie_pesos;

-- 6. TRIGGERS DE AUDITORÍA
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
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_protocolos_bioseguridad_updated_at') THEN
        CREATE TRIGGER trg_protocolos_bioseguridad_updated_at BEFORE UPDATE ON protocolos_bioseguridad FOR EACH ROW EXECUTE FUNCTION fn_actualizar_updated_at();
    END IF;
END $$;

-- 7. DATOS SEMILLA (INSERT ON CONFLICT DO NOTHING)

-- 7.1 Usuarios (Contraseñas Hasheadas con Bcrypt: admin123, vet123, ope123)
INSERT INTO usuarios (id, nombre, apellido, email, password_hash, rol, telefono, activo, sync_status)
VALUES 
    ('b1eebc99-9c0b-4ef8-bb6d-6bb9bd380a22', 'Julian', 'Barco', 'admin@sigep.com', '$2b$12$ikzt/pdSvBnKVf9bCVBXm.IjSTmoBDznRny/z8zi4Hjhd3M4p7CHC', 'administrador', '3101112233', true, 'synced'),
    ('b2eebc99-9c0b-4ef8-bb6d-6bb9bd380a23', 'Maria', 'Lopez', 'vet@sigep.com', '$2b$12$bo8AcLlfqvgp0Z7EaCXqxu9SOGM4.U5C8quE4SPm6FzwE082h7mEG', 'veterinario', '3114445566', true, 'synced'),
    ('b3eebc99-9c0b-4ef8-bb6d-6bb9bd380a24', 'Juan', 'Perez', 'operario@sigep.com', '$2b$12$NEZVVQDPwmilCPHu.8Uzh.5NU2aZFjZwJyxIK.Z/v3q7WSwi5zJh2', 'operario', '3127778899', true, 'synced')
ON CONFLICT (id) DO UPDATE SET
    nombre = EXCLUDED.nombre,
    apellido = EXCLUDED.apellido,
    email = EXCLUDED.email,
    password_hash = EXCLUDED.password_hash,
    rol = EXCLUDED.rol;

-- 7.2 Corrales
INSERT INTO corrales (id, codigo, descripcion, fase, capacidad_maxima, fecha_inicio, activo, sync_status)
VALUES
    ('c1eebc99-9c0b-4ef8-bb6d-6bb9bd380a31', 'CORRAL-01', 'Lote Maternidad y Parideras Norte', 'maternidad', 12, '2026-01-01', true, 'synced'),
    ('c2eebc99-9c0b-4ef8-bb6d-6bb9bd380a32', 'CORRAL-09', 'Lote Precebo - Transición Temprana', 'precebo', 30, '2026-02-15', true, 'synced'),
    ('c3eebc99-9c0b-4ef8-bb6d-6bb9bd380a33', 'LOTE-42', 'Lote Engorde Final - Galpón 3 Ceba', 'ceba', 25, '2026-03-01', true, 'synced'),
    ('c4eebc99-9c0b-4ef8-bb6d-6bb9bd380a34', 'LOTE-15', 'Lote Levante y Crecimiento', 'levante', 25, '2026-02-01', true, 'synced'),
    ('c5eebc99-9c0b-4ef8-bb6d-6bb9bd380a35', 'SECTOR-A12', 'Corral Gestación y Reemplazo', 'gestacion', 15, '2026-01-10', true, 'synced')
ON CONFLICT (id) DO NOTHING;

-- 7.3 Categorías de Inventario
INSERT INTO inventario_categorias (id, codigo, nombre, color, descripcion)
VALUES
    ('e1eebc99-9c0b-4ef8-bb6d-6bb9bd380a51', 'alimento', 'Alimento Concentrado', 'blue', 'Dietas y piensos balanceados por etapa'),
    ('e2eebc99-9c0b-4ef8-bb6d-6bb9bd380a52', 'medicamento', 'Medicamentos y Biológicos', 'emerald', 'Antibióticos, vacunas y antiparasitarios'),
    ('e3eebc99-9c0b-4ef8-bb6d-6bb9bd380a53', 'bioseguridad', 'Material e Higiene', 'purple', 'Desinfectantes y elementos sanitarios'),
    ('e4eebc99-9c0b-4ef8-bb6d-6bb9bd380a54', 'suplemento', 'Suplementos y Minerales', 'orange', 'Electrolitos y vitaminas')
ON CONFLICT (id) DO NOTHING;

-- 7.4 Items de Inventario
INSERT INTO inventario_items (id, categoria_id, codigo_sku, nombre, unidad_medida, stock_actual, stock_minimo, costo_unitario, ubicacion_bodega, sync_status)
VALUES
    ('f1eebc99-9c0b-4ef8-bb6d-6bb9bd380a61', 'e1eebc99-9c0b-4ef8-bb6d-6bb9bd380a51', 'INS-001', 'Iniciación Lechones Precebo 1', 'Bultos (40kg)', 85.00, 20.00, 98000.00, 'Silo 1', 'synced'),
    ('f2eebc99-9c0b-4ef8-bb6d-6bb9bd380a62', 'e1eebc99-9c0b-4ef8-bb6d-6bb9bd380a51', 'INS-002', 'Ceba Finalización Harina Forte', 'Bultos (40kg)', 120.00, 30.00, 89000.00, 'Silo 2', 'synced'),
    ('f3eebc99-9c0b-4ef8-bb6d-6bb9bd380a63', 'e2eebc99-9c0b-4ef8-bb6d-6bb9bd380a52', 'INS-004', 'Ivermectina 1% Antiparasitario', 'Frascos (250ml)', 15.00, 5.00, 42000.00, 'Botiquín A', 'synced'),
    ('f4eebc99-9c0b-4ef8-bb6d-6bb9bd380a64', 'e2eebc99-9c0b-4ef8-bb6d-6bb9bd380a52', 'INS-005', 'Vacuna Peste Porcina Clásica (PPC)', 'Dosis', 100.00, 25.00, 15000.00, 'Nevera 1', 'synced')
ON CONFLICT (id) DO NOTHING;

-- 7.5 Protocolos de Bioseguridad
INSERT INTO protocolos_bioseguridad (id, codigo, tipo_protocolo, tarea, descripcion, icono, frecuencia, activo, sync_status)
VALUES
    ('71eebc99-9c0b-4ef8-bb6d-6bb9bd380a81', 'S1', 'estructural', 'Cerco Perimetral', 'Malla o barda a 50m de galpones.', 'ShieldCheck', 'semanal', true, 'synced'),
    ('72eebc99-9c0b-4ef8-bb6d-6bb9bd380a82', 'S2', 'estructural', 'Filtro Sanitario', 'Ducha obligatoria y cambio de ropa.', 'UserCheck', 'diaria', true, 'synced'),
    ('73eebc99-9c0b-4ef8-bb6d-6bb9bd380a83', 'S3', 'estructural', 'Arco de Desinfección', 'Desinfección total de chasis y ruedas.', 'Truck', 'diaria', true, 'synced'),
    ('74eebc99-9c0b-4ef8-bb6d-6bb9bd380a84', 'O1', 'operacional', 'Control de Plagas', 'Plan documentado para roedores e insectos.', 'Bug', 'quincenal', true, 'synced')
ON CONFLICT (id) DO NOTHING;

-- 7.6 Animales Iniciales
INSERT INTO animales (id, codigo_arete, codigo_qr, nombre_alias, sexo, raza, fecha_nacimiento, estado, corral_id, peso_actual_kg, sync_status)
VALUES
    ('01eebc99-9c0b-4ef8-bb6d-6bb9bd380a71', 'PT-2026-001', 'QR-PT-2026-001', 'Titan', 'macho', 'Pietrain', '2025-08-10', 'engorde', 'c3eebc99-9c0b-4ef8-bb6d-6bb9bd380a33', 118.00, 'synced'),
    ('02eebc99-9c0b-4ef8-bb6d-6bb9bd380a72', '2024-001', 'QR-2024-001', 'Apolo', 'macho', 'Duroc', '2025-11-20', 'precebo', 'c2eebc99-9c0b-4ef8-bb6d-6bb9bd380a32', 29.50, 'synced'),
    ('03eebc99-9c0b-4ef8-bb6d-6bb9bd380a73', '2024-042', 'QR-2024-042', 'Atenea', 'hembra', 'Landrace', '2025-10-15', 'levante', 'c4eebc99-9c0b-4ef8-bb6d-6bb9bd380a34', 56.50, 'synced'),
    ('04eebc99-9c0b-4ef8-bb6d-6bb9bd380a74', 'H-001', 'QR-H-001', 'Matrona 1', 'hembra', 'Landrace x Pietrain', '2024-05-15', 'gestacion', 'c5eebc99-9c0b-4ef8-bb6d-6bb9bd380a35', 185.50, 'synced'),
    ('05eebc99-9c0b-4ef8-bb6d-6bb9bd380a75', 'C-089', 'QR-C-089', 'Junior', 'macho', 'Hampshire', '2025-10-25', 'levante', 'c4eebc99-9c0b-4ef8-bb6d-6bb9bd380a34', 44.00, 'synced')
ON CONFLICT (id) DO NOTHING;

-- 7.7 Pesajes Iniciales (Registro en Hipertabla)
INSERT INTO registro_pesos (tiempo, id_registro, id_cerdo, corral_id, peso_kg, metodo, confianza_ia, sync_status)
VALUES
    ('2026-07-15 04:00:00-05', '9a23a9ca-5b15-4cf6-ac1d-c42dfc90f574', '01eebc99-9c0b-4ef8-bb6d-6bb9bd380a71', 'c3eebc99-9c0b-4ef8-bb6d-6bb9bd380a33', 85.00, 'manual', NULL, 'synced'),
    ('2026-08-01 03:00:00-05', '8c82c9ef-4ca1-4b05-84d5-cee171534a57', '02eebc99-9c0b-4ef8-bb6d-6bb9bd380a72', 'c2eebc99-9c0b-4ef8-bb6d-6bb9bd380a32', 22.00, 'manual', NULL, 'synced'),
    ('2026-08-05 04:15:00-05', '373fabd3-501a-42f2-a292-3fee6c770150', '01eebc99-9c0b-4ef8-bb6d-6bb9bd380a71', 'c3eebc99-9c0b-4ef8-bb6d-6bb9bd380a33', 102.00, 'vision_ai', 0.962, 'synced'),
    ('2026-08-20 03:30:00-05', 'c0dcd86f-c88e-44d8-a600-2ef0ede72354', '02eebc99-9c0b-4ef8-bb6d-6bb9bd380a72', 'c2eebc99-9c0b-4ef8-bb6d-6bb9bd380a32', 29.50, 'vision_ai', 0.975, 'synced'),
    ('2026-08-25 04:45:00-05', '5a970cb6-24ab-4f84-b359-6f466ab4d282', '01eebc99-9c0b-4ef8-bb6d-6bb9bd380a71', 'c3eebc99-9c0b-4ef8-bb6d-6bb9bd380a33', 118.00, 'vision_ai', 0.981, 'synced')
ON CONFLICT (tiempo, id_registro, id_cerdo) DO NOTHING;

-- 8. REFRESCO DE VISTA CONTINUA Y PRIVILEGIOS

-- Privilegios de acceso universal para desarrollo local
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO PUBLIC;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO PUBLIC;
GRANT ALL PRIVILEGES ON ALL ROUTINES IN SCHEMA public TO PUBLIC;
