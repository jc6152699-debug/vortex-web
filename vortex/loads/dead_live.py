"""
Cargas muertas, vivas, de producto (PL) y de impacto — NTC 5689 numerales
2.3 y 2.4.
"""
from __future__ import annotations

from typing import Dict, List

from ..geometry.model import RackModel
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


def beam_udl_from_product_load(pl_per_level_kn: float, bay_length: float) -> float:
    """
    Convierte la carga de producto (PL, kN por bahía y nivel, numeral
    1.5.2) en carga uniformemente distribuida (kN/m) sobre CADA viga
    porta-estibas de esa bahía y nivel.

    Trayectoria de carga completa (verificada en
    ``tests/test_load_path.py`` contra el reparto manual bahía → viga →
    reacción → paral):

    1. La estiba se apoya sobre el par de vigas de la bahía (frente +
       fondo): cada viga recibe PL/2.
    2. Cada viga reparte esa carga a sus dos apoyos (parales) por
       equilibrio de la reacción de extremo.
    3. Un paral EXTREMO (frame_index 0 o n_bays, sólo conectado a una
       bahía) recibe la reacción de una sola viga por nivel; un paral
       INTERIOR (conectado a dos bahías consecutivas) recibe las
       reacciones de ambas — el doble de carga axial por nivel que un
       paral extremo. Esto no se calcula aparte: emerge automáticamente
       del modelo de pórtico espacial 3D continuo (un solo elemento
       MemberLoad por viga, aplicado con esta función), porque cada
       paral interior está conectado a las dos vigas adyacentes.
    """
    return (pl_per_level_kn / 2.0) / bay_length


def beam_udl_from_live_load(ll_kn_m2: float, frame_depth: float) -> float:
    """
    Convierte la carga viva (LL, kN/m², numeral 2.1 — plataformas de
    trabajo/pasillos, NO la de producto) en carga distribuida (kN/m)
    sobre cada viga, con el mismo criterio de reparto que
    `beam_udl_from_product_load`: ancho tributario = profundidad de
    marco / 2 por viga (frente + fondo).
    """
    return ll_kn_m2 * frame_depth / 2.0


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
