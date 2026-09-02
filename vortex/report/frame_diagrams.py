"""
Diagramas de elevación estilo SAP2000 (marco bahía x nivel) y reporte de
especificaciones de sección de parales — "Member Check" — a partir de los
resultados ya calculados por `analysis.pipeline` (NUNCA se recalcula nada
aquí, sólo se dibuja/formatea lo que el motor de análisis ya produjo, para
no desincronizar el dibujo del cálculo real):

  - `plot_seismic_load_diagram`   -> elevación con flechas de fuerza sísmica
    por nivel (estilo "CARGAS DE SISMO" de SAP2000: flechas horizontales al
    borde izquierdo del marco, una por nivel, con su magnitud en kN).
  - `plot_frame_force_diagram`     -> diagrama de M3 (momento), P (axial) o
    V2 (cortante) sobre la elevación del marco, para una combinación de
    carga dada (`analysis.pipeline.element_forces_table`).
  - `seismic_levels_table`               -> tabla NIVEL/FX[kN] (igual al
    anexo "1.1.4 Cargas de sismo" del proyecto).
  - `upright_section_report`              -> texto tipo "Member Check"
    (NSR-10 F.4 / AISI S100, ASD) por cada sección de paral usada en el
    modelo, con el elemento gobernante de esa sección.

Todos los `plot_*` devuelven una figura de matplotlib (para incrustar en
la GUI o guardar a archivo con `fig.savefig(path)`).
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from ..geometry.model import MemberKind, RackModel


def _frente_members(model: RackModel, kind: MemberKind):
    """Elementos de un solo lado ('frente') de un tipo dado, para dibujar
    una única elevación 2D sin líneas duplicadas por el lado 'fondo'
    (idéntico desde el punto de vista de este dibujo esquemático)."""
    members = [m for m in model.members_of_kind(kind) if getattr(m, "side", None) == "frente"]
    if not members:
        # Geometrías sin metadato 'side' (p.ej. modelos armados a mano):
        # se usan todos los elementos del tipo pedido.
        members = model.members_of_kind(kind)
    return members


def _frame_bbox(model: RackModel):
    """Caja (xmin, xmax, zmin, zmax), en las coordenadas REALES (m) del
    modelo, de los elementos que se dibujan en la elevación 2D (parales +
    vigas del lado 'frente') — nunca se asume espaciado unitario por
    bahía/nivel, porque `bay_length`/`level_heights` son valores reales
    en metros, no índices."""
    xs, zs = [], []
    for kind in (MemberKind.UPRIGHT, MemberKind.BEAM):
        for m in _frente_members(model, kind):
            for nid in (m.node_i, m.node_j):
                n = model.nodes[nid]
                xs.append(n.x); zs.append(n.z)
    if not xs:
        xs, zs = [0.0, 1.0], [0.0, 1.0]
    return min(xs), max(xs), min(zs), max(zs)


def _setup_frame_axes(model: RackModel, ax, title: str) -> None:
    ax.set_title(title, fontsize=11)
    for m in _frente_members(model, MemberKind.UPRIGHT):
        ni, nj = model.nodes[m.node_i], model.nodes[m.node_j]
        ax.plot([ni.x, nj.x], [ni.z, nj.z], color="tab:blue", linewidth=1.2, zorder=2)
    for m in _frente_members(model, MemberKind.BEAM):
        ni, nj = model.nodes[m.node_i], model.nodes[m.node_j]
        ax.plot([ni.x, nj.x], [ni.z, nj.z], color="tab:blue", linewidth=1.2, zorder=2)
    # Apoyos (triángulos), en la base (z=0).
    xs_base = sorted({model.nodes[m.node_i].x for m in _frente_members(model, MemberKind.UPRIGHT)
                       if abs(model.nodes[m.node_i].z) < 1e-6})
    for x in xs_base:
        ax.plot(x, 0.0, marker="^", markersize=10, linestyle="none",
                 markerfacecolor="none", markeredgecolor="tab:green",
                 markeredgewidth=1.3, zorder=3)


def seismic_levels_table(seismic_result, model: RackModel) -> List[dict]:
    """Tabla NIVEL / altura / FX[kN], igual al anexo '1.1.4 Cargas de
    sismo' del proyecto (`SeismicResult.fx_by_level`, ya calculado por
    `loads.seismic.compute_seismic`)."""
    rows = []
    for lv in sorted(seismic_result.fx_by_level.keys()):
        rows.append({
            "nivel": lv,
            "elevacion_m": model.level_elevations[lv] if lv < len(model.level_elevations) else None,
            "fx_kn": seismic_result.fx_by_level[lv],
        })
    return rows


def plot_seismic_load_diagram(
    model: RackModel,
    seismic_result,
    path: Optional[str] = None,
    dpi: int = 150,
    direction_label: str = "",
):
    """Elevación del marco con una flecha horizontal por nivel, escalada
    y etiquetada con la fuerza sísmica Fx de ese nivel (kN) — mismo
    estilo que el plot 'CARGAS DE SISMO' de SAP2000 (flechas al borde
    izquierdo, aplicadas a todo el nivel/diafragma)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    xmin, xmax, zmin, zmax = _frame_bbox(model)
    width, height = max(xmax - xmin, 1e-6), max(zmax - zmin, 1e-6)
    arrow_zone = 0.16 * width + 0.6   # espacio reservado a la izquierda para las flechas, en m
    fig_w = max(8.0, (width + arrow_zone) * 0.9)
    fig_h = max(6.0, height * 0.9)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))

    subtitle = f" ({direction_label})" if direction_label else ""
    _setup_frame_axes(model, ax, f"Vortex — CARGAS DE SISMO{subtitle}")

    fx_by_level = seismic_result.fx_by_level
    max_fx = max((abs(v) for v in fx_by_level.values()), default=0.0) or 1.0
    x0 = xmin
    arrow_len = 0.6 * arrow_zone  # longitud máxima de flecha, en m

    for lv, fx in sorted(fx_by_level.items()):
        z = model.level_elevations[lv] if lv < len(model.level_elevations) else None
        if z is None:
            continue
        length = arrow_len * max(abs(fx) / max_fx, 0.12)
        ax.annotate(
            "", xy=(x0 + length, z), xytext=(x0, z),
            arrowprops=dict(arrowstyle="-|>", color="tab:green", lw=1.6),
            zorder=4,
        )
        ax.text(x0 - 0.05 * arrow_zone, z + 0.02 * height, f"{fx:.3f}", ha="right", va="bottom",
                 color="darkgreen", fontsize=9, fontweight="bold")

    margin = 0.06 * max(width, height)
    ax.set_xlim(xmin - arrow_zone, xmax + margin)
    ax.set_ylim(zmin - margin, zmax + margin)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.tight_layout()
    if path:
        fig.savefig(path, dpi=dpi)
    return fig


