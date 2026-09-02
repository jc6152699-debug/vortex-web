"""
Generador paramétrico de la geometría de una estantería selectiva
porta-estibas (el tipo más común, base de los anexos de referencia:
torres de parales unidos por vigas en dirección X y arriostrados en el
plano Y-Z formando "marcos").
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional

from .model import (
    ConnectionRelease,
    EndFixity,
    Member,
    MemberKind,
    Node,
    RackModel,
    Section,
)

# Rigidez rotacional por defecto de una conexión viga-paral semirrígida
# típica de gancho ("boltless"), cuando no se dispone del ensayo tipo
# cantiléver (NTC 5689 numeral 9.4.1). Es un valor de referencia MODERADO
# usado únicamente para permitir un primer análisis; debe reemplazarse por
# el valor de ensayo antes de emitir una memoria de cálculo definitiva.
DEFAULT_BEAM_CONNECTION_KM = 60.0  # kN*m/rad

# Código de una letra para cada lado del marco, usado en las etiquetas de
# nudos y elementos (p.ej. "VIGA B0-F N3"). NO se puede derivar tomando la
# primera letra de "frente"/"fondo" (side[0].upper()): ambas palabras
# empiezan por "F" en español, así que esa fórmula generaba la MISMA
# etiqueta para dos elementos físicos distintos (el paral/viga del frente y
# el del fondo de cada marco quedaban ambos como "...-F..."), haciendo
# imposible distinguirlos en la memoria de cálculo — p.ej. "PARAL M0-F
# N0-N1" aparecía dos veces, una por cada elemento real. "fondo" (parte
# posterior del marco, en la fila de atrás) usa código "P" (posterior).
SIDE_CODE = {"frente": "F", "fondo": "P"}


@dataclass
class RackParameters:
    n_bays: int                     # número de bahías/módulos (dirección X)
    bay_length: float                 # m, longitud libre de viga
    frame_depth: float                  # m, profundidad del marco (dirección Y)
    level_heights: List[float]            # m, altura piso->nivel1, nivel1->2, ...
    upright_section: Section
    beam_section: Section
    brace_section: Section
    beam_connection_km: Optional[float] = DEFAULT_BEAM_CONNECTION_KM
    base_fixity: str = "pinned"    # "pinned" | "fixed"
    include_struts_all_levels: bool = True
    n_frames: Optional[int] = None  # por defecto n_bays + 1
    # Cuántos tramos de nivel abarca cada panel en zigzag de la diagonal de
    # arriostramiento del marco (1 = una diagonal por nivel, como antes; 2 =
    # una diagonal cada dos niveles, etc.). Ver `brace_levels_per_panel_for_angle`
    # y `brace_levels_per_panel_for_count` para derivarlo de un ángulo
    # objetivo o de una cantidad de diagonales deseada.
    brace_levels_per_panel: int = 1

    def __post_init__(self) -> None:
        if self.n_frames is None:
            self.n_frames = self.n_bays + 1
        self.brace_levels_per_panel = max(1, int(self.brace_levels_per_panel))

    @property
    def n_levels(self) -> int:
        return len(self.level_heights)


def brace_levels_per_panel_for_angle(
    angle_deg: float, frame_depth: float, level_heights: List[float],
) -> int:
    """
    Número de tramos de nivel por panel de diagonal que mejor aproxima un
    ángulo objetivo (medido desde la horizontal) para la diagonal de
    arriostramiento del marco, dada la profundidad del marco y las alturas
    de nivel disponibles. El ángulo real resultante puede consultarse con
    `resulting_brace_angle_deg` una vez fijado el número de tramos (los
    tramos de nivel no siempre son uniformes, así que el ángulo real varía
    ligeramente panel a panel).
    """
    angle_deg = min(max(angle_deg, 1.0), 89.0)
    avg_h = sum(level_heights) / len(level_heights) if level_heights else 1.0
    target_vertical_span = frame_depth / math.tan(math.radians(angle_deg))
    n = round(target_vertical_span / avg_h) if avg_h > 0 else 1
    return max(1, min(int(n), len(level_heights)))


def brace_levels_per_panel_for_count(panel_count: int, n_levels: int) -> int:
    """Tramos de nivel por panel que producen aproximadamente `panel_count`
    diagonales en total, dado el número de niveles de carga."""
    panel_count = max(1, int(panel_count))
    return max(1, round(n_levels / panel_count))


def resulting_brace_angle_deg(
    frame_depth: float, level_heights: List[float], levels_per_panel: int,
) -> float:
    """Ángulo real promedio (grados, desde la horizontal) de la diagonal de
    arriostramiento resultante para un `levels_per_panel` dado."""
    levels_per_panel = max(1, min(int(levels_per_panel), len(level_heights)))
    avg_h = sum(level_heights) / len(level_heights) if level_heights else 1.0
    vertical_span = avg_h * levels_per_panel
    if vertical_span <= 0:
        return 90.0
    return math.degrees(math.atan(frame_depth / vertical_span))


def brace_panel_count(n_levels: int, levels_per_panel: int) -> int:
    """Número de paneles de diagonal resultantes (para mostrar en la GUI)."""
    levels_per_panel = max(1, min(int(levels_per_panel), n_levels))
    return math.ceil(n_levels / levels_per_panel)


def _brace_panel_boundaries(n_levels: int, levels_per_panel: int) -> List[int]:
    levels_per_panel = max(1, min(levels_per_panel, n_levels))
    boundaries = list(range(0, n_levels, levels_per_panel))
    if boundaries[-1] != n_levels:
        boundaries.append(n_levels)
    return boundaries


def build_selective_rack(p: RackParameters) -> RackModel:
    model = RackModel(
        n_bays=p.n_bays,
        n_levels=p.n_levels,
        bay_length=p.bay_length,
        frame_depth=p.frame_depth,
        level_heights=list(p.level_heights),
    )

    elevations = [0.0]
    for h in p.level_heights:
        elevations.append(elevations[-1] + h)
    model.level_elevations = elevations

    node_id_of = {}  # (frame_idx, side, level_idx) -> node id
    nid = 1
    for f in range(p.n_frames):
        x = f * p.bay_length
        for side, y in (("frente", 0.0), ("fondo", p.frame_depth)):
            for lv, z in enumerate(elevations):
                restraints = (False,) * 6
                if lv == 0:
                    if p.base_fixity == "fixed":
                        restraints = (True, True, True, True, True, True)
                    else:
                        # "Articulada": se liberan los giros de flexión en
                        # los dos planos principales (rx global=My del
                        # paral, ry global=Mz del paral), pero se restringe
                        # el giro alrededor del eje vertical del paral
                        # (rz global = torsión propia de CADA paral, no
                        # acoplada entre parales por ningún otro elemento
                        # del modelo) — una placa base con pernos de anclaje
                        # sí resiste razonablemente la torsión del paral
                        # aunque se idealice como articulada en flexión; sin
                        # esta restricción el modelo global queda con un
                        # mecanismo de giro libre (ver tests/test_builder.py).
                        restraints = (True, True, True, False, False, True)
                node = Node(
                    id=nid,
                    x=x, y=y, z=z,
                    restraints=restraints,
                    label=f"M{f}-{SIDE_CODE[side]}-N{lv}",
                )
                model.add_node(node)
                node_id_of[(f, side, lv)] = nid
                nid += 1

    mid = 1

    # --- Parales (uprights): un elemento por segmento entre niveles -------
    for f in range(p.n_frames):
        for side in ("frente", "fondo"):
            for lv in range(p.n_levels):
                ni = node_id_of[(f, side, lv)]
                nj = node_id_of[(f, side, lv + 1)]
                m = Member(
                    id=mid, node_i=ni, node_j=nj,
                    section=p.upright_section, kind=MemberKind.UPRIGHT,
                    z_axis_ref=(1.0, 0.0, 0.0),
                    release_i_My=ConnectionRelease.rigid(),
                    release_i_Mz=ConnectionRelease.rigid(),
                    release_j_My=ConnectionRelease.rigid(),
                    release_j_Mz=ConnectionRelease.rigid(),
                    label=f"PARAL M{f}-{SIDE_CODE[side]} N{lv}-N{lv+1}",
                    frame_index=f, level_index=lv, side=side,
                )
                model.add_member(m)
                mid += 1

    # --- Vigas porta-estibas: conectan marcos consecutivos en cada nivel --
    beam_release = (
        ConnectionRelease.semirigid(p.beam_connection_km)
        if p.beam_connection_km is not None
        else ConnectionRelease.rigid()
    )
    for b in range(p.n_bays):
        for side in ("frente", "fondo"):
            for lv in range(1, p.n_levels + 1):
                ni = node_id_of[(b, side, lv)]
                nj = node_id_of[(b + 1, side, lv)]
                m = Member(
                    id=mid, node_i=ni, node_j=nj,
                    section=p.beam_section, kind=MemberKind.BEAM,
                    z_axis_ref=(0.0, 0.0, 1.0),
                    release_i_My=beam_release,
                    release_i_Mz=ConnectionRelease.pinned(),
                    release_j_My=beam_release,
                    release_j_Mz=ConnectionRelease.pinned(),
                    label=f"VIGA B{b}-{SIDE_CODE[side]} N{lv}",
                    bay_index=b, level_index=lv, side=side,
                )
                model.add_member(m)
                mid += 1

    # --- Arriostramiento del marco (plano Y-Z): diagonales en zigzag y
    #     travesaños horizontales frente-fondo ---------------------------
    for f in range(p.n_frames):
        levels_for_struts = range(p.n_levels + 1) if p.include_struts_all_levels else (0, p.n_levels)
        for lv in levels_for_struts:
            ni = node_id_of[(f, "frente", lv)]
            nj = node_id_of[(f, "fondo", lv)]
            m = Member(
                id=mid, node_i=ni, node_j=nj,
                section=p.brace_section, kind=MemberKind.BRACE,
                z_axis_ref=(0.0, 0.0, 1.0),
                release_i_My=ConnectionRelease.pinned(),
                release_i_Mz=ConnectionRelease.pinned(),
                release_j_My=ConnectionRelease.pinned(),
                release_j_Mz=ConnectionRelease.pinned(),
                label=f"TRAVESAÑO M{f} N{lv}",
                frame_index=f, level_index=lv,
            )
            model.add_member(m)
            mid += 1

        boundaries = _brace_panel_boundaries(p.n_levels, p.brace_levels_per_panel)
        for panel_i in range(len(boundaries) - 1):
            lv0, lv1 = boundaries[panel_i], boundaries[panel_i + 1]
            start_front = (panel_i % 2 == 0)
            ni = node_id_of[(f, "frente" if start_front else "fondo", lv0)]
            nj = node_id_of[(f, "fondo" if start_front else "frente", lv1)]
            m = Member(
                id=mid, node_i=ni, node_j=nj,
                section=p.brace_section, kind=MemberKind.BRACE,
                z_axis_ref=(1.0, 0.0, 0.0),
                release_i_My=ConnectionRelease.pinned(),
                release_i_Mz=ConnectionRelease.pinned(),
                release_j_My=ConnectionRelease.pinned(),
                release_j_Mz=ConnectionRelease.pinned(),
                label=f"DIAGONAL M{f} N{lv0}-N{lv1}",
                frame_index=f, level_index=lv0,
            )
            model.add_member(m)
            mid += 1

    return model
