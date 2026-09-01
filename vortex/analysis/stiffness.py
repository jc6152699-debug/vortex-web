"""
Elemento de pórtico espacial (3D frame element), 6 GDL/nodo, método de
la rigidez directa — formulación estándar de Euler-Bernoulli (sin
deformación por cortante), con soporte de liberaciones/conexiones
semirrígidas en los extremos mediante condensación estática (resorte
rotacional en serie con el elemento, técnica de Guyan/Monforton-Wu).

Orden de grados de libertad por nodo: [ux, uy, uz, rx, ry, rz] (locales).
Orden del vector de 12 GDL del elemento: [nodo i (6), nodo j (6)].

Convención de ejes locales:
    x : del nodo i al nodo j (eje del elemento)
    z : definido a partir del vector de referencia `z_axis_ref` del
        elemento, ez = normalize(ex × ey), ey = normalize(vref × ex)
    y : ex × ez  (completa la triada derecha)

Flexión "My" (alrededor del eje local y) ocurre en el plano local x-z.
Flexión "Mz" (alrededor del eje local z) ocurre en el plano local x-y.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

from ..geometry.model import ConnectionRelease, EndFixity, Member, RackModel


def local_axes(node_i_xyz, node_j_xyz, z_ref) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    p_i = np.asarray(node_i_xyz, dtype=float)
    p_j = np.asarray(node_j_xyz, dtype=float)
    vref = np.asarray(z_ref, dtype=float)

    ex = p_j - p_i
    L = np.linalg.norm(ex)
    if L < 1e-9:
        raise ValueError("Elemento de longitud nula")
    ex = ex / L

    if np.linalg.norm(np.cross(vref, ex)) < 1e-6:
        raise ValueError(
            "El vector de referencia z_axis_ref es paralelo al eje del "
            "elemento; no se puede definir la orientación local."
        )
    ey = np.cross(vref, ex)
    ey = ey / np.linalg.norm(ey)
    ez = np.cross(ex, ey)
    return ex, ey, ez


def transformation_matrix(ex: np.ndarray, ey: np.ndarray, ez: np.ndarray) -> np.ndarray:
    """Matriz de transformación 12x12 (local = T @ global)."""
    R = np.vstack([ex, ey, ez])  # 3x3
    T = np.zeros((12, 12))
    for k in range(4):
        T[3 * k: 3 * k + 3, 3 * k: 3 * k + 3] = R
    return T


def rigid_local_stiffness(E: float, G: float, A: float, Iy: float, Iz: float,
                            J: float, L: float) -> np.ndarray:
    """Matriz de rigidez local 12x12, ambos extremos totalmente rígidos."""
    k = np.zeros((12, 12))

    EAL = E * A / L
    k[0, 0] = k[6, 6] = EAL
    k[0, 6] = k[6, 0] = -EAL

    GJL = G * J / L
    k[3, 3] = k[9, 9] = GJL
    k[3, 9] = k[9, 3] = -GJL

    # Flexión en plano x-y alrededor de z (v, rz) -> índices 1,5,7,11
    EIz = E * Iz
    k[1, 1] = k[7, 7] = 12 * EIz / L ** 3
    k[1, 7] = k[7, 1] = -12 * EIz / L ** 3
    k[1, 5] = k[5, 1] = 6 * EIz / L ** 2
    k[1, 11] = k[11, 1] = 6 * EIz / L ** 2
    k[7, 5] = k[5, 7] = -6 * EIz / L ** 2
    k[7, 11] = k[11, 7] = -6 * EIz / L ** 2
    k[5, 5] = k[11, 11] = 4 * EIz / L
    k[5, 11] = k[11, 5] = 2 * EIz / L

    # Flexión en plano x-z alrededor de y (w, ry) -> índices 2,4,8,10
    EIy = E * Iy
    k[2, 2] = k[8, 8] = 12 * EIy / L ** 3
    k[2, 8] = k[8, 2] = -12 * EIy / L ** 3
    k[2, 4] = k[4, 2] = -6 * EIy / L ** 2
    k[2, 10] = k[10, 2] = -6 * EIy / L ** 2
    k[8, 4] = k[4, 8] = 6 * EIy / L ** 2
    k[8, 10] = k[10, 8] = 6 * EIy / L ** 2
    k[4, 4] = k[10, 10] = 4 * EIy / L
    k[4, 10] = k[10, 4] = 2 * EIy / L

    return k


@dataclass
class Condensation:
    """
    Operador de condensación estática para un bloque de flexión 4x4
    [v1, r1, v2, r2], donde uno o ambos extremos pueden tener un resorte
    rotacional (conexión articulada o semirrígida) en serie con la viga.

    El GDL de salida en cada extremo flexible es la rotación del NODO
    (lado exterior del resorte); el GDL de salida en un extremo rígido es
    directamente la rotación de la viga en ese extremo (son el mismo
    GDL). `k_reduced` ya está en el orden estándar [v1, out1, v2, out2].

    Toda la contabilidad de condensación se guarda en términos del
    sistema aumentado [v1, r1_int, v2, r2_int, (phi1), (phi2)] para poder
    condensar de forma consistente tanto la matriz de rigidez como
    cualquier vector de carga de empotramiento equivalente, y para poder
    recuperar las rotaciones internas reales de la viga después de
    resolver el sistema global (necesario para el cálculo de deflexión).
    """

    k_reduced: np.ndarray                 # 4x4, orden [v1, out1, v2, out2]
    is_identity: bool                        # True si ambos extremos son rígidos
    n_aug: int = 4                             # tamaño del sistema aumentado
    int_idx: Tuple[int, ...] = ()                # índices (aumentados) de r_int a condensar
    out_aug_idx: Tuple[int, int, int, int] = (0, 1, 2, 3)  # posiciones aumentadas de [v1,out1,v2,out2]
    Kii_inv: Optional[np.ndarray] = None
    Kie: Optional[np.ndarray] = None            # (n_int x 4), del sistema ya reducido a [v1,out1,v2,out2]

    def _augment(self, f4: np.ndarray) -> np.ndarray:
        """Expande un vector de carga físico [f_v1,f_r1,f_v2,f_r2] (siempre
        aplicado directamente sobre la viga, nunca sobre el resorte) al
        espacio aumentado, con ceros en los GDL de nodo (phi) añadidos."""
        f_aug = np.zeros(self.n_aug)
        f_aug[0] = f4[0]
        f_aug[1] = f4[1]
        f_aug[2] = f4[2]
        f_aug[3] = f4[3]
        return f_aug

    def condense_load(self, f4: np.ndarray) -> np.ndarray:
        if self.is_identity:
            return f4.copy()
        f_aug = self._augment(f4)
        f_i = f_aug[list(self.int_idx)]
        f_e = f_aug[list(self.out_aug_idx)]
        f_reduced = f_e - self.Kie.T @ (self.Kii_inv @ f_i)
        return f_reduced

    def recover_internal_rotations(self, u_ext4: np.ndarray, f4: np.ndarray) -> np.ndarray:
        """
        Dado el vector externo resuelto [v1, out1, v2, out2] (out=rotación
        de nodo en extremos flexibles) y la carga física original f4,
        devuelve [r1_int, r2_int]: las rotaciones reales de la viga en
        cada extremo (== a las externas si el extremo es rígido).
        """
        if self.is_identity:
            return np.array([u_ext4[1], u_ext4[3]])
        f_aug = self._augment(f4)
        f_i = f_aug[list(self.int_idx)]
        u_i = self.Kii_inv @ (f_i - self.Kie @ u_ext4)
        r_int = [u_ext4[1], u_ext4[3]]
        for pos, idx in enumerate(self.int_idx):
            if idx == 1:
                r_int[0] = u_i[pos]
            elif idx == 3:
                r_int[1] = u_i[pos]
        return np.array(r_int)


def condense_bending_block(
    k4_rigid: np.ndarray,
    release_1: ConnectionRelease,
    release_2: ConnectionRelease,
) -> Condensation:
    """
    Aplica condensación estática al bloque de flexión 4x4 [v1,r1,v2,r2]
    según las condiciones de extremo. RIGID: sin cambios en ese extremo.
    PINNED: resorte de rigidez 0 (momento liberado). SEMIRIGID: resorte
    de rigidez km (kN*m/rad).

    Casos límite verificados en tests/test_stiffness.py: ambos extremos
    rígidos reproduce k4_rigid exactamente; ambos extremos articulados
    produce el bloque nulo (un elemento biarticulado no aporta rigidez a
    flexión, como corresponde a una diagonal/riostra tipo armadura).
    """
    ends = [release_1, release_2]
    flexible = [e.fixity != EndFixity.RIGID for e in ends]

    if not any(flexible):
        return Condensation(k4_rigid.copy(), True)

    n_extra = sum(flexible)
    n = 4 + n_extra
    K = np.zeros((n, n))
    K[:4, :4] = k4_rigid

    extra_of_end = {}
    next_extra = 4
    for end_i, is_flex in enumerate(flexible):
        if not is_flex:
            continue
        r_idx = 1 if end_i == 0 else 3
        ext_i = next_extra
        next_extra += 1
        extra_of_end[end_i] = ext_i
        ks = ends[end_i].km
        K[r_idx, r_idx] += ks
        K[ext_i, ext_i] += ks
        K[r_idx, ext_i] -= ks
        K[ext_i, r_idx] -= ks

    int_idx = tuple(sorted(1 if e == 0 else 3 for e, f in enumerate(flexible) if f))
    out1 = extra_of_end.get(0, 1)
    out2 = extra_of_end.get(1, 3)
    out_aug_idx = (0, out1, 2, out2)

    all_aug = list(range(n))
    ext_aug = [i for i in all_aug if i not in int_idx]

    Kee = K[np.ix_(ext_aug, ext_aug)]
    Kei = K[np.ix_(ext_aug, list(int_idx))]
    Kie = K[np.ix_(list(int_idx), ext_aug)]
    Kii = K[np.ix_(list(int_idx), list(int_idx))]
    Kii_inv = np.linalg.inv(Kii)
    K_reduced_ext_order = Kee - Kei @ Kii_inv @ Kie

    pos_in_ext = {dof: p for p, dof in enumerate(ext_aug)}
    perm = [pos_in_ext[d] for d in out_aug_idx]
    k_reduced = K_reduced_ext_order[np.ix_(perm, perm)]

    # Kie/Kei reordenados para que sus columnas/filas "externas" queden en
    # el orden de salida [v1, out1, v2, out2]
    Kie_out_order = Kie[:, perm]

    return Condensation(
        k_reduced=k_reduced, is_identity=False, n_aug=n,
        int_idx=int_idx, out_aug_idx=out_aug_idx,
        Kii_inv=Kii_inv, Kie=Kie_out_order,
    )
