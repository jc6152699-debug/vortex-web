"""
Verificación de parales (compresión + flexión biaxial + cortante),
formados en frío (AISI) o laminados en caliente (AISC), método ASD.

Fórmulas y factores citados literalmente de NSR-10, Título F, Capítulo
F.4 "Estructuras de acero con perfiles de lámina formada en frío"
(adopción de AISI S100 para Colombia — el mismo cuerpo normativo al que
remite NTC 5689 numeral 1.4), verificados contra el texto del reglamento:

  - Pandeo por flexión (flexural buckling), ambos ejes: Fn según NSR-10
    F.4.3.4-1 a F.4.3.4-4 (Ωc=1.80, φc=0.85):
        λc ≤ 1.5:  Fn = (0.658^λc²)·Fy
        λc > 1.5:  Fn = (0.877/λc²)·Fy
        λc = sqrt(Fy/Fe),  Fe = π²E/(KL/r)²  (F.4.3.4-5, sección no sujeta
        a pandeo torsional/flexo-torsional)
  - Pandeo flexo-torsional (secciones monosimétricas, F.4.3.4-6 a -8):
    SÓLO se evalúa si la sección trae Cw, xo, ro (constante de alabeo,
    distancia al centro de cortante, radio de giro polar respecto al
    centro de cortante) suministrados por el fabricante o por ensayo —
    ver advertencia en `sections/catalog.py`. Si faltan, se omite y se
    marca explícitamente "no verificado" (nunca se asume un valor).
  - Área efectiva a compresión (Ae): calculada al esfuerzo f=Fn mediante
    el método de ancho efectivo de NSR-10 F.4.2.2 (`effective_area_at_stress`)
    cuando la sección trae la geometría de sus elementos planos
    (`Section.effective_width_segments`); si no, se aproxima como
    `A_bruta · perforation_ratio` (sólo para un primer análisis sin
    geometría detallada).
  - Cortante (F.4.3.3-44, φv=0.95, Ωv=1.60): Fv=0.60Fy para almas
    robustas; para almas esbeltas se usa conservadoramente el límite
    elástico F.4.3.3-47b (ver `shear_capacity`), sin el tramo de
    transición inelástica intermedio de la norma completa (aproximación
    del lado seguro, documentada en `shear_capacity`).
  - Interacción compresión + flexión biaxial: ecuación de interacción
    estándar ASD (AISC/AISI), incluyendo el factor de amplificación por
    efectos de segundo orden P-δ (Cm / (1-P/Pe)).

Referencias: NTC 5689 numeral 1.4 (remite a AISI - Specification for the
Design of Cold-Formed Steel Structural Members, y AISC - Allowable Stress
Design and Plastic Design) y NSR-10 Título F Capítulo F.4.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from ..geometry.model import Section

OMEGA_C = 1.80   # NSR-10 F.4.3.4 / AISI ASD, compresión (φc=0.85 en DCCR)
OMEGA_B = 1.67    # NSR-10 F.4.3.3 / AISI ASD, flexión (φb=0.90-0.95 en DCCR)
OMEGA_V = 1.60     # NSR-10 F.4.3.3-44 / AISI ASD, cortante (φv=0.95 en DCCR)


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


def plate_reduction_factor(w: float, t: float, f: float, E: float, k: float) -> float:
    """
    Factor de reducción ρ de ancho efectivo, NSR-10 F.4.2.2-1 a -5
    (idéntico a AISI S100 §B2.1): ρ=1 (sección no reducida) para λ≤0.673,
    ρ=(1-0.22/λ)/λ en caso contrario, con λ=sqrt(f/Fcr) y Fcr el esfuerzo
    crítico de pandeo elástico de placa (F.4.2.2-5, μ=0.3 para acero).
    Válida tanto para elementos rigidizados (k=4) como no rigidizados
    (k=0.43) — sólo cambia el coeficiente de pandeo de placa k.
    """
    if f <= 0 or w <= 0 or t <= 0:
        return 1.0
    mu = 0.3
    Fcr = k * math.pi ** 2 * E / (12.0 * (1 - mu ** 2) * (w / t) ** 2)
    lam = math.sqrt(f / Fcr)
    if lam <= 0.673:
        return 1.0
    rho = (1.0 - 0.22 / lam) / lam
    return min(rho, 1.0)


def effective_area_at_stress(section: Section, f: float) -> float:
    """
    Área efectiva Ae al esfuerzo de compresión f (NSR-10 F.4.2.2, con
    f=Fn para miembros a compresión según F.4.3.4-1). Calcula el ancho
    efectivo elemento por elemento a partir de
    `section.effective_width_segments`; si la sección no trae esa
    geometría (p.ej. viene de una ficha de fabricante sin detalle de
    elementos), recurre a la aproximación de `effective_area`.
    """
    if not section.effective_width_segments:
        return effective_area(section)
    E = section.material.E
    Ae = 0.0
    for seg in section.effective_width_segments:
        rho = plate_reduction_factor(seg["w"], seg["t"], f, E, seg["k"])
        Ae += rho * seg["w"] * seg["t"]
    return Ae


def shear_capacity(section: Section, Fy: float) -> float:
    """
    Resistencia admisible a cortante del alma, Va = Vn/Ωv (NSR-10
    F.4.3.3-44 a -48, Ωv=1.60). Usa el/los elementos marcados como
    "is_web" en `section.effective_width_segments` (si no hay, aproxima
    Aw=0.5·A y Fv=0.6Fy). Para almas esbeltas (h/t grande) se aplica
    directamente el límite elástico F.4.3.3-47b como cota conservadora,
    sin el tramo de transición inelástica (F.4.3.3-46) de la norma
    completa — subestima levemente Va en ese rango intermedio, nunca la
    sobreestima.
    """
    E = section.material.E
    kv = 5.34  # almas sin rigidizadores transversales (F.4.3.3-49/50)
    webs = [s for s in (section.effective_width_segments or []) if s.get("is_web")]
    if not webs:
        Aw = 0.5 * section.A
        return Aw * 0.60 * Fy / OMEGA_V
    Va_total = 0.0
    for seg in webs:
        h, t = seg["w"], seg["t"]
        Aw = h * t
        ht = h / t
        limit1 = 0.96 * math.sqrt(kv * E / Fy)
        if ht <= limit1:
            Fv = 0.60 * Fy
        else:
            Fv = 0.904 * E * kv / ht ** 2
        Va_total += Aw * Fv / OMEGA_V
    return Va_total


@dataclass
class UprightCheckResult:
    combo_id: str
    P: float             # kN, compresión positiva
    M2: float               # kN*m, alrededor de eje local y
    M3: float                # kN*m, alrededor de eje local z
    V2: float                 # kN, cortante en plano x-y
    V3: float                  # kN, cortante en plano x-z
    Pa: float                  # kN, capacidad axial admisible
    Ma2: float                   # kN*m, capacidad a flexión admisible (eje y)
    Ma3: float                    # kN*m, capacidad a flexión admisible (eje z)
    Va: float                       # kN, capacidad a cortante admisible (NSR-10 F.4.3.3-44)
    Pe2: float                       # kN, carga crítica de Euler eje y (amplificación)
    Pe3: float                        # kN, carga crítica de Euler eje z
    ratio_axial: float
    ratio_interaction: float
    ratio_v2: float
    ratio_v3: float
    governs: str                          # "axial" | "interaccion" | "cortante"
    ft_buckling_checked: bool
    notes: list = field(default_factory=list)

    @property
    def ratio(self) -> float:
        return max(self.ratio_axial, self.ratio_interaction, self.ratio_v2, self.ratio_v3)

    @property
    def ok(self) -> bool:
        return self.ratio <= 1.0

    @property
    def component_checks(self) -> dict:
        """
        Chequeo independiente por componente (P, M2, M3, V2, V3), estilo
        la tabla "CHEQUEO" de referencia del proyecto: cada componente se
        compara contra su propia capacidad admisible, además del ratio de
        interacción combinado (`ratio_interaction`) que gobierna el
        diseño real. Útil como verificación cruzada rápida.
        """
        return {
            "P": self.P <= self.Pa if self.Pa > 0 else False,
            "M2": self.M2 <= self.Ma2 if self.Ma2 > 0 else False,
            "M3": self.M3 <= self.Ma3 if self.Ma3 > 0 else False,
            "V2": self.V2 <= (self.Va if self.Va > 0 else float("inf")),
            "V3": self.V3 <= (self.Va if self.Va > 0 else float("inf")),
        }


def check_upright_compression_bending(
    section: Section,
    combo_id: str,
    P: float, M2: float, M3: float,
    KLy: float, KLz: float,
    V2: float = 0.0, V3: float = 0.0,
    Cmy: float = 0.85, Cmz: float = 0.85,
    symmetry_axis: str = "y",
    KLt: Optional[float] = None,
) -> UprightCheckResult:
    """
    P>0 = compresión. M2, M3 momentos máximos de diseño (valor absoluto)
    en el elemento para la combinación `combo_id`. KLy, KLz = longitudes
    efectivas (K*L, ya con el factor de longitud efectiva incluido: K=1.7
    en la dirección no arriostrada, K=1.0 en la arriostrada — NTC 5689
    numeral 6.3.1.1, confirmado además por RMI MH16.1-2008 numeral 6.3.1
    "Racks Not/Braced Against Sidesway" y por la hoja de referencia del
    proyecto).

    NOTA — chequeo pendiente (no implementado): RMI MH16.1-2008 numeral
    6.3.4 "Stability of Trussed-Braced Upright Frames" exige además un
    chequeo de pandeo GLOBAL del marco arriostrado completo (como
    columna equivalente, con una rigidez EI reducida por la flexibilidad
    a cortante de las diagonales/horizontales — fórmula Pcr con
    parámetros A, Ab, Ad, Ic, φ, k según el patrón de arriostramiento).
    No se implementa aquí porque la fórmula exacta (con su término de
    corrección por cortante) no pudo extraerse de forma confiable del
    documento fuente (ecuaciones en fuente Symbol/OMML mal conservadas en
    la conversión de texto); implementarla con un término mal transcrito
    sería peor que no implementarla. Para marcos altos y esbeltos,
    verificar este numeral manualmente contra el texto oficial de RMI
    MH16.1 antes de un diseño definitivo.
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

    Fn_y = nominal_flexural_buckling_stress(Fy, Fe_y)
    Fn_z = nominal_flexural_buckling_stress(Fy, Fe_z)
    Fn = min(Fn_y, Fn_z)
    # Ae se calcula al esfuerzo f=Fn (NSR-10 F.4.3.4-1), no a Fy: un
    # paral esbelto que pandea a Fn<Fy moviliza un ancho efectivo mayor
    # que el que tendría a fluencia plena.
    Ae = effective_area_at_stress(section, Fn)
    Pn = Ae * Fn
    Pa = Pn / OMEGA_C
    if section.effective_width_segments and Ae < 0.9 * section.A:
        notes.append(
            f"Ae={Ae * 1e4:.2f} cm² ({Ae / section.A:.0%} de A bruta): las alas se "
            f"redujeron como elemento NO rigidizado (k=0.43, conservador, ignora "
            f"el aporte del labio — NSR-10 F.4.2.4 completo podría dar mayor "
            f"capacidad). Si el labio es adecuado, verificar con el método "
            f"completo de elemento rigidizado de borde antes de descartar la "
            f"sección."
        )

    Ma2 = section.Sy * Fy / OMEGA_B
    Ma3 = section.Sz * Fy / OMEGA_B
    Va = shear_capacity(section, Fy)

    Pe2 = Ae * Fe_y
    Pe3 = Ae * Fe_z

    ratio_axial = P / Pa if Pa > 0 else float("inf")
    ratio_v2 = abs(V2) / Va if Va > 0 else float("inf")
    ratio_v3 = abs(V3) / Va if Va > 0 else float("inf")

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

    ratios = {
        "axial": ratio_axial, "interaccion": ratio_interaction,
        "cortante": max(ratio_v2, ratio_v3),
    }
    governs = max(ratios, key=ratios.get)

    return UprightCheckResult(
        combo_id=combo_id, P=P, M2=M2, M3=M3, V2=V2, V3=V3,
        Pa=Pa, Ma2=Ma2, Ma3=Ma3, Va=Va, Pe2=Pe2, Pe3=Pe3,
        ratio_axial=ratio_axial, ratio_interaction=ratio_interaction,
        ratio_v2=ratio_v2, ratio_v3=ratio_v3,
        governs=governs, ft_buckling_checked=ft_checked, notes=notes,
    )
