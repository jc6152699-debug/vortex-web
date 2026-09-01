# Vortex — Cálculo y modelado visual de estanterías industriales de acero

Software de escritorio en Python para **modelar, analizar y verificar
estanterías industriales de acero (racks porta-estibas)** conforme a la
norma **NTC 5689:2009** ("Especificación para el diseño, ensayo y
utilización de estanterías industriales de acero", adopción modificada de
ANSI/RMI MH16.1), con apoyo de AISI (perfiles formados en frío) y AISC
(perfiles laminados en caliente).

La interfaz combina un modelador paramétrico 3D (estilo Autodesk Inventor)
con un motor de análisis matricial de pórtico espacial y verificación de
elementos (estilo SAP2000), y genera una memoria de cálculo en `.docx` con
el mismo formato que usan los calculistas de estanterías en Colombia.

## Alcance y motor de cálculo

- **Geometría**: modelador paramétrico de estanterías selectivas
  (parales, vigas, diagonales de arriostramiento, placas base), en 3D.
- **Cargas**: muerta (peso propio), viva, de producto (PL), impacto,
  sísmica (NTC 5689 numeral 2.7 — con tablas Ca/Cv, Aa/Av por ciudad
  (NSR-10), R=4/6 según dirección), viento.
- **Combinaciones de carga**: ASD y LRFD completas, numerales 2.1 y 2.2.
- **Análisis**: elementos finitos de pórtico espacial 3D (6 GDL/nudo,
  método de la rigidez directa), con conexiones viga-paral semirrígidas
  (condensación estática) y diagonales articuladas.
- **Verificación**: parales (compresión + flexión biaxial, AISI/AISC),
  vigas (flexión, cortante, deflexión de servicio), placas base y
  anclajes (demanda), diagonales.
- **Reporte**: memoria de cálculo `.docx` con portada, evaluación de
  cargas, combinaciones, sistema estructural, datos de entrada y
  verificación de elementos.

El motor de sismo fue **validado numéricamente contra una hoja de cálculo
real de un proyecto de estantería** (ver `tests/test_seismic.py`), y el
motor de análisis matricial fue validado contra soluciones clásicas de
resistencia de materiales (viga en voladizo, viga simplemente apoyada —
ver `tests/test_solve.py`).

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

1. Defina la geometría, secciones, cargas y parámetros sísmicos (ciudad
   → Aa/Av se autocompletan según NSR-10) en el panel izquierdo.
2. **Construir modelo** → genera y muestra la estantería en 3D.
3. **Analizar y verificar** → corre el análisis matricial completo y
   colorea cada elemento según su relación demanda/capacidad (verde =
   holgado, amarillo = ajustado ≥0.9, rojo = no cumple >1.0).
4. **Exportar memoria de cálculo** → guarda el reporte `.docx`.

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
  gui/        aplicación de escritorio (PySide6 + visor 3D)
tests/        pruebas unitarias y de validación
examples/     ejemplo de extremo a extremo
```
