# Vortex — Cálculo y modelado visual de estanterías industriales de acero

Software de escritorio en Python para **modelar, analizar y verificar
estanterías industriales de acero (racks porta-estibas)** conforme a la
norma **NTC 5689:2009** ("Especificación para el diseño, ensayo y
utilización de estanterías industriales de acero", adopción modificada de
ANSI/RMI MH16.1), con las fórmulas de diseño de elementos tomadas
literalmente de **NSR-10, Título F, Capítulo F.4** ("Estructuras de acero
con perfiles de lámina formada en frío", la adopción colombiana de AISI
S100 a la que remite la propia NTC 5689 numeral 1.4).

La interfaz combina un modelador paramétrico 3D (estilo Autodesk Inventor)
con un motor de análisis matricial de pórtico espacial y verificación de
elementos (estilo SAP2000), y genera una memoria de cálculo en `.docx` con
el mismo formato que usan los calculistas de estanterías en Colombia.

## Alcance y motor de cálculo

- **Geometría**: modelador paramétrico de estanterías selectivas
  (parales, vigas, diagonales de arriostramiento, placas base), en 3D.
  Catálogo con secciones reales de fabricación (viga y riostra tomadas de
  un plano de Autodesk Inventor) además de secciones de referencia
  idealizadas. El arriostramiento del marco es configurable por ángulo
  objetivo (30°–75°, incluido 70°) o por cantidad de diagonales — el
  software deriva cuántos niveles abarca cada panel y reporta el ángulo
  real logrado (que puede diferir del objetivo si la geometría no lo
  permite sin subdividir el paral).
- **Cargas**: muerta (peso propio), viva, de producto (PL), impacto,
  sísmica (NTC 5689 numeral 2.7 — con tablas Ca/Cv, Aa/Av por ciudad
  (NSR-10), R=4/6 según dirección), viento.
- **Combinaciones de carga**: ASD y LRFD completas, numerales 2.1 y 2.2.
- **Análisis**: elementos finitos de pórtico espacial 3D (6 GDL/nudo,
  método de la rigidez directa), con conexiones viga-paral semirrígidas
  (condensación estática) y diagonales articuladas.
- **Verificación**: parales (compresión + flexión biaxial + cortante,
  con ancho efectivo real por elemento — NSR-10 F.4.2.2/F.4.3.4, no una
  aproximación plana — y chequeo por componente P/M2/M3/V2/V3 además del
  ratio de interacción combinado), vigas (flexión, cortante — NSR-10
  F.4.3.3-44 —, deflexión de servicio), placas base y anclajes (demanda),
  diagonales.
- **Reporte**: memoria de cálculo `.docx` con portada, evaluación de
  cargas, combinaciones, sistema estructural, datos de entrada,
  verificación de elementos, y las tablas "RESISTENCIA \<sección\> MODELO
  CFS" / "CHEQUEO" con el mismo nombre y columnas (H, P, Mx, Vy, My, Vx)
  que usa el calculista de referencia, para que el formato sea
  reconocible frente a memorias anteriores del mismo proyecto.
- **Visualización**: el visor 3D colorea cada elemento por relación
  demanda/capacidad (verde/amarillo/rojo) o por concentración de
  esfuerzos (fuerza axial en parales, momento en vigas, normalizada por
  tipo de elemento), con una leyenda de escala de colores junto al
  visor. También dibuja **líneas de fuerzas** (diagramas de P, M2, M3,
  V2 o V3, estilo SAP2000, offset perpendicular al eje de cada
  elemento) para cualquiera de los patrones de carga resueltos (DL, PL,
  sismo en X, sismo en Y), con control de escala.

El motor de sismo fue **validado numéricamente contra una hoja de cálculo
real de un proyecto de estantería** (ver `tests/test_seismic.py`), el
motor de análisis matricial fue validado contra soluciones clásicas de
resistencia de materiales (viga en voladizo, viga simplemente apoyada —
ver `tests/test_solve.py`), y el modelo 3D completo fue **contrastado
contra las fuerzas reales reportadas por SAP2000 en la memoria de cálculo
de un proyecto real** (`examples/run_example.py`): usando la misma
geometría, secciones y combinación de carga (`1.4DL+1.2PL`), el paral
interior de la base da **86.3 kN** en Vortex contra un rango real
reportado de **73.2–84.3 kN** en la memoria — una coincidencia razonable
que valida el reparto de cargas y el análisis matricial del modelo
completo.

