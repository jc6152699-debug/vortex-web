"""
Pruebas del asesor de IA (Groq), ahora vive dentro de `vortex.gui.app`
(a pedido explícito: toda la configuración y el código de Groq quedan en
ese único archivo, en vez de un paquete `vortex.ai` aparte). El resumen de
resultados se construye sin red, y la llamada a la API de Groq se valida
con `requests.post`/`requests.get` remplazados por un doble de prueba
(sin salir a internet).
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
        gui_app.get_recommendations("resumen de prueba", api_key="")


def test_get_recommendations_success(gui_app, monkeypatch):
    class _FakeResponse:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "  Recomendación de prueba.  "}}]}

    def _fake_post(url, headers=None, json=None, timeout=None):
        assert url == gui_app.GROQ_API_URL
        assert headers["Authorization"] == "Bearer test-key"
        return _FakeResponse()

    monkeypatch.setattr(gui_app.requests, "post", _fake_post)
    text = gui_app.get_recommendations("resumen", api_key="test-key")
    assert text == "Recomendación de prueba."


def test_get_recommendations_http_error(gui_app, monkeypatch):
    class _FakeResponse:
        status_code = 401
        text = "unauthorized"

        def json(self):
            raise AssertionError("no debería llamarse json() en un error HTTP")

    monkeypatch.setattr(gui_app.requests, "post", lambda *a, **k: _FakeResponse())
    with pytest.raises(gui_app.AdvisorError, match="inválida"):
        gui_app.get_recommendations("resumen", api_key="bad-key")


def test_get_recommendations_network_error(gui_app, monkeypatch):
    import requests

    def _raise(*a, **k):
        raise requests.exceptions.ConnectionError("sin red")

    monkeypatch.setattr(gui_app.requests, "post", _raise)
    with pytest.raises(gui_app.AdvisorError, match="red"):
        gui_app.get_recommendations("resumen", api_key="test-key")


def test_get_recommendations_model_not_found_suggests_refresh(gui_app, monkeypatch):
    class _FakeResponse:
        status_code = 404
        text = '{"error": {"code": "model_not_found"}}'

        def json(self):
            return {"error": {"code": "model_not_found", "message": "no existe"}}

    monkeypatch.setattr(gui_app.requests, "post", lambda *a, **k: _FakeResponse())
    with pytest.raises(gui_app.AdvisorError, match="🔄"):
        gui_app.get_recommendations("resumen", api_key="test-key", model="modelo-viejo")


def test_list_available_models_without_key_raises(gui_app):
    with pytest.raises(gui_app.AdvisorError):
        gui_app.list_available_models(api_key="")


def test_list_available_models_success(gui_app, monkeypatch):
    class _FakeResponse:
        status_code = 200

        def json(self):
            return {"data": [{"id": "b-model"}, {"id": "a-model"}]}

    def _fake_get(url, headers=None, timeout=None):
        assert url == gui_app.GROQ_MODELS_URL
        assert headers["Authorization"] == "Bearer test-key"
        return _FakeResponse()

    monkeypatch.setattr(gui_app.requests, "get", _fake_get)
    models = gui_app.list_available_models(api_key="test-key")
    assert models == ["a-model", "b-model"]


def test_list_available_models_http_error(gui_app, monkeypatch):
    class _FakeResponse:
        status_code = 401
        text = "unauthorized"

    monkeypatch.setattr(gui_app.requests, "get", lambda *a, **k: _FakeResponse())
    with pytest.raises(gui_app.AdvisorError, match="inválida"):
        gui_app.list_available_models(api_key="bad-key")
