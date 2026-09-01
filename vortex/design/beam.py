"""
Verificación de vigas porta-estibas: flexión, cortante y deflexión de
servicio (NTC 5689, con Imp aplicado sólo a flexión/cortante — numeral
2.4 — y excluido de la verificación de deflexión).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

import numpy as np

from ..analysis.solve import MemberForces
from ..geometry.model import Section

OMEGA_B = 1.67
DEFLECTION_LIMIT_RATIO = 180.0   # L/180, criterio usual de estantería (RMI/NTC 5689 Anexo B)


def beam_moment_at(mf: MemberForces, w_local_z: float, L: float, x: float) -> float:
    """Momento M2(x) [kN*m] por equilibrio del tramo libre desde el nodo i,
    para un tramo prismático bajo carga uniforme w_local_z (kN/m, en el
    eje local z) sin otras cargas intermedias. Fórmula verificada
    numéricamente en tests/test_beam_design.py."""
    return mf.M2_i - mf.V3_i * x + w_local_z * x ** 2 / 2.0


def beam_shear_at(mf: MemberForces, w_local_z: float, x: float) -> float:
    return -mf.V3_i + w_local_z * x


def moment_envelope(mf: MemberForces, w_local_z: float, L: float, n: int = 41) -> Tuple[float, float]:
    """Devuelve (x_max, |M|_max) evaluando M(x) en `n` estaciones."""
    xs = np.linspace(0.0, L, n)
    ms = [beam_moment_at(mf, w_local_z, L, x) for x in xs]
    i = int(np.argmax(np.abs(ms)))
    return xs[i], abs(ms[i])


def deflection_profile(
    L: float, EI: float, v1: float, theta1: float, v2: float, theta2: float,
    w: float, n: int = 41,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Perfil de deflexión v(x) de un tramo prismático bajo carga uniforme w
    (kN/m, eje local z) con condiciones de frontera reales en ambos
    extremos (v, rotación INTERNA de la viga — ya considerando la
    flexibilidad de la conexión, ver `MemberForces.r_int_y`).
    Resuelve EI*v'''' = w con las 4 condiciones de frontera dadas.
    """
    A = np.array([
        [L ** 2, L ** 3],
        [2 * L, 3 * L ** 2],
    ])
    b = np.array([
        v2 - v1 - theta1 * L - w * L ** 4 / (24 * EI),
        theta2 - theta1 - w * L ** 3 / (6 * EI),
    ])
    a2, a3 = np.linalg.solve(A, b)
    a0, a1 = v1, theta1
    xs = np.linspace(0.0, L, n)
    vs = a0 + a1 * xs + a2 * xs ** 2 + a3 * xs ** 3 + w * xs ** 4 / (24 * EI)
    return xs, vs


@dataclass
class BeamCheckResult:
    combo_id: str
    Mmax: float
    x_Mmax: float
    Vmax: float
    fb: float
    Fb_allow: float
    fv: float
    Fv_allow: float
    ratio_bending: float
    ratio_shear: float
    deflection_max: float = 0.0
    deflection_limit: float = 0.0
    ratio_deflection: float = 0.0
    notes: List[str] = field(default_factory=list)

    @property
    def ratio(self) -> float:
        return max(self.ratio_bending, self.ratio_shear, self.ratio_deflection)

    @property
    def ok(self) -> bool:
        return self.ratio <= 1.0


def check_beam(
    section: Section,
    combo_id: str,
    mf: MemberForces,
    w_local_z: float,
    L: float,
    shear_area_factor: float = 0.5,
) -> BeamCheckResult:
    """
    `shear_area_factor` aproxima el área efectiva a cortante como una
    fracción del área bruta (0.5 es una aproximación conservadora usual
    para perfiles tipo caja/canal cuando no se dispone del área de alma
    exacta del fabricante).
    """
    Fy = section.Fy
    x_max, Mmax = moment_envelope(mf, w_local_z, L)
    Vmax = max(abs(mf.V3_i), abs(mf.V3_j))

    Fb_allow = Fy / OMEGA_B
    fb = Mmax / section.Sy if section.Sy > 0 else float("inf")

    Aw = section.A * shear_area_factor
    Fv_allow = 0.4 * Fy
    fv = Vmax / Aw if Aw > 0 else float("inf")

    notes = []
    if section.Ae_known is None:
        notes.append(
            "Módulo de sección (Sy) calculado con área bruta geométrica "
            "idealizada; verificar con la ficha certificada del fabricante."
        )

    return BeamCheckResult(
        combo_id=combo_id, Mmax=Mmax, x_Mmax=x_max, Vmax=Vmax,
        fb=fb, Fb_allow=Fb_allow, fv=fv, Fv_allow=Fv_allow,
        ratio_bending=fb / Fb_allow if Fb_allow > 0 else float("inf"),
        ratio_shear=fv / Fv_allow if Fv_allow > 0 else float("inf"),
        notes=notes,
    )


def check_deflection(
    result: BeamCheckResult,
    section: Section,
    mf: MemberForces,
    w_local_z_service: float,
    L: float,
    limit_ratio: float = DEFLECTION_LIMIT_RATIO,
) -> BeamCheckResult:
    """
    Verifica la deflexión bajo cargas de SERVICIO (LL+PL, sin mayorar y
    sin impacto — numeral 2.4) contra L/`limit_ratio` (por defecto
    L/180). Debe llamarse con las fuerzas de un análisis SEPARADO bajo
    cargas de servicio (no la combinación mayorada usada para `mf`).
    """
    EI = section.material.E * section.Iy
    v1 = 0.0
    v2 = 0.0
    theta1, theta2 = mf.r_int_y
    xs, vs = deflection_profile(L, EI, v1, theta1, v2, theta2, w_local_z_service)
    dmax = float(np.max(np.abs(vs)))
    limit = L / limit_ratio
    result.deflection_max = dmax
    result.deflection_limit = limit
    result.ratio_deflection = dmax / limit if limit > 0 else float("inf")
    return result
