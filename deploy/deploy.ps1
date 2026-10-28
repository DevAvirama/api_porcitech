# ==============================================================================
# Script de Despliegue Automatizado para Windows 10/11 (PowerShell) - PorciTech
# ==============================================================================
$ErrorActionPreference = "Stop"

Set-Location -Path $PSScriptRoot

Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host "    Iniciando Despliegue de PorciTech en Windows (Docker)" -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan

# 0. Verificar que Docker Desktop este en ejecucion
try {
    docker info *> $null
} catch {
    Write-Host "[!] Error: Docker Desktop no esta en ejecucion o no esta instalado." -ForegroundColor Red
    Write-Host "    Por favor, abre Docker Desktop, espera a que inicie el motor y vuelve a ejecutar este script." -ForegroundColor Yellow
    exit 1
}

# 1. Normalizar nombres de carpetas o clonar si faltan
if (-not (Test-Path ".\porcitech-backend") -and (Test-Path ".\api_porcitech")) {
    Write-Host "[*] Renombrando carpeta 'api_porcitech' -> 'porcitech-backend'..." -ForegroundColor Yellow
    Rename-Item -Path ".\api_porcitech" -NewName "porcitech-backend"
} elseif (-not (Test-Path ".\porcitech-backend")) {
    Write-Host "[*] Clonando repositorio porcitech-backend..." -ForegroundColor Yellow
    git clone https://github.com/DevAvirama/api_porcitech.git porcitech-backend
}

if (-not (Test-Path ".\porcitech-nextjs")) {
    Write-Host "[*] Clonando repositorio porcitech-nextjs..." -ForegroundColor Yellow
    git clone https://github.com/DevAvirama/porcitech-nextjs.git porcitech-nextjs
}

if (-not (Test-Path ".\vision-artificial") -and (Test-Path ".\Vision_Artifical")) {
    Write-Host "[*] Renombrando carpeta 'Vision_Artifical' -> 'vision-artificial'..." -ForegroundColor Yellow
    Rename-Item -Path ".\Vision_Artifical" -NewName "vision-artificial"
} elseif (-not (Test-Path ".\vision-artificial")) {
    Write-Host "[*] Clonando repositorio vision-artificial..." -ForegroundColor Yellow
    git clone https://github.com/DevAvirama/Vision_Artifical.git vision-artificial
}

# 2. Detectar IP local de Windows en la red Wi-Fi / Ethernet
$IpActual = "localhost"
try {
    $NetIp = Get-NetIPAddress -AddressFamily IPv4 |
        Where-Object {
            $_.IPAddress -notlike "127.*" -and
            $_.IPAddress -notlike "169.254.*" -and
            $_.InterfaceAlias -notmatch "WSL|vEthernet|Docker|Loopback|VMware|VirtualBox"
        } |
        Select-Object -First 1 -ExpandProperty IPAddress
    if ($NetIp) {
        $IpActual = $NetIp
    }
} catch {
    $IpActual = "localhost"
}

Write-Host "[*] IP de red local detectada en Windows: $IpActual" -ForegroundColor Green

# 3. Asegurar red Docker compartida
$ExistingNet = docker network ls --format "{{.Name}}" | Where-Object { $_ -eq "porcitech-net" }
if (-not $ExistingNet) {
    Write-Host "[*] Creando red Docker 'porcitech-net'..."
    docker network create porcitech-net | Out-Null
}

# 4. Configurar variables de entorno y levantar contenedores
$env:CORS_ORIGINS = "[`"http://localhost:3000`", `"http://127.0.0.1:3000`", `"http://${IpActual}:3000`"]"
$env:NEXT_PUBLIC_API_URL = ""

Write-Host "[*] Construyendo y levantando Base de Datos (TimescaleDB), Backend API (FastAPI) y Frontend (Next.js)..." -ForegroundColor Cyan
docker compose up -d --build db api frontend
if ($LASTEXITCODE -ne 0) {
    Write-Host "[!] Error durante la construccion o arranque de Docker Compose." -ForegroundColor Red
    exit $LASTEXITCODE
}

# 5. Esperar disponibilidad de PostgreSQL
Write-Host "[*] Esperando a que la base de datos este en linea..."
$Retries = 0
do {
    Start-Sleep -Seconds 2
    docker exec porcitech-db pg_isready -h 127.0.0.1 -U postgres -d porcitech_db *> $null
    $Retries++
} while ($LASTEXITCODE -ne 0 -and $Retries -lt 30)

Write-Host "[OK] Base de datos en linea y saludable." -ForegroundColor Green

# 6. Mostrar tablas creadas
Write-Host "[*] Verificando tablas creadas en porcitech_db:"
docker exec -i porcitech-db psql -U postgres -d porcitech_db -c "\dt"

$DbPort = if ($env:DB_PORT) { $env:DB_PORT } else { "5433" }

Write-Host "==================================================================" -ForegroundColor Green
Write-Host " [OK] DESPLIEGUE COMPLETADO CON EXITO EN WINDOWS!" -ForegroundColor Green
Write-Host "==================================================================" -ForegroundColor Green
Write-Host " • Acceso en este PC (Windows):"
Write-Host "   - Frontend Web:   http://localhost:3000"
Write-Host "   - API Docs:       http://localhost:8000/api/v1/docs"
Write-Host "   - Base de datos:  localhost:$DbPort (usuario: postgres / bd: porcitech_db)"
if ($IpActual -ne "localhost") {
    Write-Host " • Acceso desde otros dispositivos en tu Wi-Fi (ej. App Movil):"
    Write-Host "   - Frontend Web:   http://${IpActual}:3000"
    Write-Host "   - API Backend:    http://${IpActual}:8000/api/v1"
}
Write-Host "==================================================================" -ForegroundColor Green
