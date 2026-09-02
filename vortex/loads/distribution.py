"""
Distribución de cargas sobre el modelo — punto ÚNICO donde se decide qué
carga (DL, PL, LL) recibe cada elemento, en función de su posición dentro
de la estantería (marco/bahía/nivel/lado).

Por qué existe este módulo
---------------------------
Antes de este módulo, `analysis.pipeline` calculaba el reparto de cargas
(DL por elemento, PL y LL por viga) DOS VECES de forma independiente —
una vez en `run_full_check` y otra en `element_forces_table` — con el
riesgo de que ambas copias se desincronizaran. `build_load_distribution`
es ahora la única función que arma ese reparto; el pipeline sólo la
consume.

Qué contiene `LoadDistribution`
--------------------------------
- Los totales y valores por metro (`dl_total_kn`, `w_pl_beam`, ...) que ya
  existían, sin cambiar ningún valor ni fórmula.
- `dl_loads` / `pl_loads` / `ll_loads`: listas de `DistributedLoad`
  (member_id, wz) — el pipeline las envuelve en `analysis.solve.MemberLoad`
  justo antes de correr `analyze` (mismo valor que antes; sólo se movió
  el `import` para no acoplar `loads` con `analysis`).
- `beam_rows`: **una fila por viga** con su ubicación (marco, bahía,
  nivel, lado) y su carga (`w_dl`, `w_pl`, `w_ll`, `w_total`, kN/m). Esta
  es la pieza nueva: permite VER en el código, con una sola llamada, cómo
  quedó repartida la carga en toda la estantería (por bahía y nivel), en
  vez de tener que rastrear la fórmula por el pipeline. Reproduce
  exactamente la cuadrícula bahía x nivel de un diagrama de cargas como
  el usado para verificación visual (PL por bahía-nivel, w por viga).

Trayectoria de carga (bahía -> viga -> paral) documentada en
`loads.dead_live.beam_udl_from_product_load`; este módulo sólo organiza
el resultado, no cambia el cálculo.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..geometry.model import MemberKind, RackModel
from .dead_live import (
    dead_load_uprights,
    beam_udl_from_product_load,
    beam_udl_from_live_load,
)


@dataclass
class DistributedLoad:
    """Carga uniformemente distribuida (kN/m) sobre UN elemento, en la
    componente vertical global (wz). Forma mínima, independiente de
    `analysis.solve.MemberLoad` a propósito: `loads` no debe depender de
    `analysis` (evita import circular loads <-> analysis). El pipeline la
    envuelve en un `MemberLoad` real justo antes de correr el análisis —
    ver `analysis.pipeline.run_full_check`."""

    member_id: int
    wz: float


@dataclass
class BeamLoadRow:
    """Carga distribuida sobre UNA viga porta-estibas, con su ubicación
    dentro de la estantería. `w_total_kn_m` es lo que efectivamente se
    aplica al modelo (DL propio de la viga + PL + LL)."""

    member_id: int
    label: str
    frame_index: Optional[int]
    bay_index: Optional[int]
    level_index: Optional[int]
    side: Optional[str]        # "frente" | "fondo"
    w_dl_kn_m: float             # peso propio de la viga (self-weight/L)
    w_pl_kn_m: float               # carga de producto (igual para toda viga)
    w_ll_kn_m: float                 # carga viva (igual para toda viga)

    @property
    def w_total_kn_m(self) -> float:
        return self.w_dl_kn_m + self.w_pl_kn_m + self.w_ll_kn_m


@dataclass
class LoadDistribution:
    """Reparto de cargas ya resuelto para TODO el modelo — DL, PL y LL —
    listo para (a) alimentar `analysis.solve.analyze` y (b) inspeccionar
    o graficar cómo quedó repartida la carga por bahía y nivel."""

    # --- Peso propio (DL) ---------------------------------------------
    dl_by_member: Dict[int, float]      # kN, peso total de cada elemento
    dl_total_kn: float
    dl_per_level_kn: float
    dl_loads: List[DistributedLoad]

    # --- Carga de producto (PL) -----------------------------------------
    w_pl_beam_kn_m: float                 # kN/m, igual para toda viga porta-estibas
    pl_total_kn: float                      # kN, por nivel (todas las bahías)
    pl_loads: List[DistributedLoad]

    # --- Carga viva (LL) ------------------------------------------------
    w_ll_beam_kn_m: float
    ll_total_kn: float                       # kN, por nivel (todas las bahías)
    ll_loads: List[DistributedLoad]

    # --- Vista por viga, para inspección/gráficas ------------------------
    beam_rows: List[BeamLoadRow] = field(default_factory=list)

    def beam_grid(self) -> Dict[int, Dict[int, float]]:
        """`{level_index: {bay_index: w_total_kn_m}}` — la misma
        cuadrícula bahía x nivel que un diagrama de cargas de producto
        (una viga "frente"/"fondo" por bahía-nivel; se reporta el lado
        "frente" ya que ambos llevan la misma carga)."""
        grid: Dict[int, Dict[int, float]] = {}
        for row in self.beam_rows:
            if row.side not in (None, "frente") or row.level_index is None or row.bay_index is None:
                continue
            grid.setdefault(row.level_index, {})[row.bay_index] = row.w_total_kn_m
        return grid

    def print_beam_grid(self) -> None:
        """Imprime la cuadrícula bahía x nivel de carga por viga (kN/m),
        para verificar visualmente el reparto directamente en consola —
        útil para comparar contra un diagrama de cargas de producto."""
        grid = self.beam_grid()
        for level in sorted(grid.keys(), reverse=True):
            row = grid[level]
            cells = "  ".join(f"{row[b]:6.2f}" for b in sorted(row.keys()))
            print(f"Nivel {level}: {cells}")


def build_load_distribution(
    model: RackModel, pl_per_level_kn: float, ll_kn_m2: float,
) -> LoadDistribution:
    """
    Arma el reparto de cargas DL/PL/LL sobre `model` — ÚNICA función que
    debe usarse para esto (ver docstring del módulo). `pl_per_level_kn` y
    `ll_kn_m2` vienen de `PipelineInputs`.
    """
    # --- DL: peso propio de cada elemento, aplicado a lo largo de su eje.
    dl_by_member = dead_load_uprights(model)
    dl_total = sum(dl_by_member.values())
    dl_per_level = dl_total / model.n_levels if model.n_levels else 0.0
    dl_loads = [
        DistributedLoad(member_id=mid, wz=-(w / model.member_length(model.members[mid])))
        for mid, w in dl_by_member.items() if model.member_length(model.members[mid]) > 1e-9
    ]

    # --- PL: carga de producto, igual sobre cada viga porta-estibas
    # (numeral 1.5.2 — ver `dead_live.beam_udl_from_product_load` para el
    # detalle bahía -> viga -> paral).
    w_pl_beam = beam_udl_from_product_load(pl_per_level_kn, model.bay_length)
    pl_loads = [
        DistributedLoad(member_id=m.id, wz=-w_pl_beam)
        for m in model.members_of_kind(MemberKind.BEAM)
    ]
    pl_total = pl_per_level_kn * model.n_bays   # kN, por nivel (todas las bahías)

    # --- LL: carga viva de plataforma/pasillo (numeral 2.1), mismo
    # criterio de reparto tributario que PL.
    w_ll_beam = beam_udl_from_live_load(ll_kn_m2, model.frame_depth)
    ll_loads = [
        DistributedLoad(member_id=m.id, wz=-w_ll_beam)
        for m in model.members_of_kind(MemberKind.BEAM)
    ]
    ll_total = ll_kn_m2 * model.bay_length * model.frame_depth * model.n_bays

    # --- Vista por viga (para inspección y diagramas) --------------------
    beam_rows = [
        BeamLoadRow(
            member_id=m.id, label=m.label,
            frame_index=m.frame_index, bay_index=m.bay_index,
            level_index=m.level_index, side=m.side,
            w_dl_kn_m=dl_by_member.get(m.id, 0.0) / model.bay_length,
            w_pl_kn_m=w_pl_beam, w_ll_kn_m=w_ll_beam,
        )
        for m in model.members_of_kind(MemberKind.BEAM)
    ]

    return LoadDistribution(
        dl_by_member=dl_by_member, dl_total_kn=dl_total, dl_per_level_kn=dl_per_level,
        dl_loads=dl_loads,
        w_pl_beam_kn_m=w_pl_beam, pl_total_kn=pl_total, pl_loads=pl_loads,
        w_ll_beam_kn_m=w_ll_beam, ll_total_kn=ll_total, ll_loads=ll_loads,
        beam_rows=beam_rows,
    )
