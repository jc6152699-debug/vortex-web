"""
Generador paramétrico de la geometría de una estantería selectiva
porta-estibas (el tipo más común, base de los anexos de referencia:
torres de parales unidos por vigas en dirección X y arriostrados en el
plano Y-Z formando "marcos").
"""
from __future__ import annotations

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

    def __post_init__(self) -> None:
        if self.n_frames is None:
            self.n_frames = self.n_bays + 1

    @property
    def n_levels(self) -> int:
        return len(self.level_heights)


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
                    label=f"M{f}-{side[0].upper()}-N{lv}",
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
                    label=f"PARAL M{f}-{side[0].upper()} N{lv}-N{lv+1}",
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
                    label=f"VIGA B{b}-{side[0].upper()} N{lv}",
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

        for lv in range(p.n_levels):
            start_front = (lv % 2 == 0)
            ni = node_id_of[(f, "frente" if start_front else "fondo", lv)]
            nj = node_id_of[(f, "fondo" if start_front else "frente", lv + 1)]
            m = Member(
                id=mid, node_i=ni, node_j=nj,
                section=p.brace_section, kind=MemberKind.BRACE,
                z_axis_ref=(1.0, 0.0, 0.0),
                release_i_My=ConnectionRelease.pinned(),
                release_i_Mz=ConnectionRelease.pinned(),
                release_j_My=ConnectionRelease.pinned(),
                release_j_Mz=ConnectionRelease.pinned(),
                label=f"DIAGONAL M{f} N{lv}-N{lv+1}",
                frame_index=f, level_index=lv,
            )
            model.add_member(m)
            mid += 1

    return model
