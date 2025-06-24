@echo off
setlocal enabledelayedexpansion

:: ====================================================================
:: PARTE 1: VERIFICACION DEL SISTEMA
:: ====================================================================

title Asistente de Instalacion - v10.0

:: Si el script se esta ejecutando por segunda vez, el titulo cambiara
if "%1"=="restarted" (
    title Ejecutando aplicacion...
) else (
    echo.
    echo ====================================================================
    echo    VERIFICANDO LAS CARACTERISTICAS DEL ORDENADOR (v10.0)
    echo ====================================================================
    echo.

    :: (La verificacion del sistema no cambia)
    echo [PROCESADOR]
    for /f "usebackq tokens=*" %%a in (`powershell -NoProfile -Command "Get-CimInstance -ClassName Win32_Processor | Select-Object -ExpandProperty Name"`) do (echo %%a)
    echo.
    echo [MEMORIA RAM]
    for /f "usebackq" %%a in (`powershell -NoProfile -Command "$mem = Get-CimInstance -ClassName Win32_ComputerSystem; [math]::Round($mem.TotalPhysicalMemory / 1GB, 0)"`) do (echo Capacidad Total: %%a GB)
    echo.
    echo [TARJETA GRAFICA (GPU)]
    for /f "usebackq tokens=*" %%a in (`powershell -NoProfile -Command "Get-CimInstance -ClassName Win32_VideoController | Where-Object { $_.Name -like '*NVIDIA*' -or $_.Name -like '*AMD*' -or $_.Name -like '*INTEL*' } | Select-Object -ExpandProperty Name"`) do (echo %%a)
    echo.
    set "hasRTX=0"
    set "hasCUDA=0"
    for /f "usebackq" %%i in (`powershell -NoProfile -Command "if (Get-CimInstance -ClassName Win32_VideoController | Where-Object { $_.Name -like '*RTX*' }) { Write-Output 'RTX_FOUND' }"`) do (if "%%i" == "RTX_FOUND" set "hasRTX=1")
    echo [VERSION DE CUDA]
    where nvidia-smi >nul 2>nul
    if %errorlevel% == 0 (set "hasCUDA=1" & echo Se detecto una GPU NVIDIA... & for /f "tokens=9" %%v in ('nvidia-smi ^| findstr /i "CUDA Version"') do (echo Version de CUDA instalada: %%v)) else (echo CUDA no esta disponible.)
    echo.
    echo ==============================================================
    echo    Verificacion completada.
    echo ==============================================================
    echo.
    echo.
)


:: ====================================================================
:: PARTE 2: LOGICA DE INSTALACION O EJECUCION
:: ====================================================================

:: Si el entorno ya existe, salta directamente a la seccion de ejecucion.
if exist "venv\Scripts\activate.bat" (
    goto :run_application
)

:: --- BLOQUE DE INSTALACION (Solo se ejecuta si el venv no existe) ---

title Asistente de Instalacion - Instalando dependencias...
echo ====================================================================
echo    ENTORNO VIRTUAL NO ENCONTRADO. INICIANDO INSTALACION...
echo ====================================================================
echo.

:: Verificamos si Python esta instalado
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR FATAL] Python no se encuentra. Por favor, instalalo y marca "Add to PATH".
    pause
    exit /b
)
    
echo [PASO 1/4] Creando entorno virtual...
python -m venv venv
if %errorlevel% neq 0 (
    echo [ERROR] Hubo un problema al crear el entorno virtual.
    pause
    exit /b
)
    
echo.
echo [PASO 2/4] Activando entorno...
call venv\Scripts\activate.bat

echo.
echo [PASO 3/4] Actualizando pip e instalando dependencias de requirements.txt...
python.exe -m pip install --upgrade pip >nul
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [ERROR] Hubo un problema al instalar las librerias.
    pause
    exit /b
)

:: Verificamos si se debe instalar la version de PyTorch para CUDA
if "!hasRTX!" == "1" if "!hasCUDA!" == "1" (
    echo.
    echo [PASO 4/4] Instalando PyTorch para GPU (puede tardar)...
    pip uninstall torch torchvision torchaudio -y >nul
    pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
    if %errorlevel% neq 0 (
        echo [ERROR] Hubo un problema al instalar PyTorch para CUDA.
        pause
        exit /b
    )
)
echo.
echo [INFO] La instalacion inicial ha finalizado con exito.
echo El asistente se reiniciara ahora para lanzar la aplicacion...
timeout /t 3 >nul

:: Reinicia el script y cierra la ventana actual, pasando un parametro para saber que fue reiniciado
start "" "%~f0" restarted
exit


:: ====================================================================
:: PARTE 3: EJECUCION DE LA APLICACION
:: ====================================================================

:run_application
title Ejecutando aplicacion...

:: Siempre activamos el entorno virtual antes de ejecutar
call venv\Scripts\activate.bat

:: Ejecutamos el script de Python
python main.py

endlocal
