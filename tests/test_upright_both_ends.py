"""
El chequeo de interacción de un paral debe evaluar los DOS extremos del
elemento (nudo i y nudo j), no sólo el nudo i: un paral no tiene carga
transversal distribuida (el peso propio es axial puro), así que P, M2, M3,
V2 y V3 varían linealmente entre sus dos extremos y el máximo puede caer en
cualquiera de los dos -- con conexiones viga-paral semirrígidas en cada
nivel, es común que el extremo con la conexión (p.ej. la parte superior de
un tramo con base articulada) tenga momento mucho mayor que el otro. Mirar
sólo un extremo (como hacía antes `run_full_check`) podía subestimar la
demanda real y era una causa directa de que el chequeo de la sección 5 de
la memoria no coincidiera con la tabla "Element Forces - Frames" (que sí
reporta ambos extremos).
"""
from vortex.geometry import RackParameters, build_selective_rack
from vortex.geometry.model import MemberKind
from vortex.sections.catalog import default_catalog
from vortex.analysis import PipelineInputs, SeismicInputs, run_full_check
from vortex.loads.combinations import LoadCase
from vortex.design.upright_cfs import check_upright_compression_bending
from vortex.units import kgf_to_kn


def _build():
    catalog = default_catalog()
    params = RackParameters(
        # Base articulada: M2_i=M3_i≈0 en el primer tramo (nudo 0), pero el
        # nudo 1 (conexión semirrígida a las vigas) sí toma momento -- el
        # caso donde el extremo "j" gobierna y "i" solo, por sí mismo, lo
        # habría ocultado.
        n_bays=3, bay_length=2.44, frame_depth=1.06,
        level_heights=[1.20, 1.80, 1.80, 1.80],
        upright_section=catalog["PARAL 122x2.5mm"],
        beam_section=catalog["VIGA CAJA 160x60x1.5mm"],
        brace_section=catalog["DIAGONAL TUBULAR 30x30x2.0mm"],
        base_fixity="pinned",
    )
    model = build_selective_rack(params)
    inputs = PipelineInputs(
        pl_per_level_kn=kgf_to_kn(2400.0), ll_kn_m2=0.0,
        seismic=SeismicInputs(soil_type="D", aa=0.15, av=0.20),
    )
    result = run_full_check(model, inputs)
    return model, result, inputs


def test_base_segment_governing_ratio_considers_both_ends():
    model, result, inputs = _build()
    base_upright = next(
        m for m in model.members_of_kind(MemberKind.UPRIGHT)
        if m.level_index == 0 and m.frame_index == 1 and m.side == "frente"
    )
    combo_gravity = next(c for c in result.combos if c.id == "2" and "granizo" in c.description)
    combo_seismic = next(c for c in result.combos if c.id == "5")

    L = model.member_length(base_upright)
    KLy, KLz = inputs.k_long * L, inputs.k_trans * L

    ratios = []
    for combo, el_pattern in ((combo_gravity, None), (combo_seismic, "EL_X"), (combo_seismic, "EL_Y")):
        f_dl = combo.factors.get(LoadCase.DL, 0.0)
        f_pl = combo.factors.get(LoadCase.PL, 0.0)
        f_ll = combo.factors.get(LoadCase.LL, 0.0)
        f_el = combo.factors.get(LoadCase.EL, 0.0)
        mf_dl = result.patterns["DL"].member_forces[base_upright.id]
        mf_pl = result.patterns["PL"].member_forces[base_upright.id]
        mf_ll = result.patterns["LL"].member_forces[base_upright.id]
        mf_el = result.patterns[el_pattern].member_forces[base_upright.id] if el_pattern else None
        for suffix in ("_i", "_j"):
            P = (f_dl * getattr(mf_dl, "P" + suffix) + f_pl * getattr(mf_pl, "P" + suffix)
                 + f_ll * getattr(mf_ll, "P" + suffix))
            M2 = (f_dl * getattr(mf_dl, "M2" + suffix) + f_pl * getattr(mf_pl, "M2" + suffix)
                  + f_ll * getattr(mf_ll, "M2" + suffix))
            M3 = (f_dl * getattr(mf_dl, "M3" + suffix) + f_pl * getattr(mf_pl, "M3" + suffix)
                  + f_ll * getattr(mf_ll, "M3" + suffix))
            V2 = (f_dl * getattr(mf_dl, "V2" + suffix) + f_pl * getattr(mf_pl, "V2" + suffix)
                  + f_ll * getattr(mf_ll, "V2" + suffix))
            V3 = (f_dl * getattr(mf_dl, "V3" + suffix) + f_pl * getattr(mf_pl, "V3" + suffix)
                  + f_ll * getattr(mf_ll, "V3" + suffix))
            if mf_el is not None:
                P += f_el * getattr(mf_el, "P" + suffix)
                M2 += f_el * getattr(mf_el, "M2" + suffix)
                M3 += f_el * getattr(mf_el, "M3" + suffix)
                V2 += f_el * getattr(mf_el, "V2" + suffix)
                V3 += f_el * getattr(mf_el, "V3" + suffix)
            r = check_upright_compression_bending(
                base_upright.section, combo.id, P=abs(P), M2=abs(M2), M3=abs(M3),
                V2=abs(V2), V3=abs(V3), KLy=KLy, KLz=KLz,
            )
            ratios.append((suffix, r.ratio))

    expected_best = max(ratios, key=lambda t: t[1])
    reported = result.member_rows[base_upright.id]

    assert reported.ratio == expected_best[1]
    # Ambos extremos deben quedar representados entre las opciones evaluadas
    # (no solo "_i"): si el máximo real está en "_j", el reporte debe
    # reflejarlo (regresión directa del bug: antes SIEMPRE se reportaba
    # el resultado de "_i", incluso cuando "_j" era peor).
    suffixes_seen = {s for s, _ in ratios}
    assert suffixes_seen == {"_i", "_j"}
