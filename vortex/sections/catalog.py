"""
Cálculo de propiedades geométricas de secciones de pared delgada y
catálogo de secciones típicas de estantería.

IMPORTANTE — alcance y limitaciones:

Las propiedades de flexión/axial/cortante (A, Iy, Iz, Sy, Sz, ry, rz) y la
constante de torsión de Saint-Venant (J) se calculan aquí mediante
integración de segmentos de pared delgada (norma de cálculo estándar para
perfiles abiertos formados en frío) y son confiables para un primer
análisis. Se asume que los ejes y,z suministrados ya son ejes principales
de la sección (válido para los perfiles simétricos típicos de estantería:
canal con labios, sombrero, caja, ángulo).

La constante de alabeo (Cw), la distancia del centro de cortante al
centroide (xo) y el radio de giro polar respecto al centro de cortante (ro)
— necesarios para el pandeo flexo-torsional de perfiles formados en frío
(AISI) — NO se estiman con fórmulas aproximadas aquí, porque un valor
incorrecto de Cw/xo puede subestimar significativamente la resistencia de
un paral. Estos valores DEBEN provenir de la ficha técnica certificada del
fabricante o de un ensayo tipo columna corta (NTC 5689 numeral 9.3). Si no
se suministran (quedan en 0.0), el módulo de diseño (`design.upright_cfs`)
omite el chequeo de pandeo flexo-torsional y lo reporta explícitamente
como "no verificado" en la memoria de cálculo, en lugar de presentar un
resultado numérico no confiable.
"""
from __future__ import annotations

import dataclasses
from typing import List, Sequence, Tuple

from ..geometry.model import Material, Section, SectionKind


def thin_wall_open_section(
    name: str,
    points: Sequence[Tuple[float, float]],
    thickness: float,
    material: Material,
    kind: SectionKind = SectionKind.CFS_UPRIGHT,
    perforation_ratio: float = 0.85,
) -> Section:
    """
    Calcula A, Iy, Iz, Sy, Sz, ry, rz y J (Saint-Venant) de un perfil
    abierto de pared delgada definido por una polilínea de puntos
    (y, z) en metros, recorridos en orden a lo largo de la línea media
    de la pared, con espesor constante `thickness`.

    Se asume que el origen (0,0) de los puntos suministrados coincide con
    el eje de simetría de la sección (y = 0 es eje de simetría, o la
    sección se da ya centrada) — válido para los perfiles típicos de
    estantería (canal con labios, sombrero, ángulo simétrico).
    """
    t = thickness
    segs = list(zip(points[:-1], points[1:]))
    if not segs:
        raise ValueError("Se requieren al menos 2 puntos para definir el perfil")

    A = 0.0
    ybar_num = 0.0
    zbar_num = 0.0
    for (y1, z1), (y2, z2) in segs:
        L = ((y2 - y1) ** 2 + (z2 - z1) ** 2) ** 0.5
        dA = L * t
        A += dA
        ybar_num += dA * (y1 + y2) / 2.0
        zbar_num += dA * (z1 + z2) / 2.0
    ybar = ybar_num / A
    zbar = zbar_num / A

    Iy = 0.0   # integral z_c^2 dA  (flexión alrededor de eje y)
    Iz = 0.0    # integral y_c^2 dA  (flexión alrededor de eje z)
    Iyz = 0.0
    J = 0.0
    for (y1, z1), (y2, z2) in segs:
        yc1, zc1 = y1 - ybar, z1 - zbar
        yc2, zc2 = y2 - ybar, z2 - zbar
        L = ((y2 - y1) ** 2 + (z2 - z1) ** 2) ** 0.5
        Iz += t * L * (yc1 ** 2 + yc1 * yc2 + yc2 ** 2) / 3.0
        Iy += t * L * (zc1 ** 2 + zc1 * zc2 + zc2 ** 2) / 3.0
        Iyz += t * L * (2 * yc1 * zc1 + yc1 * zc2 + yc2 * zc1 + 2 * yc2 * zc2) / 6.0
        J += L * t ** 3 / 3.0

    if abs(Iyz) > 0.05 * max(Iy, Iz, 1e-12):
        raise ValueError(
            f"La sección '{name}' no es simétrica respecto a los ejes y,z dados "
            f"(Iyz={Iyz:.3e} no despreciable frente a Iy,Iz). Este calculador "
            f"asume ejes principales; reoriente los puntos de entrada o use "
            f"una rutina de ejes principales antes de crear la sección."
        )

    ys = [y - ybar for y, _ in points]
    zs = [z - zbar for _, z in points]
    y_max = max(abs(min(ys)), abs(max(ys))) or 1e-9
    z_max = max(abs(min(zs)), abs(max(zs))) or 1e-9

    Sy = Iy / z_max
    Sz = Iz / y_max
    ry = (Iy / A) ** 0.5
    rz = (Iz / A) ** 0.5
    depth = max(zs) - min(zs)
    width = max(ys) - min(ys)

    return Section(
        name=name, kind=kind, material=material,
        A=A, Iy=Iy, Iz=Iz, J=J,
        depth=depth, width=width, thickness=t,
        Sy=Sy, Sz=Sz, ry=ry, rz=rz,
        perforation_ratio=perforation_ratio,
    )