**Limitación conocida, expuesta deliberadamente en vez de ocultada:** la
sección de viga de catálogo (`VIGA CAJA 160x60x1.5mm`) es una caja
rectangular simple idealizada a partir de las cotas exteriores de un
plano de fabricación; el perfil real probablemente incluye un pliegue o
refuerzo interior (visible como un escalón en el plano) que le da un
módulo de sección mayor al calculado aquí. Por eso el ejemplo reporta
ratios de flexión de hasta **~1.44** en las vigas bajo carga real (antes
de una corrección de catálogo aplicada en 2026-09, este ratio llegaba a
~2.9 porque `rectangular_tube_section` tenía los ejes fuerte/débil
intercambiados — `Iy`/`Sy`, usados para el chequeo de flexión por
gravedad, recibían por error el módulo del eje débil de 60mm en vez del
eje fuerte de 160mm; ya corregido y cubierto por `tests/test_design.py`).
El ~1.44 restante es una limitación de la sección de catálogo, no del
motor de cargas ni del análisis (la carga distribuida calculada, ~4.8
kN/m, coincide con el valor real de SAP2000 de la memoria, ~4.92 kN/m).
Para un diseño definitivo, reemplazar `Section.Sy` por el valor
certificado del fabricante.

**Por qué los valores de Vortex se acercan pero no coinciden exactamente
con una memoria de SAP2000 existente:** al examinar la tabla "Frame
Section Assignments" completa de la memoria de referencia se observa que
el modelo SAP2000 original está compuesto por bloques repetidos de 6
elementos PARAL + 5 elementos VIGA cada uno (no un pórtico 3D continuo
de varias bahías conectadas entre sí) — es decir, el calculista analizó
cada "torre" como un subsistema 2D con su propio reparto de cargas,
mientras que Vortex arma y resuelve un **pórtico espacial 3D único y
continuo** con todas las bahías conectadas (método más riguroso, que no
requiere decidir a mano cuánta carga tributaria le corresponde a cada
columna). Esta diferencia de topología —no un error de fórmulas— es la
causa de que P coincida razonablemente bien (ambos métodos reparten la
carga vertical de forma similar) pero M y V no coincidan elemento a
elemento. Reproducir exactamente esa topología 2D específica requeriría
conocer las condiciones de apoyo y de conexión exactas del archivo
`.sdb` original, que no están recuperables desde las tablas de resultados
exportadas a Word.

## ⚠️ Advertencia — uso profesional

Este software es una **herramienta de apoyo al cálculo**, no un sustituto
del juicio profesional de un ingeniero. Antes de usar cualquier resultado
para fabricación o construcción, un ingeniero calculista con matrícula
profesional vigente debe revisar y complementar, como mínimo:

- Las propiedades certificadas de las secciones (incluida la constante de
  alabeo `Cw`, la distancia al centro de cortante `xo` y el radio de giro
  polar `ro`, necesarias para el pandeo flexo-torsional de perfiles
  formados en frío) — este software **no** las estima con fórmulas
  aproximadas; deben provenir de la ficha del fabricante o de ensayo
  (NTC 5689 numeral 9.3).
- La rigidez real de la conexión viga-paral (`km`), obtenida del ensayo
  tipo cantiléver (numeral 9.4.1) — el valor por defecto del software es
  sólo de referencia para un primer análisis.
- La capacidad de los anclajes al concreto (arrancamiento por cono,
  hendimiento, pryout — ACI 318 cap. 17) con la geometría real de
  espaciamiento y distancia a borde del proyecto.

## Instalación

```bash
pip install -r requirements.txt
```

## Uso

**Interfaz gráfica:**

```bash
python3 main.py
```

La interfaz usa un tema oscuro tipo CAD (similar a Autodesk Inventor), con
una barra de herramientas superior para el flujo principal y un panel de
propiedades a la izquierda organizado por categorías (Geometría, Secciones,
Arriostramiento, Cargas, Sismo):

1. Defina la geometría, secciones, cargas y parámetros sísmicos (ciudad
   → Aa/Av se autocompletan según NSR-10) en el panel izquierdo.
2. **Construir modelo** → genera y muestra la estantería en 3D.
3. **Analizar y verificar** → corre el análisis matricial completo y
   colorea cada elemento según su relación demanda/capacidad (verde =
   holgado, amarillo = ajustado ≥0.9, rojo = no cumple >1.0).
   **Analizar reutiliza el último modelo construido** — si cambia una
   sección, la geometría, el arriostramiento, etc. sin volver a
   presionar "Construir modelo", el resultado NO reflejará ese cambio.
   Para evitar ese olvido, use en cambio:
   - **🔄 Actualizar** → reconstruye el modelo con los valores actuales
     del formulario y vuelve a analizar, en un solo clic — el atajo
     recomendado siempre que se cambie cualquier dato.
   - **🗑 Borrar** → limpia el modelo, los resultados, el visor 3D y las
     recomendaciones de IA para empezar de nuevo; los valores ya
     diligenciados en el formulario NO se borran.
