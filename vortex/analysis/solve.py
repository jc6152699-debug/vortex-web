"""
Ensamblaje y solución del modelo global por el método de la rigidez
directa, y recuperación de fuerzas internas por elemento (P, V2, V3, T,
M2, M3 en notación estilo SAP2000: P=axial, V2/V3=cortantes en los planos
locales, T=torsión, M2/M3=momentos flectores locales).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from ..geometry.model import ConnectionRelease, RackModel, Member
from .stiffness import (
    Condensation,
    condense_bending_block,
    local_axes,
    rigid_local_stiffness,
    transformation_matrix,
)

DOF_PER_NODE = 6


@dataclass
class NodalLoad:
    node_id: int
    fx: float = 0.0
    fy: float = 0.0
    fz: float = 0.0
    mx: float = 0.0
    my: float = 0.0
    mz: float = 0.0


@dataclass
class MemberLoad:
    """Carga uniformemente distribuida (kN/m) a lo largo del elemento,
    en componentes GLOBALES."""
    member_id: int
    wx: float = 0.0
    wy: float = 0.0
    wz: float = 0.0


@dataclass
class MemberGeometry:
    """Datos geométricos/locales precalculados de un elemento, reutilizados
    entre patrones de carga."""
    length: float
    ex: np.ndarray
    ey: np.ndarray
    ez: np.ndarray
    T: np.ndarray            # 12x12
    k_local_rigid: np.ndarray  # 12x12, ambos extremos rígidos
    cond_z: Condensation       # bloque v,rz  (índices 1,5,7,11)
    cond_y: Condensation        # bloque w,ry  (índices 2,4,8,10)
    k_local: np.ndarray           # 12x12 con condensación aplicada
    k_global: np.ndarray            # 12x12 en coordenadas globales
    dof_map: Tuple[int, ...]          # 12 índices de GDL globales


def _member_geometry(model: RackModel, member: Member) -> MemberGeometry:
    ni, nj = model.nodes[member.node_i], model.nodes[member.node_j]
    ex, ey, ez = local_axes(ni.xyz, nj.xyz, member.z_axis_ref)
    L = model.member_length(member)
    T = transformation_matrix(ex, ey, ez)

    s = member.section
    k_rigid = rigid_local_stiffness(s.material.E, s.material.G, s.A, s.Iy, s.Iz, s.J, L)

    kz_rigid = k_rigid[np.ix_([1, 5, 7, 11], [1, 5, 7, 11])]
    ky_rigid = k_rigid[np.ix_([2, 4, 8, 10], [2, 4, 8, 10])]
    cond_z = condense_bending_block(kz_rigid, member.release_i_Mz, member.release_j_Mz)
    cond_y = condense_bending_block(ky_rigid, member.release_i_My, member.release_j_My)

    k_local = k_rigid.copy()
    z_idx = [1, 5, 7, 11]
    y_idx = [2, 4, 8, 10]
    k_local[np.ix_(z_idx, z_idx)] = cond_z.k_reduced
    k_local[np.ix_(y_idx, y_idx)] = cond_y.k_reduced

    k_global = T.T @ k_local @ T

    return MemberGeometry(
        length=L, ex=ex, ey=ey, ez=ez, T=T,
        k_local_rigid=k_rigid, cond_z=cond_z, cond_y=cond_y,
        k_local=k_local, k_global=k_global, dof_map=(),
    )


def _build_dof_index(model: RackModel) -> Dict[int, int]:
    """Nodo -> índice base (0-based) de su primer GDL en el vector global."""
    ordered_ids = sorted(model.nodes.keys())
    return {nid: i * DOF_PER_NODE for i, nid in enumerate(ordered_ids)}


def _member_dof_map(model: RackModel, member: Member, base_idx: Dict[int, int]) -> List[int]:
    bi, bj = base_idx[member.node_i], base_idx[member.node_j]
    return [bi + k for k in range(6)] + [bj + k for k in range(6)]


def _element_equivalent_load_local(geom: MemberGeometry, w_local: np.ndarray) -> np.ndarray:
    """
    Vector de carga consistente (12x1, coordenadas locales) para una carga
    uniformemente distribuida w_local=[wx,wy,wz] (kN/m) a lo largo del
    elemento, obtenido por integración de las funciones de forma
    (∫ N q dx) y luego condensado según las liberaciones de extremo.
    """
    L = geom.length
    wx, wy, wz = w_local
    f = np.zeros(12)
    f[0] = wx * L / 2.0
    f[6] = wx * L / 2.0

    f4_z_rigid = np.array([wy * L / 2.0, wy * L ** 2 / 12.0, wy * L / 2.0, -wy * L ** 2 / 12.0])
    f4_z = geom.cond_z.condense_load(f4_z_rigid)
    z_idx = [1, 5, 7, 11]
    for k, idx in enumerate(z_idx):
        f[idx] = f4_z[k]

    f4_y_rigid = np.array([wz * L / 2.0, -wz * L ** 2 / 12.0, wz * L / 2.0, wz * L ** 2 / 12.0])
    f4_y = geom.cond_y.condense_load(f4_y_rigid)
    y_idx = [2, 4, 8, 10]
    for k, idx in enumerate(y_idx):
        f[idx] = f4_y[k]

    return f


@dataclass
class MemberForces:
    member_id: int
    P_i: float; V2_i: float; V3_i: float; T_i: float; M2_i: float; M3_i: float
    P_j: float; V2_j: float; V3_j: float; T_j: float; M2_j: float; M3_j: float
    r_int_z: Tuple[float, float] = (0.0, 0.0)  # rotaciones internas reales (rz) en i,j
    r_int_y: Tuple[float, float] = (0.0, 0.0)  # rotaciones internas reales (ry) en i,j


@dataclass
class AnalysisResult:
    displacements: Dict[int, np.ndarray]      # node_id -> 6-vector global
    reactions: Dict[int, np.ndarray]             # node_id -> 6-vector global (solo GDL restringidos)
    member_forces: Dict[int, MemberForces]         # member_id -> fuerzas locales
    member_geometry: Dict[int, MemberGeometry] = field(default_factory=dict)


def analyze(
    model: RackModel,
    nodal_loads: List[NodalLoad],
    member_loads: Optional[List[MemberLoad]] = None,
) -> AnalysisResult:
    member_loads = member_loads or []
    base_idx = _build_dof_index(model)
    n_dof = DOF_PER_NODE * len(model.nodes)

    K = np.zeros((n_dof, n_dof))
    F = np.zeros(n_dof)

    geoms: Dict[int, MemberGeometry] = {}
    dof_maps: Dict[int, List[int]] = {}
    for mid, member in model.members.items():
        geom = _member_geometry(model, member)
        dmap = _member_dof_map(model, member, base_idx)
        geoms[mid] = geom
        dof_maps[mid] = dmap
        K[np.ix_(dmap, dmap)] += geom.k_global

    for load in nodal_loads:
        b = base_idx[load.node_id]
        F[b:b + 6] += [load.fx, load.fy, load.fz, load.mx, load.my, load.mz]

    for mload in member_loads:
        member = model.members[mload.member_id]
        geom = geoms[mload.member_id]
        w_global = np.array([mload.wx, mload.wy, mload.wz])
        R = np.vstack([geom.ex, geom.ey, geom.ez])
        w_local = R @ w_global
        f_local = _element_equivalent_load_local(geom, w_local)
        f_global = geom.T.T @ f_local
        dmap = dof_maps[mload.member_id]
        F[dmap] += f_global

    for nid, node in model.nodes.items():
        b = base_idx[nid]
        for k in range(6):
            if not node.restraints[k] and node.springs[k] != 0.0:
                K[b + k, b + k] += node.springs[k]

    restrained = np.zeros(n_dof, dtype=bool)
    for nid, node in model.nodes.items():
        b = base_idx[nid]
        for k in range(6):
            restrained[b + k] = node.restraints[k]
    free = ~restrained

    Kff = K[np.ix_(free, free)]
    Ff = F[free]
    if np.linalg.matrix_rank(Kff) < Kff.shape[0]:
        raise np.linalg.LinAlgError(
            "La matriz de rigidez es singular (mecanismo/inestabilidad). "
            "Revise apoyos, arriostramiento y conexiones del modelo."
        )
    Uf = np.linalg.solve(Kff, Ff)

    U = np.zeros(n_dof)
    U[free] = Uf

    Fs_internal = K[np.ix_(restrained, free)] @ Uf
    reactions_vec = np.zeros(n_dof)
    reactions_vec[restrained] = Fs_internal - F[restrained]

    displacements = {
        nid: U[base_idx[nid]: base_idx[nid] + 6] for nid in model.nodes
    }
    reactions = {
        nid: reactions_vec[base_idx[nid]: base_idx[nid] + 6] for nid in model.nodes
        if any(model.nodes[nid].restraints)
    }

    member_forces: Dict[int, MemberForces] = {}
    for mid, member in model.members.items():
        geom = geoms[mid]
        dmap = dof_maps[mid]
        u_global = U[dmap]
        u_local = geom.T @ u_global

        w_global = np.array([0.0, 0.0, 0.0])
        for mload in member_loads:
            if mload.member_id == mid:
                w_global = np.array([mload.wx, mload.wy, mload.wz])
                break
        R = np.vstack([geom.ex, geom.ey, geom.ez])
        w_local = R @ w_global
        f_equiv_local = _element_equivalent_load_local(geom, w_local)

        f_end_local = geom.k_local @ u_local - f_equiv_local

        u4_z = u_local[[1, 5, 7, 11]]
        u4_y = u_local[[2, 4, 8, 10]]
        f4_z_rigid_for_recovery = np.array([w_local[1] * geom.length / 2.0,
                                              w_local[1] * geom.length ** 2 / 12.0,
                                              w_local[1] * geom.length / 2.0,
                                              -w_local[1] * geom.length ** 2 / 12.0])
        f4_y_rigid_for_recovery = np.array([w_local[2] * geom.length / 2.0,
                                              -w_local[2] * geom.length ** 2 / 12.0,
                                              w_local[2] * geom.length / 2.0,
                                              w_local[2] * geom.length ** 2 / 12.0])
        r_int_z = geom.cond_z.recover_internal_rotations(u4_z, f4_z_rigid_for_recovery)
        r_int_y = geom.cond_y.recover_internal_rotations(u4_y, f4_y_rigid_for_recovery)

        member_forces[mid] = MemberForces(
            member_id=mid,
            P_i=-f_end_local[0], V2_i=-f_end_local[1], V3_i=-f_end_local[2],
            T_i=-f_end_local[3], M2_i=-f_end_local[4], M3_i=-f_end_local[5],
            P_j=f_end_local[6], V2_j=f_end_local[7], V3_j=f_end_local[8],
            T_j=f_end_local[9], M2_j=f_end_local[10], M3_j=f_end_local[11],
            r_int_z=tuple(r_int_z), r_int_y=tuple(r_int_y),
        )

    return AnalysisResult(
        displacements=displacements, reactions=reactions,
        member_forces=member_forces, member_geometry=geoms,
    )