def lipped_channel_upright(
    name: str,
    depth: float,
    flange: float,
    lip: float,
    thickness: float,
    material: Material,
    perforation_ratio: float = 0.85,
    kind: SectionKind = SectionKind.CFS_UPRIGHT,
) -> Section:
    """
    Perfil típico de paral: canal con labios rigidizadores (C con
    retornos), simétrico respecto al eje horizontal medio (eje z=0).
    Todas las dimensiones son a línea media de pared, en metros.

        depth   : altura total del alma (h)
        flange  : ancho de las alas (b), medido desde el alma
        lip     : longitud del labio rigidizador (d)
        thickness: espesor de lámina (t)
    """
    h2 = depth / 2.0
    # Recorrido físico del perfil: punta del labio inferior -> ala inferior
    # -> alma (y=0, eje del alma) -> ala superior -> punta del labio
    # superior. y=0 es el plano del alma; y=flange es el borde de las alas.
    points = [
        (flange, -h2 + lip),   # punta labio inferior
        (flange, -h2),           # esquina ala inferior / alma
        (0.0, -h2),                # alma, borde inferior (y=0, eje del alma)
        (0.0, h2),                   # alma, borde superior
        (flange, h2),                  # esquina ala superior / alma
        (flange, h2 - lip),              # punta labio superior
    ]
    sec = thin_wall_open_section(
        name, points, thickness, material,
        kind=kind, perforation_ratio=perforation_ratio,
    )
    # Segmentos planos para ancho efectivo (NSR-10 F.4.2.2): el alma es un
    # elemento rigidizado por un ala en cada borde longitudinal (k=4,
    # F.4.2.2-1 a -5); las alas y los labios se tratan conservadoramente
    # como elementos NO rigidizados (k=0.43, F.4.2.3), ignorando el aporte
    # rigidizador del labio — una simplificación del lado seguro (subestima
    # el ancho efectivo real de un labio adecuado) que evita depender de la
    # verificación completa de rigidizador de borde de la sección F.4.2.4.
    K_STIFFENED, K_UNSTIFFENED = 4.0, 0.43
    segments = [
        {"w": lip, "t": thickness, "k": K_UNSTIFFENED, "is_web": False},   # labio inferior
        {"w": flange, "t": thickness, "k": K_UNSTIFFENED, "is_web": False},  # ala inferior
        {"w": depth, "t": thickness, "k": K_STIFFENED, "is_web": True},        # alma
        {"w": flange, "t": thickness, "k": K_UNSTIFFENED, "is_web": False},  # ala superior
        {"w": lip, "t": thickness, "k": K_UNSTIFFENED, "is_web": False},   # labio superior
    ]
    return dataclasses.replace(sec, effective_width_segments=segments)


def rectangular_tube_section(
    name: str,
    depth: float,
    width: float,
    thickness: float,
    material: Material,
    kind: SectionKind = SectionKind.HR_UPRIGHT,
) -> Section:
    """
    Sección tubular rectangular (HSS) de pared delgada cerrada — se usa
    tanto para parales laminados en caliente como para vigas tipo caja.
    Fórmulas estándar AISC para tubo rectangular de pared delgada
    (dimensiones exteriores H x B, espesor t).
    """
    H, B, t = depth, width, thickness
    h = H - t
    b = B - t
    A = 2 * t * (h + b)
    Iz = (B * H ** 3 - (B - 2 * t) * (H - 2 * t) ** 3) / 12.0   # flexión alrededor de z (fuerte, si H>B)
    Iy = (H * B ** 3 - (H - 2 * t) * (B - 2 * t) ** 3) / 12.0    # flexión alrededor de y
    Sz = Iz / (H / 2.0)
    Sy = Iy / (B / 2.0)
    ry = (Iy / A) ** 0.5
    rz = (Iz / A) ** 0.5
    # Constante de torsión de Bredt para tubo rectangular de pared delgada
    Ap = h * b
    J = 2 * t * (h * b) ** 2 / (h + b)
    _ = Ap
    # Las dos almas (paredes verticales) de la sección cajón son elementos
    # rigidizados en ambos bordes (k=4, NSR-10 F.4.2.2); las dos alas
    # (paredes horizontales) también, al ser una sección cerrada.
    segments = [
        {"w": H, "t": t, "k": 4.0, "is_web": True},
        {"w": H, "t": t, "k": 4.0, "is_web": True},
        {"w": B, "t": t, "k": 4.0, "is_web": False},
        {"w": B, "t": t, "k": 4.0, "is_web": False},
    ]
    return Section(
        name=name, kind=kind, material=material,
        A=A, Iy=Iy, Iz=Iz, J=J,
        depth=H, width=B, thickness=t,
        Sy=Sy, Sz=Sz, ry=ry, rz=rz,
        effective_width_segments=segments,
    )


