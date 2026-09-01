"""
Modelo geométrico y de datos de una estantería industrial de acero
(estantería selectiva porta-estibas), en coordenadas globales:

    X : dirección del corredor / dirección de las vigas ("down-aisle")
    Y : dirección transversal al corredor / dirección de los marcos ("cross-aisle")
    Z : vertical

Un "marco" (frame, en el sentido RMI/NTC 5689) es el conjunto formado por
dos parales (uno en Y=0, "frente", y otro en Y=profundidad, "fondo") unidos
por diagonales de arriostramiento en el plano Y-Z: es la estructura que
resiste las cargas transversales (dirección Y), típicamente arriostrada
(R=4 según NTC 5689 numeral 2.7.3).

Las vigas porta-estibas corren en dirección X conectando parales de marcos
consecutivos, al frente y al fondo, en cada nivel de carga. La dirección X
es un pórtico no arriostrado (momento, con conexiones semirrígidas
viga-paral) — R=6 según NTC 5689 numeral 2.7.3.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Optional, Tuple


# --------------------------------------------------------------------------
# Materiales y secciones
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Material:
    """Propiedades del material (unidades SI consistentes: kPa, kN/m3)."""

    name: str
    E: float          # módulo de elasticidad, kPa
    G: float           # módulo de cortante, kPa
    Fy: float           # esfuerzo de fluencia, kPa
    density: float = 76.97  # peso específico del acero, kN/m3 (~7850 kg/m3 * g)

    @staticmethod
    def acero_a572_gr50() -> "Material":
        # Fy = 345 MPa (50 ksi), E = 200000 MPa, G = 77000 MPa aprox.
        return Material("ASTM A572 Gr50", E=200_000_000.0, G=77_000_000.0, Fy=345_000.0)

    @staticmethod
    def acero_a36() -> "Material":
        return Material("ASTM A36", E=200_000_000.0, G=77_000_000.0, Fy=250_000.0)


class SectionKind(Enum):
    CFS_UPRIGHT = auto()      # paral de acero formado en frío, perforado
    HR_UPRIGHT = auto()       # paral de acero laminado en caliente
    BEAM_BOX = auto()          # viga porta-estibas tipo caja (box beam)
    BRACE_ANGLE = auto()       # diagonal / riostra (ángulo o tubular)
    GENERIC = auto()


@dataclass(frozen=True)
class Section:
    """
    Propiedades geométricas de la sección transversal, en ejes principales
    locales del elemento (y = eje débil, z = eje fuerte, salvo que se indique
    lo contrario en el catálogo). Unidades SI consistentes (m, m2, m4).
    """

    name: str
    kind: SectionKind
    material: Material

    A: float             # área bruta, m2
    Iy: float             # inercia respecto al eje local y (flexión en plano x-z), m4
    Iz: float             # inercia respecto al eje local z (flexión en plano x-y), m4
    J: float               # constante torsional St. Venant, m4
    depth: float             # altura/profundidad de la sección, m
    width: float              # ancho de la sección, m
    thickness: float           # espesor de pared/lámina, m

    # Módulos de sección para chequeo de flexión (fibra extrema)
    Sy: float = 0.0
    Sz: float = 0.0

    # Radios de giro (para esbeltez KL/r)
    ry: float = 0.0
    rz: float = 0.0

    # --- Propiedades adicionales para perfiles formados en frío (AISI) -----
    Cw: float = 0.0        # constante de alabeo, m6
    xo: float = 0.0          # distancia del centroide al centro de cortante, m
    ro: float = 0.0           # radio de giro polar respecto al centro de cortante, m
    Fy_override: Optional[float] = None  # Fy propio del perfil si difiere del material

    # Segmentos planos de pared delgada (ancho w, espesor t, coeficiente de
    # pandeo de placa k) para el cálculo del ancho efectivo Ae(f) según
    # NSR-10 Título F.4.2.2 (k=4: elemento rigidizado por almas en ambos
    # bordes, p.ej. el alma; k=0.43: elemento no rigidizado, p.ej. alas y
    # labios — ver `design.upright_cfs.effective_area_at_stress`). Cada
    # entrada: {"w": float, "t": float, "k": float, "is_web": bool}.
    effective_width_segments: Optional[list] = None

    # Área neta efectiva para compresión a Fy (AISI, ancho efectivo) — si se
    # conoce de ensayo o cálculo previo se puede fijar directamente aquí;
    # en caso contrario `design.upright_cfs` la estima.
    Ae_known: Optional[float] = None

    # Fracción de área bruta que queda tras la perforación típica de
    # graduación de ganchos (para estimar Anet de compresión simplificada).
    perforation_ratio: float = 0.85

    @property
    def Fy(self) -> float:
        return self.Fy_override if self.Fy_override is not None else self.material.Fy


class EndFixity(Enum):
    RIGID = auto()       # conexión rígida (momento totalmente transmitido)
    PINNED = auto()        # articulación (momento liberado)
    SEMIRIGID = auto()      # conexión semirrígida (resorte rotacional, km)


@dataclass
class ConnectionRelease:
    """
    Condición de extremo de un elemento para flexión alrededor de un eje
    local dado. `km` (kN*m/rad) sólo aplica si fixity == SEMIRIGID y se
    obtiene del ensayo tipo cantiléver (NTC 5689 numeral 9.4.1) o de un
    valor por defecto conservador del catálogo de conexiones.
    """

    fixity: EndFixity = EndFixity.RIGID
    km: float = 0.0

    @staticmethod
    def rigid() -> "ConnectionRelease":
        return ConnectionRelease(EndFixity.RIGID, 0.0)

    @staticmethod
    def pinned() -> "ConnectionRelease":
        return ConnectionRelease(EndFixity.PINNED, 0.0)

    @staticmethod
    def semirigid(km: float) -> "ConnectionRelease":
        return ConnectionRelease(EndFixity.SEMIRIGID, km)


class MemberKind(Enum):
    UPRIGHT = auto()     # paral
    BEAM = auto()          # viga porta-estibas
    BRACE = auto()          # diagonal de arriostramiento
    BASE = auto()            # elemento ficticio de apoyo/placa base (opcional)


@dataclass
class Node:
    id: int
    x: float
    y: float
    z: float
    # Restricciones de apoyo: True = restringido. Orden: ux,uy,uz,rx,ry,rz
    restraints: Tuple[bool, bool, bool, bool, bool, bool] = (
        False, False, False, False, False, False,
    )
    # Rigidez de resorte para grados de libertad NO restringidos que se
    # quieran modelar como apoyo elástico (p.ej. placa base semirrígida).
    # kN/m para traslaciones, kN*m/rad para rotaciones. 0.0 = sin resorte.
    springs: Tuple[float, float, float, float, float, float] = (
        0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
    )
    label: str = ""

    @property
    def xyz(self) -> Tuple[float, float, float]:
        return (self.x, self.y, self.z)


@dataclass
class Member:
    id: int
    node_i: int
    node_j: int
    section: Section
    kind: MemberKind
    # Vector de referencia (no paralelo al eje del elemento) que define la
    # orientación del eje local z (fuerte) en el espacio.
    z_axis_ref: Tuple[float, float, float] = (0.0, 0.0, 1.0)
    # Liberaciones/rigidez de conexión en cada extremo, para flexión
    # alrededor de los ejes locales y (My) y z (Mz). El caso típico de
    # conexión viga-paral semirrígida (NTC 5689 numeral 7.1) libera/flexibiliza
    # Mz (flexión en el plano del pórtico no arriostrado, dirección X).
    release_i_My: ConnectionRelease = field(default_factory=ConnectionRelease.rigid)
    release_i_Mz: ConnectionRelease = field(default_factory=ConnectionRelease.rigid)
    release_j_My: ConnectionRelease = field(default_factory=ConnectionRelease.rigid)
    release_j_Mz: ConnectionRelease = field(default_factory=ConnectionRelease.rigid)
    label: str = ""
    # Metadatos de ubicación dentro de la estantería, útiles para reportes
    # y para la GUI (a qué marco/bahía/nivel pertenece).
    frame_index: Optional[int] = None   # índice del marco (0..n_bays)
    bay_index: Optional[int] = None       # índice de la bahía (0..n_bays-1)
    level_index: Optional[int] = None      # índice del nivel (0=piso)
    side: Optional[str] = None               # "frente" | "fondo" | None


@dataclass
class RackModel:
    """Contenedor del modelo completo: nodos, elementos y metadatos."""

    nodes: Dict[int, Node] = field(default_factory=dict)
    members: Dict[int, Member] = field(default_factory=dict)

    # Metadatos geométricos (llenados por el builder paramétrico)
    n_bays: int = 0
    n_levels: int = 0
    bay_length: float = 0.0        # m, longitud de viga (dirección X)
    frame_depth: float = 0.0         # m, profundidad de marco (dirección Y)
    level_heights: List[float] = field(default_factory=list)  # m, entre niveles
    level_elevations: List[float] = field(default_factory=list)  # m, desde piso

    def add_node(self, node: Node) -> Node:
        self.nodes[node.id] = node
        return node

    def add_member(self, member: Member) -> Member:
        self.members[member.id] = member
        return member

    def next_node_id(self) -> int:
        return (max(self.nodes.keys()) + 1) if self.nodes else 1

    def next_member_id(self) -> int:
        return (max(self.members.keys()) + 1) if self.members else 1

    def members_of_kind(self, kind: MemberKind) -> List[Member]:
        return [m for m in self.members.values() if m.kind == kind]

    def member_length(self, member: Member) -> float:
        ni, nj = self.nodes[member.node_i], self.nodes[member.node_j]
        return (
            (nj.x - ni.x) ** 2 + (nj.y - ni.y) ** 2 + (nj.z - ni.z) ** 2
        ) ** 0.5

    def bounding_box(self) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
        xs = [n.x for n in self.nodes.values()]
        ys = [n.y for n in self.nodes.values()]
        zs = [n.z for n in self.nodes.values()]
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))
