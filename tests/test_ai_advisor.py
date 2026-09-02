"""
Pruebas del asesor de IA (`vortex.ai.advisor`): el resumen de resultados
se construye sin red, y la llamada a la API de Groq se valida con
`requests.post` remplazado por un doble de prueba (sin salir a internet).
"""
import pytest

from vortex.geometry import RackParameters, build_selective_rack
from vortex.sections.catalog import default_catalog
from vortex.analysis import PipelineInputs, SeismicInputs, run_full_check
from vortex.units import kgf_to_kn
from vortex.ai import advisor
from vortex.ai.advisor import (
    AdvisorError, build_results_summary, get_recommendations, list_available_models,
    load_local_api_key, save_local_api_key,
)


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


def test_build_results_summary_contains_key_numbers():
    model, result, inputs = _build()
    summary = build_results_summary(model, result, inputs, n_worst=5)
    assert "Aa=0.15" in summary
    assert "Elementos verificados" in summary
    assert summary.count("\n") >= 5  # cabecera + hasta 5 filas de elementos críticos


def test_get_recommendations_without_api_key_raises():
    with pytest.raises(AdvisorError):
        get_recommendations("resumen de prueba", api_key="")


def test_get_recommendations_success(monkeypatch):
    class _FakeResponse:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "  Recomendación de prueba.  "}}]}

    def _fake_post(url, headers=None, json=None, timeout=None):
        assert url == advisor.GROQ_API_URL
        assert headers["Authorization"] == "Bearer test-key"
        return _FakeResponse()

    monkeypatch.setattr(advisor.requests, "post", _fake_post)
    text = get_recommendations("resumen", api_key="test-key")
    assert text == "Recomendación de prueba."


def test_get_recommendations_http_error(monkeypatch):
    class _FakeResponse:
        status_code = 401
        text = "unauthorized"

        def json(self):
            raise AssertionError("no debería llamarse json() en un error HTTP")

    monkeypatch.setattr(advisor.requests, "post", lambda *a, **k: _FakeResponse())
    with pytest.raises(AdvisorError, match="inválida"):
        get_recommendations("resumen", api_key="bad-key")


def test_get_recommendations_network_error(monkeypatch):
    import requests

    def _raise(*a, **k):
        raise requests.exceptions.ConnectionError("sin red")

    monkeypatch.setattr(advisor.requests, "post", _raise)
    with pytest.raises(AdvisorError, match="red"):
        get_recommendations("resumen", api_key="test-key")


def test_get_recommendations_model_not_found_suggests_refresh(monkeypatch):
    class _FakeResponse:
        status_code = 404
        text = '{"error": {"code": "model_not_found"}}'

        def json(self):
            return {"error": {"code": "model_not_found", "message": "no existe"}}

    monkeypatch.setattr(advisor.requests, "post", lambda *a, **k: _FakeResponse())
    with pytest.raises(AdvisorError, match="🔄"):
        get_recommendations("resumen", api_key="test-key", model="modelo-viejo")


def test_list_available_models_without_key_raises():
    with pytest.raises(AdvisorError):
        list_available_models(api_key="")


def test_list_available_models_success(monkeypatch):
    class _FakeResponse:
        status_code = 200

        def json(self):
            return {"data": [{"id": "b-model"}, {"id": "a-model"}]}

    def _fake_get(url, headers=None, timeout=None):
        assert url == advisor.GROQ_MODELS_URL
        assert headers["Authorization"] == "Bearer test-key"
        return _FakeResponse()

    monkeypatch.setattr(advisor.requests, "get", _fake_get)
    models = list_available_models(api_key="test-key")
    assert models == ["a-model", "b-model"]


def test_list_available_models_http_error(monkeypatch):
    class _FakeResponse:
        status_code = 401
        text = "unauthorized"

    monkeypatch.setattr(advisor.requests, "get", lambda *a, **k: _FakeResponse())
    with pytest.raises(AdvisorError, match="inválida"):
        list_available_models(api_key="bad-key")


def test_local_api_key_roundtrip(monkeypatch, tmp_path):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(advisor, "_LOCAL_KEY_FILE", str(tmp_path / ".groq_api_key"))
    assert load_local_api_key() == ""
    save_local_api_key("gsk_test_123")
    assert load_local_api_key() == "gsk_test_123"


def test_local_api_key_env_var_takes_precedence(monkeypatch, tmp_path):
    key_file = tmp_path / ".groq_api_key"
    key_file.write_text("from-file\n")
    monkeypatch.setattr(advisor, "_LOCAL_KEY_FILE", str(key_file))
    monkeypatch.setenv("GROQ_API_KEY", "from-env")
    assert load_local_api_key() == "from-env"