def box_beam_section(
    name: str,
    depth: float,
    width: float,
    thickness: float,
    material: Material,
) -> Section:
    """Viga porta-estibas tipo caja (perfil formado en frío, cerrado)."""
    return rectangular_tube_section(
        name, depth, width, thickness, material, kind=SectionKind.BEAM_BOX,
    )


def default_catalog() -> dict:
    """
    Secciones de referencia aproximadas, dimensionalmente consistentes con
    los ejemplos de los anexos ("PARAL 122x2.5mm", "PARAL 120 2.5mm"), para
    permitir un primer análisis sin depender de una ficha de fabricante.

    ADVERTENCIA: estas propiedades son geométricas idealizadas (perfil
    canal con labios, sin considerar las perforaciones de graduación de
    ganchos más que a través de `perforation_ratio`, ni el efecto de
    esquinas redondeadas). Antes de emitir una memoria de cálculo
    definitiva, reemplazar por las propiedades certificadas del
    fabricante (A, Ix, Iy, Cw, xo, ro, Ae a Fy).
    """
    a572 = Material.acero_a572_gr50()
    a36 = Material.acero_a36()

    paral_122x25 = lipped_channel_upright(
        "PARAL 122x2.5mm", depth=0.122, flange=0.070, lip=0.018,
        thickness=0.0025, material=a572,
    )
    paral_120x25 = lipped_channel_upright(
        "PARAL 120x2.5mm", depth=0.120, flange=0.070, lip=0.018,
        thickness=0.0025, material=a572,
    )
    paral_90x20 = lipped_channel_upright(
        "PARAL 90x2.0mm", depth=0.090, flange=0.060, lip=0.015,
        thickness=0.0020, material=a572,
    )
    viga_caja_100x50x20 = box_beam_section(
        "VIGA CAJA 100x50x2.0mm", depth=0.100, width=0.050,
        thickness=0.0020, material=a36,
    )
    viga_caja_120x50x20 = box_beam_section(
        "VIGA CAJA 120x50x2.0mm", depth=0.120, width=0.050,
        thickness=0.0020, material=a36,
    )
    # Sección real usada en el proyecto de referencia ("VIGA 160X60X1.5X244"
    # en la tabla Frame Section Assignments de la memoria de cálculo anexa;
    # 244 = longitud en cm, no es una propiedad de la sección).
    viga_caja_160x60x15 = box_beam_section(
        "VIGA CAJA 160x60x1.5mm", depth=0.160, width=0.060,
        thickness=0.0015, material=a36,
    )
    diagonal_tubular_30x30x2 = rectangular_tube_section(
        "DIAGONAL TUBULAR 30x30x2.0mm", depth=0.030, width=0.030,
        thickness=0.0020, material=a36, kind=SectionKind.BRACE_ANGLE,
    )

    # Secciones reales tomadas del plano de fabricación RIOSTRA_Y_VIGA.pdf
    # (LOGIBOT, "RACK RODILLERA 2 NIVELES - SENCILLO", Autodesk Inventor,
    # lámina 1/5). Cotas leídas del plano acotado (a línea media de pared):
    #   RIOSTRA: canal con labios, alma=40mm, ala=25mm, labio=10mm, t=1.5mm
    #   VIGA: perfil de 130mm de alto x 60mm de ancho, t=2mm, con un detalle
    #     de labio/reborde de 40x20mm que se idealiza aquí como una sección
    #     caja cerrada equivalente (aproximación — el perfil real del plano
    #     parece un canal/caja con reborde de rigidización, no una caja
    #     simple; verificar con la ficha de propiedades del fabricante antes
    #     de un diseño definitivo).
    riostra_25x40x10x15 = lipped_channel_upright(
        "RIOSTRA 25x40x10x1.5mm", depth=0.040, flange=0.025, lip=0.010,
        thickness=0.0015, material=a36, kind=SectionKind.BRACE_ANGLE,
    )
    viga_130x60x2 = box_beam_section(
        "VIGA 130x60x2.0mm", depth=0.130, width=0.060,
        thickness=0.0020, material=a36,
    )

    return {
        s.name: s for s in [
            paral_122x25, paral_120x25, paral_90x20,
            viga_caja_100x50x20, viga_caja_120x50x20, viga_caja_160x60x15,
            viga_130x60x2,
            diagonal_tubular_30x30x2, riostra_25x40x10x15,
        ]
    }
