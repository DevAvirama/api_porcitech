#!/usr/bin/env bash
set -e

# Evitar conversión de rutas en Git Bash / MSYS2 sobre Windows
export MSYS_NO_PATHCONV=1

# Ubicarse siempre en el directorio raíz donde está este script
cd "$(dirname "$0")"

echo "=================================================================="
echo "    Iniciando Despliegue Multiplataforma de PorciTech"
echo "=================================================================="

# 0. Normalizar nombres de carpetas o clonar si faltan
if [ ! -d "porcitech-backend" ] && [ -d "api_porcitech" ]; then
    echo "[*] Normalizando carpeta 'api_porcitech' -> 'porcitech-backend'..."
    mv "api_porcitech" "porcitech-backend"
elif [ ! -d "porcitech-backend" ]; then
    echo "[*] Clonando repositorio porcitech-backend..."
    git clone https://github.com/DevAvirama/api_porcitech.git porcitech-backend
fi

if [ ! -d "porcitech-nextjs" ]; then
    echo "[*] Clonando repositorio porcitech-nextjs..."
    git clone https://github.com/DevAvirama/porcitech-nextjs.git porcitech-nextjs
fi

if [ ! -d "vision-artificial" ] && [ -d "Vision_Artifical" ]; then
    echo "[*] Normalizando carpeta 'Vision_Artifical' -> 'vision-artificial'..."
    mv "Vision_Artifical" "vision-artificial"
elif [ ! -d "vision-artificial" ]; then
    echo "[*] Clonando repositorio vision-artificial..."
    git clone https://github.com/DevAvirama/Vision_Artifical.git vision-artificial
fi

# 1. Detectar IP local de forma compatible con Linux, macOS y Windows (Git Bash / WSL)
detect_ip() {
    local ip=""
    if command -v ip >/dev/null 2>&1; then
        ip=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1); exit}')
    fi
    if [ -z "$ip" ] && command -v hostname >/dev/null 2>&1; then
        ip=$(hostname -I 2>/dev/null | awk '{print $1}')
    fi
    if [ -z "$ip" ] && command -v ipconfig >/dev/null 2>&1; then
        # Windows (Git Bash) o macOS
        ip=$(ipconfig 2>/dev/null | grep -E "IPv4|Dirección IPv4" | head -n 1 | awk -F: '{print $2}' | tr -d ' \r')
    fi
    echo "${ip:-localhost}"
}

IP_ACTUAL="${1:-$(detect_ip)}"
echo "[*] Host/IP detectada en este equipo: ${IP_ACTUAL}"

# 2. Asegurar que la red Docker compartida exista
if ! docker network ls --format '{{.Name}}' | grep -q "^porcitech-net$"; then
    echo "[*] Creando red Docker 'porcitech-net'..."
    docker network create porcitech-net
fi

# 3. Desplegar Base de Datos, API Backend y Frontend con Docker Compose
echo "[*] Levantando Base de Datos (TimescaleDB), Backend API (FastAPI) y Frontend (Next.js)..."
CORS_ORIGINS="[\"http://localhost:3000\", \"http://127.0.0.1:3000\", \"http://${IP_ACTUAL}:3000\"]" \
NEXT_PUBLIC_API_URL="" \
docker compose up -d --build db api frontend

echo "[*] Esperando a que la base de datos esté lista..."
until docker exec porcitech-db pg_isready -h 127.0.0.1 -U postgres -d porcitech_db > /dev/null 2>&1; do
    sleep 2
done

echo "[✓] Base de datos en línea y saludable."

# 4. Verificar tablas creadas en la base de datos
echo "[*] Verificando tablas aprovisionadas en PostgreSQL / TimescaleDB:"
docker exec -i porcitech-db psql -U postgres -d porcitech_db -c "\dt" || true

echo "=================================================================="
echo " [✓] ¡DESPLIEGUE COMPLETADO CON ÉXITO!"
echo "=================================================================="
echo " • Acceso Local (este equipo):"
echo "   - Frontend Web:   http://localhost:3000"
echo "   - API Docs:       http://localhost:8000/api/v1/docs"
echo "   - Base de datos:  localhost:${DB_PORT:-5433} (usuario: postgres / bd: porcitech_db)"
if [ "$IP_ACTUAL" != "localhost" ] && [ "$IP_ACTUAL" != "127.0.0.1" ]; then
    echo " • Acceso en Red Local / VM / App Móvil:"
    echo "   - Frontend Web:   http://${IP_ACTUAL}:3000"
    echo "   - API Backend:    http://${IP_ACTUAL}:8000/api/v1"
    echo "   - API Docs:       http://${IP_ACTUAL}:8000/api/v1/docs"
fi
echo "=================================================================="
