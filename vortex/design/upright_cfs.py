"""
Verificación de parales (compresión + flexión biaxial), formados en frío
(AISI) o laminados en caliente (AISC), método ASD.

Alcance y simplificaciones (documentadas explícitamente, ver también
`sections/catalog.py`):

  - Pandeo por flexión (flexural buckling), ambos ejes: curva de columna
    de AISI/AISC modernas (Direct Strength Method / AISC 360, formulación
    2005+, equivalente en Ω=1.80 al método ASD clásico). Es el modo de
    falla que gobierna en la gran mayoría de parales de estantería bien
    proporcionados.
  - Pandeo flexo-torsional: SÓLO se evalúa si la sección trae Cw, xo, ro
    (constante de alabeo, distancia al centro de cortante, radio de giro
    polar) suministrados por el fabricante o por ensayo — ver advertencia
    en `sections/catalog.py`. Si faltan, se omite y se marca explícitamente
    "no verificado" en el resultado (nunca se asume un valor).
  - Área neta efectiva a compresión (Ae): si `section.Ae_known` está
    definido (recomendado, del fabricante o de un cálculo AISI Capítulo B
    completo de ancho efectivo) se usa directamente; si no, se aproxima
    como `A_bruta * perforation_ratio` — una aproximación gruesa, sólo
    para un primer análisis.
  - Interacción compresión + flexión biaxial: ecuación de interacción
    estándar ASD (AISC/AISI), incluyendo el factor de amplificación por
    efectos de segundo orden P-δ (Cm / (1-P/Pe)).

Referencias: NTC 5689 numeral 1.4 (remite a AISI - Specification for the
Design of Cold-Formed Steel Structural Members, y AISC - Allowable Stress
Design and Plastic Design).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from ..geometry.model import Section

OMEGA_C = 1.80   # AISI ASD, compresión
OMEGA_B = 1.67    # AISI/AISC ASD, flexión


def euler_stress(E: float, kl_r: float) -> float:
    if kl_r <= 0:
        return float("inf")
    return math.pi ** 2 * E / kl_r ** 2


def nominal_flexural_buckling_stress(Fy: float, Fe: float) -> float:
    """Fn según la curva de columna AISI S100 / AISC 360 (2005+)."""
    if Fe <= 0:
        return 0.0
    lam2 = Fy / Fe
    lam = math.sqrt(lam2)
    if lam <= 1.5:
        return (0.658 ** lam2) * Fy
    return (0.877 / lam2) * Fy


def flexural_torsional_buckling_stress(
    section: Section, E: float, G: float, KLy: float, KLt: float,
) -> Optional[float]:
    """
    Esfuerzo crítico elástico de pandeo flexo-torsional (AISI S100,
    ec. C4.2-2), para pandeo asociado al eje PERPENDICULAR al eje de
    simetría de una sección monosimétrica (p.ej. el paral canal con
    labios respecto a su eje horizontal). Requiere Cw, xo, ro > 0.

    `KLy`  : longitud efectiva para flexión respecto al eje de simetría
             (usada en Fey).
    `KLt`  : longitud efectiva para torsión.
    Devuelve None si faltan las propiedades necesarias (no verificado).
    """
    if section.Cw <= 0 or section.ro <= 0 or section.xo == 0.0:
        return None
    A, ro, xo = section.A, section.ro, section.xo
    beta = 1.0 - (xo / ro) ** 2
    Fey = euler_stress(E, KLy)
    Ft = (G * section.J + math.pi ** 2 * E * section.Cw / KLt ** 2) / (A * ro ** 2)
    if Fey <= 0 or Ft <= 0 or beta <= 0:
        return None
    disc = 1.0 - 4 * beta * Fey * Ft / (Fey + Ft) ** 2
    disc = max(disc, 0.0)
    Fe = (Fey + Ft) / (2 * beta) * (1.0 - math.sqrt(disc))
    return Fe


def effective_area(section: Section) -> float:
    if section.Ae_known is not None:
        return section.Ae_known
    return section.A * section.perforation_ratio


@dataclass
class UprightCheckResult:
    combo_id: str
    P: float             # kN, compresión positiva
    M2: float               # kN*m, alrededor de eje local y
    M3: float                # kN*m, alrededor de eje local z
    Pa: float                  # kN, capacidad axial admisible
    Ma2: float                   # kN*m, capacidad a flexión admisible (eje y)
    Ma3: float                    # kN*m, capacidad a flexión admisible (eje z)
    Pe2: float                       # kN, carga crítica de Euler eje y (amplificación)
    Pe3: float                        # kN, carga crítica de Euler eje z
    ratio_axial: float
    ratio_interaction: float
    governs: str                          # "axial" | "interaccion"
    ft_buckling_checked: bool
    notes: list = field(default_factory=list)

    @property
    def ratio(self) -> float:
        return max(self.ratio_axial, self.ratio_interaction)

    @property
    def ok(self) -> bool:
        return self.ratio <= 1.0


def check_upright_compression_bending(
    section: Section,
    combo_id: str,
    P: float, M2: float, M3: float,
    KLy: float, KLz: float,
    Cmy: float = 0.85, Cmz: float = 0.85,
    symmetry_axis: str = "y",
    KLt: Optional[float] = None,
) -> UprightCheckResult:
    """
    P>0 = compresión. M2, M3 momentos máximos de diseño (valor absoluto)
    en el elemento para la combinación `combo_id`. KLy, KLz = longitudes
    efectivas (K*L, ya con el factor de longitud efectiva incluido, p.ej.
    K=1.7 en la dirección no arriostrada según NTC 5689 numeral 6.3.1.1
    y la hoja de referencia del proyecto).
    """
    E, G, Fy = section.material.E, section.material.G, section.Fy
    notes = []

    Fe_y = euler_stress(E, KLy / section.ry) if section.ry > 0 else float("inf")
    Fe_z = euler_stress(E, KLz / section.rz) if section.rz > 0 else float("inf")

    ft_checked = False
    if symmetry_axis == "y" and KLt is not None:
        fe_ft = flexural_torsional_buckling_stress(section, E, G, KLy, KLt)
        if fe_ft is not None:
            Fe_z = min(Fe_z, fe_ft)
            ft_checked = True
    elif symmetry_axis == "z" and KLt is not None:
        fe_ft = flexural_torsional_buckling_stress(section, E, G, KLz, KLt)
        if fe_ft is not None:
            Fe_y = min(Fe_y, fe_ft)
            ft_checked = True
    if not ft_checked:
        notes.append(
            "Pandeo flexo-torsional NO verificado (faltan Cw/xo/ro del "
            "fabricante o KLt); confirmar con ensayo o cálculo AISI "
            "completo antes de emisión final."
        )

    Ae = effective_area(section)
    Fn_y = nominal_flexural_buckling_stress(Fy, Fe_y)
    Fn_z = nominal_flexural_buckling_stress(Fy, Fe_z)
    Fn = min(Fn_y, Fn_z)
    Pn = Ae * Fn
    Pa = Pn / OMEGA_C

    Ma2 = section.Sy * Fy / OMEGA_B
    Ma3 = section.Sz * Fy / OMEGA_B

    Pe2 = Ae * Fe_y
    Pe3 = Ae * Fe_z

    ratio_axial = P / Pa if Pa > 0 else float("inf")

    if ratio_axial <= 0.15 and P > 0:
        amp2 = amp3 = 1.0
        ratio_interaction = (
            ratio_axial + (M2 / Ma2 if Ma2 > 0 else 0.0) + (M3 / Ma3 if Ma3 > 0 else 0.0)
        )
    else:
        amp2 = Cmy / (1 - P / Pe2) if Pe2 > P > 0 else 1.0
        amp3 = Cmz / (1 - P / Pe3) if Pe3 > P > 0 else 1.0
        amp2 = max(amp2, 1.0)
        amp3 = max(amp3, 1.0)
        m2_term = amp2 * M2 / Ma2 if Ma2 > 0 else 0.0
        m3_term = amp3 * M3 / Ma3 if Ma3 > 0 else 0.0
        ratio_interaction = ratio_axial + m2_term + m3_term

    governs = "axial" if ratio_axial >= ratio_interaction else "interaccion"

    return UprightCheckResult(
        combo_id=combo_id, P=P, M2=M2, M3=M3,
        Pa=Pa, Ma2=Ma2, Ma3=Ma3, Pe2=Pe2, Pe3=Pe3,
        ratio_axial=ratio_axial, ratio_interaction=ratio_interaction,
        governs=governs, ft_buckling_checked=ft_checked, notes=notes,
    )
