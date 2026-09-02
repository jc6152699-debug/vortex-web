"""
Pruebas de integración del asesor de IA (Groq) DESDE la GUI. La lógica en
sí (resumen, llamada HTTP, manejo de errores, selección automática de
modelo, resolución de la API key) vive en `vortex.ai.advisor` y se prueba
ahí (ver tests/test_ai_advisor.py) — antes estaba duplicada en
`vortex.gui.app`, con el riesgo de que las dos copias se desincronizaran
(pasó: una tenía el manejo de HTTP 429/model_decommissioned y la otra no).
Este archivo sólo verifica que la GUI reexporta y usa esas piezas
correctamente, sin repetir toda la matriz de casos de error de red/HTTP.
"""
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pyside6 = pytest.importorskip("PySide6")

from vortex.geometry import RackParameters, build_selective_rack
from vortex.sections.catalog import default_catalog
from vortex.analysis import PipelineInputs, SeismicInputs, run_full_check
from vortex.units import kgf_to_kn
from vortex.ai import advisor


@pytest.fixture(scope="module")
def qapp():
    from PySide6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    yield app


@pytest.fixture()
def gui_app(qapp):
    from vortex.gui import app as gui_app_module
    return gui_app_module


def _build():
    catalog = default_catalog()
    params = RackParameters(
        n_bays=2, bay_length=2.44, frame_depth=1.06, level_heights=[1.20, 1.80, 1.80],
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


def test_build_results_summary_contains_key_numbers(gui_app):
    model, result, inputs = _build()
    summary = gui_app.build_results_summary(model, result, inputs, n_worst=5)
    assert "Aa=0.15" in summary
    assert "Elementos verificados" in summary
    assert summary.count("\n") >= 5  # cabecera + hasta 5 filas de elementos críticos


def test_resolve_groq_api_key_delegates_to_advisor(gui_app, monkeypatch):
    """La GUI ya NO tiene su propia constante de API key (era un riesgo:
    editar un archivo versionado para guardar un secreto) — delega
    enteramente en `vortex.ai.advisor.load_local_api_key`."""
    monkeypatch.setattr(advisor, "load_local_api_key", lambda: "from-advisor")
    assert gui_app._resolve_groq_api_key() == "from-advisor"
    assert not hasattr(gui_app, "GROQ_API_KEY")


def test_candidate_models_is_the_shared_advisor_list(gui_app):
    assert gui_app.CANDIDATE_MODELS is advisor.AVAILABLE_MODELS


def test_get_recommendations_auto_is_the_shared_advisor_function(gui_app):
    """`get_recommendations_auto` (probar cada modelo en orden hasta que
    uno responda) es la misma función de `vortex.ai.advisor` — la GUI no
    mantiene su propia copia."""
    assert gui_app.get_recommendations_auto is advisor.get_recommendations_auto


def test_get_recommendations_auto_skips_models_that_dont_exist(gui_app, monkeypatch):
    """El sistema elige el modelo: si el primer candidato no existe,
    prueba el siguiente sin que el usuario tenga que hacer nada. Se
    monkeypatchea `advisor.requests` (donde ocurre la llamada real), no
    `gui_app.requests` (que ya no existe tras la consolidación)."""
    calls = []

    def _fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json["model"])

        class _Resp:
            def __init__(self, ok):
                self.ok = ok
                self.status_code = 200 if ok else 404
                self.text = '' if ok else '{"error": {"code": "model_not_found"}}'

            def json(self):
                if self.ok:
                    return {"choices": [{"message": {"content": "ok"}}]}
                return {"error": {"code": "model_not_found"}}

        return _Resp(ok=(json["model"] == "modelo-bueno"))

    monkeypatch.setattr(advisor.requests, "post", _fake_post)
    text = gui_app.get_recommendations_auto(
        "resumen", api_key="test-key", models=["modelo-malo-1", "modelo-malo-2", "modelo-bueno"],
    )
    assert text == "ok"
    assert calls == ["modelo-malo-1", "modelo-malo-2", "modelo-bueno"]


def test_get_recommendations_auto_raises_when_all_models_fail(gui_app, monkeypatch):
    class _FakeResponse:
        status_code = 404
        text = '{"error": {"code": "model_not_found"}}'

        def json(self):
            return {"error": {"code": "model_not_found"}}

    monkeypatch.setattr(advisor.requests, "post", lambda *a, **k: _FakeResponse())
    with pytest.raises(gui_app.AdvisorError, match="Ningún modelo"):
        gui_app.get_recommendations_auto(
            "resumen", api_key="test-key", models=["modelo-malo-1", "modelo-malo-2"],
        )


def test_get_recommendations_auto_without_api_key_raises(gui_app):
    with pytest.raises(gui_app.AdvisorError):
        gui_app.get_recommendations_auto("resumen", api_key="")