4. **Exportar memoria de cálculo** → guarda el reporte `.docx`.
5. **Cargas y sismo** (pestaña junto a "Resultados") → resumen calculado
   de la carga de producto (PL) y la carga viva (LL) — ambas por nivel y
   como gran total de todo el rack —, el peso propio real del modelo 3D
   (DL, por nivel y total), y los coeficientes sísmicos
   Ca/Cv/R/Ip/PLRF/Ws/Cs/V por dirección (transversal y longitudinal) más
   la distribución vertical de fuerzas Fx por nivel — el mismo tipo de
   resumen de las hojas "1.Datos_Entrada"/"2.Cargas_Sismo" de una memoria
   de cálculo en Excel, calculado por el motor ya validado de Vortex (sin
   una hoja de cálculo aparte). La ciudad (NSR-10 Tabla A.2.2.1) sigue
   autocompletando Aa/Av al seleccionarla, para las 32 ciudades de la
   tabla.

   **Carga viva (LL):** el campo "Carga viva (LL) kN/m²" existía en la
   GUI desde el principio pero no estaba conectado a ningún patrón de
   carga — cualquier valor distinto de cero se ignoraba silenciosamente.
   Ya corregido: se distribuye como carga de área (kN/m²) tributaria a
   las dos vigas de cada nivel (igual que PL), entra en Ws (peso sísmico
   efectivo, numeral 2.7.2 NTC 5689) y en las combinaciones de carga que
   ya la incluían (`combinations.py` ya traía el factor de LL, simplemente
   nunca recibía una fuerza distinta de cero) — sin cambiar ninguna
   fórmula normativa ya validada.

   **Cálculo automático de LL:** NTC 5689 define LL como "carga viva
   *distinta* a la de las estibas o productos almacenados... por ejemplo
   cargas de piso de las plataformas de trabajo" pero no fija su valor —
   ese valor viene del código de edificaciones aplicable (NSR-10 Título
   B, Tabla B.4.2.1-1). El desplegable **"Origen de la carga viva
   (LL)"** ofrece las ocupaciones más relevantes a una estantería
   industrial (bodega liviana/pesada, pasillos, oficinas, cubierta sin
   acceso) y autocompleta el campo — igual que Aa/Av se autocompletan al
   elegir la ciudad. Solo aplica si el proyecto realmente tiene una
   plataforma o entrepiso transitable (la mayoría de estanterías
   selectivas sin entrepiso NO la tienen: la opción por defecto es
   "Sin plataforma de trabajo", LL=0). Son valores de referencia — la
   opción **"Manual"** deja escribir un valor propio, y en cualquier
   caso deben verificarse contra la edición vigente de NSR-10 Título B
   antes de un diseño definitivo (`vortex/loads/dead_live.py`,
   `LIVE_LOAD_PRESETS_KN_M2`).
6. **Recomendaciones IA** (pestaña junto a "Resultados") → envía un
   resumen numérico del chequeo (elementos más críticos, parámetros
   sísmicos, conteo de fallas) a un modelo LLM a través de la API de
   [Groq](https://console.groq.com/keys) y muestra recomendaciones de
   ingeniería en lenguaje natural. Requiere una API key gratuita de Groq
   y conexión a internet; es una ayuda de lectura rápida de resultados,
   **no** un chequeo normativo ni un sustituto del criterio del calculista.

   **La API key nunca se pide ni se muestra en la interfaz — todo el
   código y la configuración de Groq viven en `vortex/gui/app.py`, a
   propósito, en un solo archivo.** Para configurarla, abra
   `vortex/gui/app.py` y edite la constante cerca del inicio del
   archivo:

   ```python
   GROQ_API_KEY = "gsk_TU_LLAVE_AQUI"
   ```

   ⚠️ Como esa constante queda en un archivo normal del proyecto, si
   sube o comparte este repositorio (por ejemplo a un repositorio Git
   propio) su key quedaría en el código — quítela antes de compartirlo,
   o use en su lugar la variable de entorno `GROQ_API_KEY` (tiene
   prioridad sobre la constante y no queda escrita en ningún archivo).

   **El modelo lo elige el sistema, no el usuario** — no hay selector
   ni lista de modelos en la interfaz. `CANDIDATE_MODELS` (también en
   `app.py`) trae varios modelos de chat de Groq conocidos, en orden de
   preferencia; `get_recommendations_auto()` los prueba uno por uno y
   usa el primero que responda. Si Groq retira o deshabilita un modelo
   para su cuenta, Vortex simplemente prueba el siguiente de la lista
   sin que el usuario vea ningún error intermedio — sólo se avisa si
   *ninguno* de los modelos de la lista responde.

**Ejemplo de línea de comandos** (reconstruye un caso de referencia y
genera su memoria de cálculo):

```bash
python3 examples/run_example.py
```

**Pruebas:**

```bash
pytest tests/ -q
```

## Estructura del proyecto

```
vortex/
  geometry/   modelo de datos y generador paramétrico de la estantería
  sections/   catálogo y cálculo de propiedades de secciones de pared delgada
  loads/      cargas (muerta/viva/producto/impacto), sismo NTC 5689 §2.7, combinaciones
  analysis/   elemento de pórtico 3D, condensación de conexiones, ensamblaje y solución
  design/     verificación de parales, vigas, placas base/anclajes, diagonales
  report/     generador de memoria de cálculo (.docx)
  gui/        aplicación de escritorio (PySide6 + visor 3D + tema visual +
              asesor de IA vía Groq, todo en gui/app.py)
tests/        pruebas unitarias y de validación
examples/     ejemplo de extremo a extremo
```