_QUANTITY_META = {
    "P": dict(title="DIAGRAMA DE FUERZA AXIAL", color="tab:red", unit="kN",
              axis="perp_signed_columns_only"),
    "M3": dict(title="DIAGRAMA DE MOMENTOS", color="tab:red", unit="kN·m",
               axis="perp_signed"),
    "V2": dict(title="DIAGRAMA DE FUERZA CORTANTE", color="tab:red", unit="kN",
               axis="perp_signed"),
}


def plot_frame_force_diagram(
    model: RackModel,
    rows,  # List[analysis.pipeline.ElementForceRow], una sola combinación/patrón
    quantity: str,
    combo_label: str = "",
    path: Optional[str] = None,
    dpi: int = 150,
):
    """
    Dibuja el diagrama de `quantity` ("P", "M3" o "V2") sobre la
    elevación del marco, con el mismo criterio visual de SAP2000: por
    cada elemento, un relleno perpendicular al eje del elemento cuyo
    ancho en cada estación es proporcional al valor de `quantity` en esa
    estación (ya calculado por `analysis.pipeline.element_forces_table`,
    sea cual sea la combinación con la que se llamó esa función).

    Para "P" sólo se rellenan los parales (fuerza axial de vigas es ~0 en
    las combinaciones de gravedad/sismo de este proyecto, igual que en el
    anexo de referencia). Para "M3"/"V2" se rellenan parales y vigas.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    meta = _QUANTITY_META[quantity]
    xmin, xmax, zmin, zmax = _frame_bbox(model)
    width, height = max(xmax - xmin, 1e-6), max(zmax - zmin, 1e-6)
    fig_w = max(8.0, width * 0.9)
    fig_h = max(6.0, height * 0.9)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))

    title = f"Vortex — {meta['title']}"
    if combo_label:
        title += f"\n{combo_label}"
    _setup_frame_axes(model, ax, title)

    by_member: Dict[int, List] = {}
    for r in rows:
        by_member.setdefault(r.frame, []).append(r)
    for lst in by_member.values():
        lst.sort(key=lambda r: r.station_m)

    values_all = [getattr(r, quantity) for r in rows]
    max_val = max((abs(v) for v in values_all), default=0.0) or 1.0
    # Desplazamiento perpendicular máximo del relleno, proporcional al
    # tamaño típico de panel (bahía x nivel) — para que el diagrama se
    # vea proporcionado sin importar la escala real del modelo (m).
    panel_ref = min(
        model.bay_length or width, (height / max(model.n_levels, 1)) or height,
    )
    max_offset = 0.32 * panel_ref

    frente_uprights = {m.id for m in _frente_members(model, MemberKind.UPRIGHT)}
    frente_beams = {m.id for m in _frente_members(model, MemberKind.BEAM)}

    for member_id, lst in by_member.items():
        is_upright = member_id in frente_uprights
        is_beam = member_id in frente_beams
        if not (is_upright or is_beam):
            continue
        if quantity == "P" and not is_upright:
            continue  # fuerza axial de vigas no se dibuja (≈0, no aporta lectura)

        member = model.members[member_id]
        ni, nj = model.nodes[member.node_i], model.nodes[member.node_j]
        dx, dz = nj.x - ni.x, nj.z - ni.z
        L = (dx ** 2 + dz ** 2) ** 0.5
        if L < 1e-9:
            continue
        ux, uz = dx / L, dz / L         # vector unitario a lo largo del elemento
        # normal perpendicular en el plano X-Z (rotación 90°)
        px, pz = -uz, ux

        xs_axis, zs_axis = [], []
        xs_off, zs_off = [], []
        for r in lst:
            t = r.station_m / L if L > 1e-9 else 0.0
            x = ni.x + dx * t
            z = ni.z + dz * t
            val = getattr(r, quantity)
            off = max_offset * (val / max_val)
            xs_axis.append(x); zs_axis.append(z)
            xs_off.append(x + px * off); zs_off.append(z + pz * off)

        poly_x = xs_axis + xs_off[::-1]
        poly_z = zs_axis + zs_off[::-1]
        ax.fill(poly_x, poly_z, color=meta["color"], alpha=0.55, linewidth=0.6,
                 edgecolor=meta["color"], zorder=1)

        # Etiqueta del valor máximo absoluto de este elemento.
        i_peak = max(range(len(lst)), key=lambda k: abs(getattr(lst[k], quantity)))
        peak = getattr(lst[i_peak], quantity)
        if abs(peak) > 1e-6:
            ax.text(xs_off[i_peak], zs_off[i_peak], f"{peak:.2f}",
                     fontsize=6.5, color="black", ha="center", va="center", zorder=5)

    margin = max(max_offset * 1.3, 0.06 * max(width, height))
    ax.set_xlim(xmin - margin, xmax + margin)
    ax.set_ylim(zmin - margin, zmax + margin)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.tight_layout()
    if path:
        fig.savefig(path, dpi=dpi)
    return fig


def upright_section_report(model: RackModel, result, inputs=None) -> str:
    """
    Reporte de especificaciones por sección de paral, estilo "Member
    Check" (NSR-10 Título F.4 / AISI S100, método ASD): agrupa los
    parales del modelo por nombre de sección y, para cada grupo, imprime
    el elemento gobernante (mayor ratio) con sus parámetros de diseño,
    cargas actuantes/admisibles y propiedades efectivas — TODO tomado de
    `UprightCheckResult` (`design.upright_cfs`), ya calculado por
    `analysis.pipeline.run_full_check` (no se recalcula nada aquí).
    """
    from ..geometry.model import MemberKind

    groups: Dict[str, list] = {}
    for member in model.members_of_kind(MemberKind.UPRIGHT):
        row = result.member_rows.get(member.id)
        if row is None or row.upright_check is None:
            continue
        groups.setdefault(member.section.name, []).append((member, row))

    if not groups:
        return "No hay parales verificados en el modelo actual (ejecute \"Analizar y verificar\")."

    lines: List[str] = []
    lines.append("=" * 78)
    lines.append("ESPECIFICACIÓN DE SECCIÓN DE PARALES — Verificación ASD")
    lines.append("NSR-10 Título F, Capítulo F.4 (AISI S100) · NTC 5689 numeral 1.4")
    lines.append("=" * 78)

    for section_name, members in groups.items():
        member, row = max(members, key=lambda mr: mr[1].ratio)
        c = row.upright_check
        sec = member.section
        mat = sec.material
        L = model.member_length(member)

        lines.append("")
        lines.append("-" * 78)
        lines.append(f"Sección: {section_name}   ·   {len(members)} elemento(s) en el modelo")
        lines.append(f"Material: {mat.name} · Fy = {sec.Fy / 1000:.1f} MPa · E = {mat.E / 1000:.0f} MPa")
        lines.append(
            f"Elemento gobernante: {member.label or member.id} "
            f"(nivel {member.level_index}, marco {member.frame_index}) · "
            f"combinación {row.combo}"
        )
        k_long = getattr(inputs, "k_long", None) if inputs is not None else None
        k_trans = getattr(inputs, "k_trans", None) if inputs is not None else None
        kly_txt = f"{k_long * L:.3f} m" if k_long is not None else "—"
        klz_txt = f"{k_trans * L:.3f} m" if k_trans is not None else "—"

        lines.append("")
        lines.append("Parámetros de diseño:")
        lines.append(f"  L    = {L:.3f} m         KLy (no arriostrada) = {kly_txt}"
                      f"     KLz (arriostrada) = {klz_txt}")
        lines.append(f"  ry   = {sec.ry * 100:.3f} cm     rz   = {sec.rz * 100:.3f} cm")
        lines.append(f"  A    = {sec.A * 1e4:.3f} cm²    Sy   = {sec.Sy * 1e6:.2f} cm³    "
                      f"Sz = {sec.Sz * 1e6:.2f} cm³")
        lines.append("")
        lines.append("Cargas actuantes vs. admisibles (combinación gobernante):")
        lines.append(f"  {'':12}{'P[kN]':>10}{'M2[kN·m]':>12}{'M3[kN·m]':>12}"
                      f"{'V2[kN]':>10}{'V3[kN]':>10}")
        lines.append(f"  {'Actuante':12}{c.P:10.2f}{c.M2:12.3f}{c.M3:12.3f}"
                      f"{c.V2:10.2f}{c.V3:10.2f}")
        lines.append(f"  {'Admisible':12}{c.Pa:10.2f}{c.Ma2:12.3f}{c.Ma3:12.3f}"
                      f"{c.Va:10.2f}{c.Va:10.2f}")
        lines.append("")
        lines.append(
            f"Ratios: axial = {c.ratio_axial:.3f}  ·  interacción = {c.ratio_interaction:.3f}"
            f"  ·  cortante = {max(c.ratio_v2, c.ratio_v3):.3f}  ->  "
            f"RATIO GOBERNANTE = {c.ratio:.3f} ({c.governs}) "
            f"{'✓ CUMPLE' if c.ok else '✗ NO CUMPLE'}"
        )
        if c.notes:
            lines.append("Notas:")
            for n in c.notes:
                lines.append(f"  • {n}")

    lines.append("")
    lines.append("=" * 78)
    return "\n".join(lines)
