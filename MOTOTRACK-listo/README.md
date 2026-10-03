# MotoTrak

Pronosticador Streamlit que lee las hojas `Demand` y `Summary` de las exportaciones
del juego. Exige al menos 208 periodos de cada una de las ocho series reales: Moto, Cuatrimoto
y Tractor en Norte y Centro; Moto y Cuatrimoto en Sur. No imputa datos ni crea
Tractor en Sur. Norte, Centro y Sur deben tener los mismos periodos consecutivos,
desde 1 hasta el último real. Los archivos desalineados se rechazan con el detalle
de periodos por regional; no se eliminan ni imputan observaciones.

`moto-track.xlsx` conserva el histórico base de 208 periodos y
`moto-track-209.xlsx` contiene la exportación real con 209. Por defecto se abre
la exportación local `moto-track-N.xlsx` con el mayor sufijo N; si no existe,
se abre la base si está disponible. **Estos Excel locales son opcionales para
iniciar la aplicación**: cuando no hay ninguno, se muestran los controles de carga
y se espera la demanda del usuario. El número real de periodos siempre se valida leyendo la demanda,
independientemente del nombre del archivo. También puede cargar nuevas exportaciones
desde la barra lateral sin renombrarlas ni reiniciar la aplicación.

## Ejecutar

El proyecto usa Python 3.11 independiente en `.uv-python` y el entorno `.venv`.
No requiere Python del sistema ni las Command Line Tools de Xcode.

Para preparar una clonación nueva, con `uv` disponible:

```sh
UV_PYTHON_INSTALL_DIR="$PWD/.uv-python" UV_CACHE_DIR="$PWD/.uv-cache" uv python install 3.11
UV_PYTHON_INSTALL_DIR="$PWD/.uv-python" UV_CACHE_DIR="$PWD/.uv-cache" uv venv --python 3.11 --managed-python --seed .venv
```

Si `.venv` ya existe, reutilizarlo. Instalar las dependencias y arrancar desde la
raíz del proyecto:

```sh
.venv/bin/python -m pip install --only-binary=:all: -r requirements.txt
.venv/bin/python -m streamlit run app.py
```

En Windows, descomprimir el proyecto en una carpeta nueva y abrir PowerShell en
esa carpeta. Con `uv` disponible, preparar Python y el entorno locales:

