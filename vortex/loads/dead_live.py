"""
Cargas muertas, vivas, de producto (PL) y de impacto — NTC 5689 numerales
2.3 y 2.4.
"""
from __future__ import annotations

from typing import Dict, List

from ..geometry.model import MemberKind, RackModel
from ..units import G

def dead_load_uprights(model: RackModel) -> Dict[int, float]:
    """
    Peso propio de cada elemento (kN), a partir de la densidad del
    material y el volumen (A * L). Se reporta por elemento; el motor de
    análisis lo aplica como carga distribuida a lo largo del eje del
    elemento.
    """
    result: Dict[int, float] = {}
    for mid, m in model.members.items():
        length = model.member_length(m)
        weight_kn = m.section.material.density * m.section.A * length
        result[mid] = weight_kn
    return result


def product_load_levels(
    model: RackModel, pl_per_level_kn: Dict[int, float],
) -> Dict[int, float]:
    """
    Reparte la carga de producto (PL) de cada nivel entre las vigas de ese
    nivel (frente y fondo), asumiendo que la estiba se apoya por igual en
    ambas filas de vigas. `pl_per_level_kn` es la carga total de producto
    (peso de la estiba/mercancía) por nivel y bahía, tal como se define en
    la placa de capacidad (numeral 1.5.2).

    Devuelve la carga por elemento (kN, uniformemente distribuida a lo
    largo de cada viga).
    """
    beams = model.members_of_kind(MemberKind.BEAM)
    result: Dict[int, float] = {}
    for m in beams:
        pl_level = pl_per_level_kn.get(m.level_index, 0.0)
        # La estiba se apoya sobre el par de vigas (frente+fondo) de la
        # bahía: cada viga recibe la mitad de la carga de esa bahía/nivel.
        result[m.id] = pl_level / 2.0
    return result


def impact_load(pl_per_level_kn: Dict[int, float], factor: float = 0.25) -> Dict[int, float]:
    """
    Carga vertical de impacto (numeral 2.4): 25 % del peso de una unidad
    de almacenamiento, aplicada en la posición más desfavorable, sólo para
    el diseño de vigas portantes, brazos y sus conexiones (no para
    deflexión, ni para parales o marcos).
    """
    return {lvl: factor * pl for lvl, pl in pl_per_level_kn.items()}


def horizontal_notional_load(dl_total: float, pl_total: float, method: str = "ASD") -> float:
    """
    Fuerza horizontal mínima del numeral 2.5.1, alternativa a sismo/viento
    para el diseño de conexiones de vigas y arriostramientos: 1.5% de
    (DL+PL) para ASD, o 1.5% de los valores factorados para LRFD (se
    asume que dl_total/pl_total ya vienen factorados si method="LRFD").
    """
    return 0.015 * (dl_total + pl_total)
