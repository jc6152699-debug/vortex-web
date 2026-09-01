"""
Cargas sísmicas — NTC 5689:2009, numeral 2.7.

Fuente de las tablas y fórmulas: texto de la norma NTC 5689 (numerales
2.7.2 y 2.7.3, Tablas 1 y 2), y NSR-10 (Aa, Av por municipio, mapa de
amenaza sísmica) tal como se referencian en el numeral 2.7.3.1. Las
fórmulas y tablas de este módulo fueron verificadas numéricamente contra
una hoja de cálculo real de un proyecto de estantería (anexo
CARGAS_DE_SISMO.xlsx): el coeficiente Cv y la distribución vertical de
fuerzas Fx reproducen exactamente los valores de ese anexo (véanse los
tests en tests/test_seismic.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------
# Tabla 1 (Ca) y Tabla 2 (Cv) — NTC 5689 numeral 2.7.3.1
# --------------------------------------------------------------------------

SOIL_TYPES = ("A", "B", "C", "D", "E")

_BREAKPOINTS = (0.05, 0.10, 0.20, 0.30, 0.40)

TABLE_1_CA: Dict[str, Tuple[float, ...]] = {
    # valores en Aa = 0.05, 0.10, 0.20, 0.30, 0.40
    "A": (0.04, 0.08, 0.16, 0.24, 0.32),
    "B": (0.05, 0.10, 0.20, 0.30, 0.40),
    "C": (0.06, 0.12, 0.24, 0.33, 0.40),
    "D": (0.08, 0.16, 0.28, 0.36, 0.44),
    "E": (0.13, 0.25, 0.34, 0.36, 0.44),
}

TABLE_2_CV: Dict[str, Tuple[float, ...]] = {
    # valores en Av = 0.05, 0.10, 0.20, 0.30, 0.40
    "A": (0.04, 0.08, 0.16, 0.24, 0.32),
    "B": (0.05, 0.10, 0.20, 0.30, 0.40),
    "C": (0.09, 0.17, 0.32, 0.45, 0.56),
    "D": (0.12, 0.24, 0.40, 0.54, 0.64),
    "E": (0.18, 0.35, 0.64, 0.84, 0.96),
}

# Aa, Av por ciudad — NSR-10, mapa de amenaza sísmica (según Tabla
# A.2.3-2 del NSR-10, transcrita en el anexo CARGAS_DE_SISMO.xlsx del
# proyecto). Lista no exhaustiva; para un municipio no incluido, consultar
# directamente el mapa/tabla del NSR-10 vigente.
AA_AV_BY_CITY: Dict[str, Dict[str, object]] = {
    "Arauca": {"Aa": 0.15, "Av": 0.15, "zona": "Intermedia"},
    "Armenia": {"Aa": 0.25, "Av": 0.25, "zona": "Alta"},
    "Barranquilla": {"Aa": 0.10, "Av": 0.10, "zona": "Baja"},
    "Bogotá": {"Aa": 0.15, "Av": 0.20, "zona": "Intermedia"},
    "Bucaramanga": {"Aa": 0.25, "Av": 0.25, "zona": "Alta"},
    "Cali": {"Aa": 0.25, "Av": 0.25, "zona": "Alta"},
    "Cartagena": {"Aa": 0.10, "Av": 0.10, "zona": "Baja"},
    "Cúcuta": {"Aa": 0.35, "Av": 0.30, "zona": "Alta"},
    "Florencia": {"Aa": 0.20, "Av": 0.15, "zona": "Intermedia"},
    "Ibagué": {"Aa": 0.20, "Av": 0.20, "zona": "Intermedia"},
    "Leticia": {"Aa": 0.05, "Av": 0.05, "zona": "Baja"},
    "Manizales": {"Aa": 0.25, "Av": 0.25, "zona": "Alta"},
    "Medellín": {"Aa": 0.15, "Av": 0.20, "zona": "Intermedia"},
    "Mitú": {"Aa": 0.05, "Av": 0.05, "zona": "Baja"},
    "Mocoa": {"Aa": 0.30, "Av": 0.25, "zona": "Alta"},
    "Montería": {"Aa": 0.10, "Av": 0.15, "zona": "Intermedia"},
    "Neiva": {"Aa": 0.25, "Av": 0.25, "zona": "Alta"},
    "Pasto": {"Aa": 0.25, "Av": 0.25, "zona": "Alta"},
    "Pereira": {"Aa": 0.25, "Av": 0.25, "zona": "Alta"},
    "Popayán": {"Aa": 0.25, "Av": 0.20, "zona": "Alta"},
    "Puerto Carreño": {"Aa": 0.05, "Av": 0.05, "zona": "Baja"},
    "Puerto Inirida": {"Aa": 0.05, "Av": 0.05, "zona": "Baja"},
    "Quibdó": {"Aa": 0.35, "Av": 0.35, "zona": "Alta"},
    "Riohacha": {"Aa": 0.10, "Av": 0.15, "zona": "Intermedia"},
    "San Andrés": {"Aa": 0.10, "Av": 0.10, "zona": "Baja"},
    "Santa Marta": {"Aa": 0.15, "Av": 0.10, "zona": "Intermedia"},
    "San Jose del Guaviare": {"Aa": 0.05, "Av": 0.05, "zona": "Baja"},
    "Sincelejo": {"Aa": 0.10, "Av": 0.15, "zona": "Intermedia"},
    "Tunja": {"Aa": 0.20, "Av": 0.20, "zona": "Intermedia"},
    "Valledupar": {"Aa": 0.10, "Av": 0.10, "zona": "Baja"},
    "Villavicencio": {"Aa": 0.35, "Av": 0.30, "zona": "Alta"},
    "Yopal": {"Aa": 0.30, "Av": 0.20, "zona": "Alta"},
}


def _interp_table(row: Sequence[float], x: float) -> float:
    """
    Interpolación lineal de Ca o Cv según el numeral 2.7.3.1: "para valores
    intermedios ... interpolar linealmente para determinar el valor".
    Para x < 0.05 se aplica Ca=Aa / Cv=Av (según nota de la norma). Para
    x > 0.40 se usa el valor superior (extremo de tabla, x=0.40) de forma
    conservadora, ya que la norma no tabula más allá de este punto.
    """
    if x < _BREAKPOINTS[0]:
        return x
    if x >= _BREAKPOINTS[-1]:
        return row[-1]
    for i in range(len(_BREAKPOINTS) - 1):
        x0, x1 = _BREAKPOINTS[i], _BREAKPOINTS[i + 1]
        if x0 <= x <= x1:
            y0, y1 = row[i], row[i + 1]
            return y0 + (x - x0) * (y1 - y0) / (x1 - x0)
    raise AssertionError("unreachable")


def coef_ca(soil_type: str, aa: float) -> float:
    soil_type = soil_type.upper()
    if soil_type not in TABLE_1_CA:
        raise ValueError(
            f"Tipo de perfil de suelo '{soil_type}' no soportado por tabla "
            f"directa (use A-E; el tipo F requiere estudio específico de sitio)."
        )
    return _interp_table(TABLE_1_CA[soil_type], aa)


def coef_cv(soil_type: str, av: float) -> float:
    soil_type = soil_type.upper()
    if soil_type not in TABLE_2_CV:
        raise ValueError(
            f"Tipo de perfil de suelo '{soil_type}' no soportado por tabla "
            f"directa (use A-E; el tipo F requiere estudio específico de sitio)."
        )
    return _interp_table(TABLE_2_CV[soil_type], av)


class SeismicDirection(Enum):
    TRANSVERSAL = "transversal"   # perpendicular al corredor (marcos, R=4)
    LONGITUDINAL = "longitudinal"   # en dirección del corredor (vigas, R=6)


def response_reduction_factor(direction: SeismicDirection, height_m: float) -> float:
    """R según numeral 2.7.3: 4.0 en dirección arriostrada (marcos,
    transversal) y 6.0 en la no arriostrada (vigas, longitudinal), para
    estanterías de más de 2.44 m de altura al último nivel."""
    if height_m <= 2.44:
        return 4.0
    return 4.0 if direction == SeismicDirection.TRANSVERSAL else 6.0


def pallet_load_reduction_factor(
    direction: SeismicDirection,
    pl_promedio: Optional[float] = None,
    pl_maxima: Optional[float] = None,
) -> float:
    """PLRF según numeral 2.7.2: 1.0 en dirección transversal al corredor;
    PLpromedio/PLmaxima en dirección del corredor."""
    if direction == SeismicDirection.TRANSVERSAL:
        return 1.0
    if pl_promedio is None or pl_maxima is None or pl_maxima <= 0:
        raise ValueError(
            "Para dirección longitudinal se requieren PLpromedio y PLmaxima "
            "(numeral 2.7.2 NTC 5689) para calcular PLRF."
        )
    return pl_promedio / pl_maxima


def importance_factor(
    essential: bool = False,
    hazardous_contents: bool = False,
    public_access: bool = False,
) -> float:
    """Ip según numeral 2.7.2: 1.5 en los tres casos especiales indicados
    en la norma; 1.0 para el resto de las estructuras."""
    return 1.5 if (essential or hazardous_contents or public_access) else 1.0


def seismic_weight(pl: float, dl: float, ll: float = 0.0, plrf: float = 1.0) -> float:
    """Ws = 0.67 * PLRF * PL + DL + 0.25 * LL  (numeral 2.7.2)."""
    return 0.67 * plrf * pl + dl + 0.25 * ll


def cs_method1(cv: float, r: float, period_s: float) -> float:
    """Cs = 1.2*Cv / (R * T^(2/3))  — cálculo dinámico (numeral 2.7.3)."""
    return 1.2 * cv / (r * period_s ** (2.0 / 3.0))


def cs_method2(ca: float, r: float) -> float:
    """Cs = 2.5*Ca / R — límite superior / método simplificado (numeral 2.7.3)."""
    return 2.5 * ca / r


def seismic_response_coefficient(
    ca: float, cv: float, r: float, period_s: Optional[float] = None,
) -> float:
    """
    Coeficiente de respuesta sísmica Cs de diseño. Si se conoce el periodo
    fundamental T (de un análisis sustentado), Cs es el MENOR entre el
    método dinámico y el límite del método simplificado (la norma indica
    que el método 1 "no debe ser mayor" que el valor del método 2). Si no
    se conoce T, se usa directamente el método simplificado.
    """
    cs2 = cs_method2(ca, r)
    if period_s is None:
        return cs2
    cs1 = cs_method1(cv, r, period_s)
    return min(cs1, cs2)


def base_shear(cs: float, ip: float, ws: float) -> float:
    """V = Cs * Ip * Ws  (numeral 2.7.2)."""
    return cs * ip * ws


@dataclass
class LevelWeight:
    level_index: int
    elevation_m: float     # altura desde el piso, m
    weight_kn: float          # peso sísmico tributario del nivel (Ws del nivel), kN


def vertical_distribution(v_total: float, levels: Sequence[LevelWeight]) -> Dict[int, float]:
    """
    Distribución vertical del cortante sísmico de base V entre los
    niveles de carga, proporcional a peso x altura (procedimiento estándar
    de fuerzas laterales equivalentes, k=1, tal como lo aplica el
    calculista de referencia en CARGAS_DE_SISMO.xlsx):

        Fx_i = V * (w_i * h_i) / sum(w_j * h_j)
    """
    denom = sum(lv.weight_kn * lv.elevation_m for lv in levels)
    if denom <= 0:
        raise ValueError("La sumatoria peso*altura debe ser mayor que cero")
    return {
        lv.level_index: v_total * (lv.weight_kn * lv.elevation_m) / denom
        for lv in levels
    }


@dataclass
class SeismicResult:
    direction: SeismicDirection
    soil_type: str
    aa: float
    av: float
    ca: float
    cv: float
    r: float
    ip: float
    plrf: float
    ws: float
    cs: float
    period_s: Optional[float]
    v_base: float
    fx_by_level: Dict[int, float]


def compute_seismic(
    direction: SeismicDirection,
    soil_type: str,
    aa: float,
    av: float,
    pl: float,
    dl: float,
    ll: float,
    height_m: float,
    levels: Sequence[LevelWeight],
    pl_promedio: Optional[float] = None,
    pl_maxima: Optional[float] = None,
    period_s: Optional[float] = None,
    essential: bool = False,
    hazardous_contents: bool = False,
    public_access: bool = False,
    r_override: Optional[float] = None,
) -> SeismicResult:
    """Flujo completo NTC 5689 numeral 2.7 para una dirección dada."""
    ca = coef_ca(soil_type, aa)
    cv = coef_cv(soil_type, av)
    r = r_override if r_override is not None else response_reduction_factor(direction, height_m)
    ip = importance_factor(essential, hazardous_contents, public_access)
    plrf = pallet_load_reduction_factor(direction, pl_promedio, pl_maxima)
    ws = seismic_weight(pl, dl, ll, plrf)
    cs = seismic_response_coefficient(ca, cv, r, period_s)
    v = base_shear(cs, ip, ws)
    fx = vertical_distribution(v, levels) if levels else {}
    return SeismicResult(
        direction=direction, soil_type=soil_type.upper(), aa=aa, av=av,
        ca=ca, cv=cv, r=r, ip=ip, plrf=plrf, ws=ws, cs=cs,
        period_s=period_s, v_base=v, fx_by_level=fx,
    )
