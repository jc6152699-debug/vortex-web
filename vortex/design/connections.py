"""
Verificación de placas base / anclajes (NTC 5689 numerales 7.2 y 8) y de
diagonales de arriostramiento.

La verificación de anclajes al concreto (capacidad de arrancamiento por
cono de concreto, hendimiento, pryout — ACI 318 capítulo 17) NO se
recalcula desde cero aquí: se recibe como dato de entrada la capacidad
admisible del anclaje (`anchor_capacity_tension_kn`,
`anchor_capacity_shear_kn`), tal como se obtiene del informe de
evaluación técnica (ICC-ES u homólogo) del fabricante del anclaje para el
concreto, espesor de losa, espaciamiento y distancia a borde reales del
proyecto — que es, en la práctica, cómo se dimensionan los anclajes de
estantería en oficina. Este módulo sí calcula la DEMANDA (tensión y
cortante por anclaje) a partir de las reacciones de la base del paral,
mediante el método elástico de grupo de pernos (placa rígida).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Sequence, Tuple

from .upright_cfs import OMEGA_C, euler_stress, nominal_flexural_buckling_stress, effective_area
from ..geometry.model import Section

OMEGA_TENSION = 1.67


@dataclass
class BasePlateResult:
    combo_id: str
    P: float                    # kN, compresión(+)/tensión(-)
    Mx: float; My: float           # kN*m
    Vx: float; Vy: float             # kN
    bearing_pressure: float             # kPa
    bearing_allow: float
    ratio_bearing: float
    anchor_tension_max: float               # kN, por anclaje (0 si todos en compresión)
    anchor_shear_per_bolt: float
    ratio_anchor_tension: float
    ratio_anchor_shear: float
    notes: List[str] = field(default_factory=list)

    @property
    def ratio(self) -> float:
        return max(self.ratio_bearing, self.ratio_anchor_tension, self.ratio_anchor_shear)

    @property
    def ok(self) -> bool:
        return self.ratio <= 1.0


def check_base_plate(
    combo_id: str,
    P: float, Mx: float, My: float, Vx: float, Vy: float,
    plate_length: float, plate_width: float,
    anchor_positions: Sequence[Tuple[float, float]],
    f_c_concrete_mpa: float,
    anchor_capacity_tension_kn: float,
    anchor_capacity_shear_kn: float,
    bearing_allow_factor: float = 0.35,
) -> BasePlateResult:
    """
    `anchor_positions`: lista de (x,y) en metros, coordenadas de cada
    anclaje relativas al centro de la placa (ejes alineados con Mx,My).
    `f_c_concrete_mpa`: resistencia del concreto (f'c), MPa.
    `bearing_allow_factor`: factor sobre f'c para el aplastamiento
    admisible bajo la placa (0.35*f'c, ASD, valor usual AISC Design Guide 1).
    """
    n = len(anchor_positions)
    area_plate = plate_length * plate_width
    Sx = plate_width * plate_length ** 2 / 6.0
    Sy = plate_length * plate_width ** 2 / 6.0

    # P [kN] / area [m2] + M [kN*m] / S [m3] => kPa directamente
    sigma_max_kpa = P / area_plate + abs(Mx) / Sx + abs(My) / Sy
    bearing_allow = bearing_allow_factor * f_c_concrete_mpa * 1000.0  # MPa -> kPa
    ratio_bearing = sigma_max_kpa / bearing_allow if bearing_allow > 0 else float("inf")

    sum_x2 = sum(x ** 2 for x, y in anchor_positions) or 1e-9
    sum_y2 = sum(y ** 2 for x, y in anchor_positions) or 1e-9

    tensions = []
    for x, y in anchor_positions:
        t = -P / n + abs(My) * abs(x) / sum_x2 + abs(Mx) * abs(y) / sum_y2
        tensions.append(t)
    anchor_tension_max = max(max(tensions), 0.0)

    V_total = (Vx ** 2 + Vy ** 2) ** 0.5
    anchor_shear = V_total / n

    notes = []
    if anchor_tension_max > 0:
        notes.append(
            "Tensión en anclajes calculada por el método elástico de grupo "
            "de pernos (placa rígida); verificar además arrancamiento por "
            "cono de concreto, hendimiento y pryout según ACI 318 cap.17 "
            "con la geometría real de espaciamiento/borde del proyecto."
        )

    return BasePlateResult(
        combo_id=combo_id, P=P, Mx=Mx, My=My, Vx=Vx, Vy=Vy,
        bearing_pressure=sigma_max_kpa, bearing_allow=bearing_allow,
        ratio_bearing=ratio_bearing,
        anchor_tension_max=anchor_tension_max,
        anchor_shear_per_bolt=anchor_shear,
        ratio_anchor_tension=anchor_tension_max / anchor_capacity_tension_kn if anchor_capacity_tension_kn > 0 else 0.0,
        ratio_anchor_shear=anchor_shear / anchor_capacity_shear_kn if anchor_capacity_shear_kn > 0 else float("inf"),
        notes=notes,
    )


@dataclass
class BraceCheckResult:
    combo_id: str
    N: float               # kN, tensión(+)/compresión(-)
    capacity: float
    ratio: float
    slenderness: float
    notes: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.ratio <= 1.0


def check_brace(section: Section, combo_id: str, N: float, KL: float) -> BraceCheckResult:
    """Diagonal/riostra: elemento de dos fuerzas (axial puro, extremos
    articulados). N>0 tracción, N<0 compresión."""
    Fy = section.Fy
    notes = []
    r_min = min(section.ry, section.rz) if min(section.ry, section.rz) > 0 else 1e-9
    slenderness = KL / r_min
    if slenderness > 200:
        notes.append(
            f"Esbeltez KL/r={slenderness:.0f} excede el límite usual de 200 "
            f"para elementos secundarios (AISC/AISI); revisar longitud "
            f"arriostrada o sección."
        )

    if N >= 0:
        Ae = effective_area(section)
        capacity = Ae * Fy / OMEGA_TENSION
        ratio = N / capacity if capacity > 0 else float("inf")
    else:
        Fe = euler_stress(section.material.E, slenderness)
        Fn = nominal_flexural_buckling_stress(Fy, Fe)
        Ae = effective_area(section)
        capacity = Ae * Fn / OMEGA_C
        ratio = abs(N) / capacity if capacity > 0 else float("inf")

    return BraceCheckResult(
        combo_id=combo_id, N=N, capacity=capacity, ratio=ratio,
        slenderness=slenderness, notes=notes,
    )
