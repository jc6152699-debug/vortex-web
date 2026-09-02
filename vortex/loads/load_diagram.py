"""
Diagrama de "CARGAS DE PRODUCTO": una elevación esquemática de la
estantería (bahías x niveles) con flechas y el valor w (kN/m) de cada
viga porta-estibas — el mismo tipo de gráfico usado para verificar
visualmente el reparto de carga contra la memoria de cálculo.

Se construye directamente sobre `loads.distribution.LoadDistribution`
(`beam_grid()`), así que SIEMPRE dibuja el reparto exacto que usa el
motor de análisis (`analysis.pipeline.run_full_check`) — no es un dibujo
aparte que se pueda desincronizar del cálculo real.
"""
from __future__ import annotations

from typing import Optional

from ..geometry.model import RackModel
from .distribution import LoadDistribution


def plot_product_load_diagram(
    model: RackModel,
    dist: LoadDistribution,
    path: Optional[str] = None,
    dpi: int = 150,
):
    """
    Dibuja el diagrama de cargas de producto (título, marco bahía x
    nivel, flechas y etiqueta w[kN/m] sobre cada viga) a partir de
    `dist.beam_grid()`. Si se da `path`, además lo guarda como imagen
    (extensión según `path`, p.ej. ".png"). Devuelve la figura de
    matplotlib (por si se quiere seguir editando o mostrar en la GUI
    antes de guardar).
    """
    import matplotlib
    matplotlib.use("Agg")   # sin display — sólo generar la imagen
    import matplotlib.pyplot as plt

    grid = dist.beam_grid()   # {level_index: {bay_index: w_kn_m}}
    n_bays = model.n_bays
    n_levels = model.n_levels
    pl_per_level_kn = dist.pl_total_kn / n_bays if n_bays else 0.0

    fig_w = max(8.0, n_bays * 1.7)
    fig_h = max(6.0, n_levels * 1.4)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    # Título en dos líneas: con pocas bahías (figura angosta) una sola
    # línea con todo el detalle no cabe en el ancho de la figura y
    # `savefig` la recorta por los bordes en vez de ajustarla.
    ax.set_title(
        "Vortex — CARGAS DE PRODUCTO\n"
        f"(PL={pl_per_level_kn:.2f} kN/nivel-bahía, w={dist.w_pl_beam_kn_m:.2f} kN/m por viga, "
        f"{n_bays} bahías x {n_levels} niveles)",
        fontsize=11,
    )

    # Marco: parales (verticales) y vigas (horizontales) del perímetro
    # de cada bahía/nivel.
    for bay in range(n_bays + 1):
        ax.plot([bay, bay], [0, n_levels], color="tab:blue", linewidth=1.2)
    for level in range(n_levels + 1):
        ax.plot([0, n_bays], [level, level], color="tab:blue", linewidth=1.2)

    n_arrows = 5   # flechas por viga, sólo decorativo (estilo carga distribuida)
    for level in range(1, n_levels + 1):
        bays = grid.get(level, {})
        for bay in range(n_bays):
            if bay not in bays:
                continue
            # Etiqueta = sólo la componente PL (mismo criterio que el
            # título "CARGAS DE PRODUCTO"): el peso propio de la viga
            # (w_dl) es un patrón de carga aparte (DL), no se mezcla aquí.
            w = dist.w_pl_beam_kn_m
            xs = [bay + (i + 0.5) / n_arrows for i in range(n_arrows)]
            for x in xs:
                ax.annotate(
                    "", xy=(x, level - 0.12), xytext=(x, level),
                    arrowprops=dict(arrowstyle="-|>", color="tab:blue", lw=1.0),
                )
            ax.text(bay + 0.5, level + 0.06, f"{w:.2f}", ha="center", va="bottom",
                     color="darkgreen", fontsize=9)

    ax.set_xlim(-0.2, n_bays + 0.2)
    ax.set_ylim(-0.2, n_levels + 0.6)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.tight_layout()

    if path:
        fig.savefig(path, dpi=dpi)
    return fig
