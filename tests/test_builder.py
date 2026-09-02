"""
Etiquetas únicas de nudos y elementos: "frente" y "fondo" son las dos
caras de un mismo marco (Y=0 y Y=profundidad de marco) y deben quedar
identificables por separado en toda la memoria de cálculo. Antes, la letra
de lado en la etiqueta se tomaba con `side[0].upper()`, y "frente" y
"fondo" empiezan por la misma letra en español ("F"), así que el paral/
viga del frente y el del fondo de cada marco terminaban con la MISMA
etiqueta (p.ej. dos elementos físicos distintos llamados ambos "PARAL
M0-F N0-N1") -- exactamente el síntoma reportado: nombres de viga
repetidos en la memoria sin forma de saber a cuál elemento físico
correspondía cada fila.
"""
from vortex.geometry import RackParameters, build_selective_rack
from vortex.sections.catalog import default_catalog


def _build_model():
    catalog = default_catalog()
    params = RackParameters(
        n_bays=2, bay_length=2.44, frame_depth=1.06, level_heights=[1.20, 1.80, 1.80],
        upright_section=catalog["PARAL 122x2.5mm"],
        beam_section=catalog["VIGA CAJA 160x60x1.5mm"],
        brace_section=catalog["DIAGONAL TUBULAR 30x30x2.0mm"],
        base_fixity="pinned",
    )
    return build_selective_rack(params)


def test_frente_and_fondo_use_different_side_codes():
    from vortex.geometry.builder import SIDE_CODE
    assert SIDE_CODE["frente"] != SIDE_CODE["fondo"]


def test_node_labels_are_unique():
    model = _build_model()
    labels = [n.label for n in model.nodes.values()]
    assert len(labels) == len(set(labels)), "hay nudos con la misma etiqueta"


def test_member_labels_are_unique_within_each_kind():
    model = _build_model()
    from vortex.geometry.model import MemberKind
    for kind in MemberKind:
        labels = [m.label for m in model.members.values() if m.kind == kind]
        assert len(labels) == len(set(labels)), f"hay elementos {kind} con la misma etiqueta"


def test_frente_and_fondo_upright_at_same_position_have_different_labels():
    model = _build_model()
    from vortex.geometry.model import MemberKind
    frente = next(
        m for m in model.members_of_kind(MemberKind.UPRIGHT)
        if m.frame_index == 0 and m.level_index == 0 and m.side == "frente"
    )
    fondo = next(
        m for m in model.members_of_kind(MemberKind.UPRIGHT)
        if m.frame_index == 0 and m.level_index == 0 and m.side == "fondo"
    )
    assert frente.label != fondo.label