```powershell
$env:UV_PYTHON_INSTALL_DIR = "$PWD\.uv-python"
$env:UV_CACHE_DIR = "$PWD\.uv-cache"
uv python install 3.11
uv venv --python 3.11 --managed-python --seed .venv
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

En ambos sistemas, ejecutar el `app.py` de la carpeta que contiene
`forecasting.py`. Esta versión importa `evaluate` y `forecast` desde ese módulo;
no utiliza `src.forecasting`, `rolling_forecast` ni `get_training_data`.
Al trasladarla, copiar todos los módulos juntos y reiniciar Streamlit.

Abrir http://localhost:8501. Para comprobar los datos, los modelos, la exportación
y los controles de Streamlit:

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Las pruebas incluyen la compilación e importación de todos los módulos y de
`app.py` en un proceso Python nuevo. Con Streamlit iniciado, comprobar también
el servidor y la ejecución real de la interfaz:

```sh
.venv/bin/python scripts/check_streamlit.py
```

La comprobación HTTP de salud por sí sola no detecta errores del script.
Este comando abre una sesión Streamlit y falla si la aplicación muestra una
excepción. Si se actualizan funciones de los módulos con el servidor activo,
la configuración local utiliza vigilancia por sondeo para recargarlos sin
Watchdog ni Xcode. Si un servidor antiguo conserva módulos en memoria,
detenerlo con Ctrl+C y reiniciarlo con el comando de ejecución anterior.

## Método

Se comparan diez modelos: naive, naive estacional 52, promedios móviles 4/8/13,
suavización exponencial simple, Holt, Holt amortiguado y Holt-Winters aditivo
con y sin tendencia amortiguada, ambos con estacionalidad 52.

Cada carga nueva actualiza la validación temporal: los últimos 52 periodos reales
se reservan para evaluación y todos los anteriores para entrenamiento, sin usar
los reales de validación durante el ajuste. Con 208 observaciones se entrena
1–156 y se valida 157–208; con 209 se entrena 1–157 y se valida 158–209;
con 210 se entrena 1–158 y se valida 159–210.
Se reportan MAE, RMSE, WMAPE, sesgo y MAPE (solo cuando todos los reales son
positivos). Se selecciona el menor WMAPE; una tolerancia de 0,5 puntos
porcentuales permite desempatar por RMSE, sesgo absoluto y nombre. Si todos
los reales son cero se selecciona por RMSE. Los ajustes que fallan o no
convergen se muestran y excluyen.

El modelo elegido por serie se reentrena con **todo el histórico disponible**,
incluidas las nuevas demandas observadas. Se pronostica desde el último real + 1
hasta, como máximo, el periodo 226. El control de horizonte ofrece únicamente
los periodos restantes: 18 con último real 208, 17 con 209, 16 con 210 y 1 con 225.
En el 226 se muestra que la simulación finalizó, sin generar futuros.
El cache se identifica por el contenido del Excel; una exportación nueva vuelve
a validar, seleccionar y entrenar los modelos. Se recortan los valores negativos a cero y
se redondea a unidades enteras antes de consolidar por producto.
La validación usa predicciones sin redondear, recortadas a cero.

La pestaña **Pronóstico** muestra KPI y el «Resumen del horizonte de pronóstico»:
totales y promedios por periodo de cada producto y del total general para el
horizonte seleccionado. También incluye acumulados por Regional y Producto.
Estos resúmenes incluyen todas las series; el detalle inferior respeta los
filtros laterales. El detalle completo está en «Pronóstico detallado de todas
las series». En **Validación de modelos**, WMAPE y MAPE se presentan como
porcentajes con dos decimales (por ejemplo, 17,19 %), conservando sus valores
numéricos en los cálculos y en el Excel.

El Excel descargable incluye detalle Regional–Producto, consolidado, modelos
seleccionados y comparación completa. Los pronósticos son estimaciones
puntuales; no incluyen intervalos de incertidumbre. Un único bloque de
validación no garantiza el desempeño futuro.

Las pruebas rolling utilizan los archivos reales de 208 y 209 periodos. Las
extensiones a 210, 225 y 226 son copias sintéticas exclusivamente de prueba y no
se escriben en los archivos fuente. Cubren los cortes temporales, reentrenamiento,
límite 226, alineación regional y recarga de archivos dentro de la misma sesión.

## Clasificación ABC-XYZ

Las nuevas pestañas **ABC-XYZ**, **Consolidado** y **Conclusiones** utilizan
exclusivamente las últimas 52 semanas reales de los ocho SKU. Con último
periodo 214 se usa 163–214; con 215, 164–215. El histórico anterior se usa
para entrenar el motor de backtesting, manteniendo el último año sin datos futuros.

ABC ordena por demanda de esas 52 semanas × utilidad unitaria. Utiliza la utilidad
del archivo de costos; solo si falta esa columna calcula precio menos costo.
Los límites iniciales A/B son 80/95 %, modificables en la barra lateral.
El SKU que cruza cada umbral se mantiene en la clase que lo cruza; el siguiente
inicia la nueva. Por ello una categoría puede quedar vacía con solo ocho SKU.

XYZ reutiliza el modelo seleccionado y las métricas del motor existente:
**Score% = WMAPE × 100**, donde WMAPE = suma de errores absolutos / suma de
demanda real de las últimas 52 semanas. No usa CV ni CV². X ≤15 %, Y >15 y
≤30 %, Z >30 %, con límites editables. RMSE permanece en unidades de demanda.
Si la demanda de validación de un SKU es cero, su WMAPE es indefinido y se
indica «Sin evaluación», sin asignarle una predictibilidad inventada.

Se lee la hoja `Costos` de `Costos_MotoTrak_Actividad.xlsx`, reconociendo sus
títulos previos y encabezados como «Precio de venta», «MD por unidad» y
«MOD por unidad». También se puede cargar el archivo de costos desde la barra
lateral, incluso antes de cargar la demanda. Sin costos disponibles, el
pronosticador funciona y las pestañas ABC-XYZ muestran una indicación para cargarlos.

Los costos válidos cargados se conservan en la sesión del usuario. El control
**Recordar costos entre sesiones locales** permite guardar, sin cambiar sus bytes,
una copia en `data/costos_actuales.xlsx` mediante reemplazo atómico. El control
comienza desmarcado; se activa explícitamente para recordar costos en el equipo
local. Una carga inválida no reemplaza los últimos costos válidos.
Si no se puede escribir en disco, la clasificación y las descargas siguen
funcionando con los costos de la sesión. Para un despliegue compartido, dejar
desmarcada esta opción y cargar los costos en cada sesión nueva.
No se recalculan MD, MOD ni costo de fabricación.

El consolidado suma las tres regionales para Moto/Cuatrimoto y solo Norte/Centro
para Tractor. El error consolidado se obtiene comparando la suma de las
predicciones elegidas con la suma de los reales, reutilizando `forecasting.metrics`;
no se promedia RMSE. También se muestra el rango de Score% de los SKU.

El botón **Descargar reporte ABC-XYZ en Excel** produce las hojas `Costos`,
`Clasif ABC_XYZ`, `Conclusiones`, `Consolidado`, `Pronosticos` y `Parametros`.
Incluye la fórmula de Score%, ventana y parámetros utilizados, con títulos,
filtros, encabezados congelados y formatos monetarios. Score% y participaciones
se almacenan como puntos porcentuales (17,19 = 17,19 %), sin multiplicarlos otra
vez al formatear. El Excel no contiene gráficos ni imágenes; Pareto, matriz y
barras comparativas aparecen únicamente en Streamlit.

Las conclusiones se redactan sobre los resultados actuales e identifican las
categorías realmente presentes. RMSE es un insumo para estudiar stock de
seguridad; no se calculan cantidades sin plazos de entrega y nivel de servicio.
Las pruebas nuevas cubren lectura/persistencia de costos, utilidad, umbrales,
ventana dinámica 214/215, errores del motor, consolidado, Excel sin gráficos
y actualización de la interfaz conservando el pronosticador.

## Preparación para GitHub y Streamlit Community Cloud

El punto de entrada es **`app.py`**, situado en la raíz junto a
`requirements.txt`. Las rutas se construyen desde los módulos del proyecto y
las cargas de usuario; no hay rutas absolutas de un equipo personal.
`requirements.txt` fija las versiones de pandas, NumPy, statsmodels, Plotly,
Streamlit y openpyxl. Incluye Jinja2 para las tablas con formato y websockets
para la comprobación del servidor. No se necesitan paquetes del sistema en
`packages.txt`.

`.gitignore` excluye los entornos (incluido el respaldo `.venv-python312-backup`),
el Python descargado por `uv`, cachés, secretos, configuración personal,
archivos temporales de Office, logs, costos persistidos y reportes generados.
Los archivos de código, pruebas, `README.md`, `.python-version` y
`.streamlit/config.toml` deben formar parte del repositorio.
Los tres Excel fuente (`moto-track.xlsx`, `moto-track-209.xlsx` y
`Costos_MotoTrak_Actividad.xlsx`) se conservan para las pruebas de regresión
con datos reales. Son opcionales para ejecutar la app; no confundirlos con los
reportes descargados ni con la copia de costos de una sesión.

Cuando se decida publicar:

1. Subir el proyecto a un repositorio de GitHub respetando `.gitignore`.
2. En Streamlit Community Cloud, seleccionar el repositorio, la rama y
   **Main file path: `app.py`**.
3. En **Advanced settings**, seleccionar **Python 3.11**, la misma versión de
   las pruebas locales. `.python-version` documenta la versión para el entorno
   local; seleccionar también la versión explícitamente en Cloud.
4. Desplegar y cargar los archivos de demanda y costos desde la barra lateral.

Referencias oficiales: [desplegar una aplicación](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy)
y [dependencias de la aplicación](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies).
Esta preparación no ejecuta ningún push ni despliegue.

Las pruebas de despliegue arrancan una copia limpia sin ningún Excel, cargan
la demanda real de 209 periodos y los costos por la interfaz, comprueban la
separación de costos entre sesiones y simulan un disco sin permisos de escritura.
Se ejecutan junto con todas las pruebas de importación, pronóstico rolling,
ABC-XYZ y exportación usando el comando `unittest` anterior.
`scripts/check_streamlit.py` comprueba la aplicación local con los Excel fuente
disponibles; la prueba de arranque sin archivos está en `tests/test_deployment.py`.
