"""
Combinaciones de carga — NTC 5689 numerales 2.1 (ASD) y 2.2 (LRFD).

Los factores y el texto de cada combinación se transcriben literalmente de
la norma. Las combinaciones que en la norma se escriben con una
alternativa "(SL o RL)" o "(WL o EL)" se expanden aquí en combinaciones
concretas independientes (una por cada alternativa), de forma que el
motor de análisis pueda evaluarlas todas y el envolvente de diseño tome
la más desfavorable — que es, en la práctica, como se usan estas
combinaciones en oficina.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List


class LoadCase(Enum):
    DL = "DL"           # carga muerta
    LL = "LL"             # carga viva (no de producto)
    PL = "PL"              # carga de producto/estibas
    PL_APP = "PL_APP"       # carga de producto aplicable para arrancamiento
    SL = "SL"                 # carga de granizo
    RL = "RL"                  # carga de lluvia
    WL = "WL"                   # carga de viento
    EL = "EL"                    # carga sísmica
    IMP = "IMP"                   # carga de impacto vertical


class DesignMethod(Enum):
    ASD = "ASD"
    LRFD = "LRFD"


class MemberScope(Enum):
    ALL = "todos los componentes"
    BEAMS_ONLY = "vigas portantes y sus conexiones únicamente"


@dataclass
class Combination:
    id: str
    method: DesignMethod
    description: str
    factors: Dict[LoadCase, float]
    scope: MemberScope = MemberScope.ALL
    note: str = ""

    def label(self) -> str:
        terms = []
        for lc, f in self.factors.items():
            if f == 0:
                continue
            sign = "+" if f >= 0 else "-"
            terms.append(f"{sign} {abs(f):g}{lc.value}")
        s = " ".join(terms)
        if s.startswith("+ "):
            s = s[2:]
        return f"{self.id}) {s}"


def asd_combinations(
    apply_067_seismic: bool = True,
    apply_075_uplift_wind_seismic: bool = False,
) -> List[Combination]:
    """
    NTC 5689 numeral 2.1. `apply_067_seismic=True` aplica el factor 0.67 a
    EL permitido cuando el sismo se calculó según el numeral 2.7 (nuestro
    caso, ver loads.seismic). `apply_075_uplift_wind_seismic=True` aplica
    el factor opcional 0.75 a los casos 3 y 4.
    """
    el_factor = 0.67 if apply_067_seismic else 1.0
    mult = 0.75 if apply_075_uplift_wind_seismic else 1.0

    combos: List[Combination] = [
        Combination(
            "1", DesignMethod.ASD, "Carga muerta crítica",
            {LoadCase.DL: 1.0},
        ),
        Combination(
            "2", DesignMethod.ASD, "Carga de gravedad crítica (con granizo)",
            {LoadCase.DL: 1.0, LoadCase.LL: 1.0, LoadCase.SL: 1.0, LoadCase.PL: 1.0},
        ),
        Combination(
            "2", DesignMethod.ASD, "Carga de gravedad crítica (con lluvia)",
            {LoadCase.DL: 1.0, LoadCase.LL: 1.0, LoadCase.RL: 1.0, LoadCase.PL: 1.0},
        ),
        Combination(
            "3-WL", DesignMethod.ASD, "Arrancamiento por viento",
            {LoadCase.DL: mult * 1.0, LoadCase.WL: -mult * 1.0, LoadCase.PL_APP: mult * 1.0},
        ),
        Combination(
            "3-EL", DesignMethod.ASD, "Arrancamiento por sismo",
            {LoadCase.DL: mult * 1.0, LoadCase.EL: -mult * el_factor, LoadCase.PL_APP: mult * 1.0},
        ),
        Combination(
            "4-WL", DesignMethod.ASD, "Gravedad más viento crítico (con granizo)",
            {LoadCase.DL: mult * 1.0, LoadCase.LL: mult * 1.0, LoadCase.SL: mult * 0.5,
             LoadCase.WL: mult * 1.0, LoadCase.PL: mult * 1.0},
        ),
        Combination(
            "4-EL", DesignMethod.ASD, "Gravedad más sismo crítico (con granizo)",
            {LoadCase.DL: mult * 1.0, LoadCase.LL: mult * 1.0, LoadCase.SL: mult * 0.5,
             LoadCase.EL: mult * el_factor, LoadCase.PL: mult * 1.0},
        ),
        Combination(
            "5", DesignMethod.ASD, "Entrepaño más impacto crítico (con granizo)",
            {LoadCase.DL: 1.0, LoadCase.LL: 1.0, LoadCase.SL: 0.5,
             LoadCase.PL: 0.88, LoadCase.IMP: 1.0},
            scope=MemberScope.BEAMS_ONLY,
        ),
    ]
    return combos


def lrfd_combinations(apply_el_factor_10: bool = True) -> List[Combination]:
    """
    NTC 5689 numeral 2.2. `apply_el_factor_10=True` usa el factor de carga
    EL=1.0 (en vez de 1.5) en los casos 5 y 6, permitido cuando el sismo
    se calculó según el numeral 2.7 (nota de la norma) — nuestro caso.
    """
    el_factor = 1.0 if apply_el_factor_10 else 1.5
    el_factor_uplift = 1.0 if apply_el_factor_10 else 1.5

    combos: List[Combination] = [
        Combination(
            "1", DesignMethod.LRFD, "Carga muerta",
            {LoadCase.DL: 1.4, LoadCase.LL: 1.0, LoadCase.PL: 1.2},
        ),
        Combination(
            "2", DesignMethod.LRFD, "Carga viva / producto (con granizo)",
            {LoadCase.DL: 1.2, LoadCase.LL: 1.6, LoadCase.SL: 0.5, LoadCase.PL: 1.4},
        ),
        Combination(
            "2", DesignMethod.LRFD, "Carga viva / producto (con lluvia)",
            {LoadCase.DL: 1.2, LoadCase.LL: 1.6, LoadCase.RL: 0.5, LoadCase.PL: 1.4},
        ),
        Combination(
            "3a", DesignMethod.LRFD, "Granizo/lluvia (con viva)",
            {LoadCase.DL: 1.2, LoadCase.SL: 1.6, LoadCase.LL: 0.5, LoadCase.PL: 0.85},
        ),
        Combination(
            "3b", DesignMethod.LRFD, "Granizo/lluvia (con viento)",
            {LoadCase.DL: 1.2, LoadCase.SL: 1.6, LoadCase.WL: 0.8, LoadCase.PL: 0.85},
        ),
        Combination(
            "4", DesignMethod.LRFD, "Carga de viento",
            {LoadCase.DL: 1.2, LoadCase.WL: 1.3, LoadCase.LL: 0.5,
             LoadCase.SL: 0.5, LoadCase.PL: 0.85},
        ),
        Combination(
            "5", DesignMethod.LRFD, "Carga sísmica",
            {LoadCase.DL: 1.2, LoadCase.EL: el_factor,
             LoadCase.LL: 0.5, LoadCase.SL: 0.2, LoadCase.PL: 0.85},
        ),
        Combination(
            "6-WL", DesignMethod.LRFD, "Arrancamiento por viento",
            {LoadCase.DL: 0.9, LoadCase.WL: -1.3, LoadCase.PL_APP: 0.9},
        ),
        Combination(
            "6-EL", DesignMethod.LRFD, "Arrancamiento por sismo",
            {LoadCase.DL: 0.9, LoadCase.EL: -el_factor_uplift, LoadCase.PL_APP: 0.9},
        ),
        Combination(
            "7", DesignMethod.LRFD, "Producto/viva/impacto (entrepañosy conexiones)",
            {LoadCase.DL: 1.2, LoadCase.LL: 1.6, LoadCase.SL: 0.5,
             LoadCase.PL: 1.4, LoadCase.IMP: 1.4},
            scope=MemberScope.BEAMS_ONLY,
        ),
    ]
    return combos
