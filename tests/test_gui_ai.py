"""
Pruebas del asesor de IA (Groq), dentro de `vortex.gui.app` (a pedido
explícito: toda la configuración y el código de Groq quedan en ese único
archivo). El resumen de resultados se construye sin red, y la llamada a
la API de Groq se valida con `requests.post` remplazado por un doble de
prueba (sin salir a internet). El modelo lo elige el sistema
(`get_recommendations_auto` prueba `CANDIDATE_MODELS` en orden) — no hay
selector de modelo ni endpoint de listado expuesto al usuario.
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


def test_resolve_groq_api_key_prefers_env_var(gui_app, monkeypatch):
    monkeypatch.setattr(gui_app, "GROQ_API_KEY", "from-constant")
    monkeypatch.setenv("GROQ_API_KEY", "from-env")
    assert gui_app._resolve_groq_api_key() == "from-env"


def test_resolve_groq_api_key_falls_back_to_constant(gui_app, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(gui_app, "GROQ_API_KEY", "from-constant")
    assert gui_app._resolve_groq_api_key() == "from-constant"


def test_get_recommendations_without_api_key_raises(gui_app):
    with pytest.raises(gui_app.AdvisorError):
        gui_app.get_recommendations("resumen de prueba", api_key="", model="cualquiera")


def test_get_recommendations_success(gui_app, monkeypatch):
    class _FakeResponse:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "  Recomendación de prueba.  "}}]}

    def _fake_post(url, headers=None, json=None, timeout=None):
        assert url == gui_app.GROQ_API_URL
        assert headers["Authorization"] == "Bearer test-key"
        assert json["model"] == "modelo-x"
        return _FakeResponse()

    monkeypatch.setattr(gui_app.requests, "post", _fake_post)
    text = gui_app.get_recommendations("resumen", api_key="test-key", model="modelo-x")
    assert text == "Recomendación de prueba."


def test_get_recommendations_http_error(gui_app, monkeypatch):
    class _FakeResponse:
        status_code = 401
        text = "unauthorized"

        def json(self):
            raise AssertionError("no debería llamarse json() en un error HTTP")

    monkeypatch.setattr(gui_app.requests, "post", lambda *a, **k: _FakeResponse())
    with pytest.raises(gui_app.AdvisorError, match="inválida"):
        gui_app.get_recommendations("resumen", api_key="bad-key", model="modelo-x")


def test_get_recommendations_network_error(gui_app, monkeypatch):
    import requests

    def _raise(*a, **k):
        raise requests.exceptions.ConnectionError("sin red")

    monkeypatch.setattr(gui_app.requests, "post", _raise)
    with pytest.raises(gui_app.AdvisorError, match="red"):
        gui_app.get_recommendations("resumen", api_key="test-key", model="modelo-x")


def test_get_recommendations_model_not_found(gui_app, monkeypatch):
    class _FakeResponse:
        status_code = 404
        text = '{"error": {"code": "model_not_found"}}'

        def json(self):
            return {"error": {"code": "model_not_found", "message": "no existe"}}

    monkeypatch.setattr(gui_app.requests, "post", lambda *a, **k: _FakeResponse())
    with pytest.raises(gui_app.AdvisorError, match="model_not_found"):
        gui_app.get_recommendations("resumen", api_key="test-key", model="modelo-viejo")


def test_get_recommendations_auto_skips_models_that_dont_exist(gui_app, monkeypatch):
    """El sistema elige el modelo: si el primer candidato no existe,
    prueba el siguiente sin que el usuario tenga que hacer nada."""
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

    monkeypatch.setattr(gui_app.requests, "post", _fake_post)
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

    monkeypatch.setattr(gui_app.requests, "post", lambda *a, **k: _FakeResponse())
    with pytest.raises(gui_app.AdvisorError, match="Ningún modelo"):
        gui_app.get_recommendations_auto(
            "resumen", api_key="test-key", models=["modelo-malo-1", "modelo-malo-2"],
        )


def test_get_recommendations_auto_without_api_key_raises(gui_app):
    with pytest.raises(gui_app.AdvisorError):
        gui_app.get_recommendations_auto("resumen", api_key="")
