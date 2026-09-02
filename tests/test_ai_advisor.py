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
    load_local_api_key,
)


def _isolate_local_key_sources(monkeypatch, tmp_path):
    """Aísla load_local_api_key() del estado real del repo (variable de
    entorno del proceso, vortex/ai/local_config.py real, .groq_api_key
    real) apuntando ambas rutas a un directorio temporal vacío."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(advisor, "_LOCAL_CONFIG_FILE", str(tmp_path / "local_config.py"))
    monkeypatch.setattr(advisor, "_LOCAL_KEY_FILE", str(tmp_path / ".groq_api_key"))


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


def test_load_local_api_key_none_configured_returns_empty(monkeypatch, tmp_path):
    _isolate_local_key_sources(monkeypatch, tmp_path)
    assert load_local_api_key() == ""


def test_load_local_api_key_reads_local_config_py(monkeypatch, tmp_path):
    _isolate_local_key_sources(monkeypatch, tmp_path)
    (tmp_path / "local_config.py").write_text('GROQ_API_KEY = "gsk_from_config"\n')
    assert load_local_api_key() == "gsk_from_config"


def test_load_local_api_key_falls_back_to_text_file(monkeypatch, tmp_path):
    _isolate_local_key_sources(monkeypatch, tmp_path)
    (tmp_path / ".groq_api_key").write_text("gsk_from_file\n")
    assert load_local_api_key() == "gsk_from_file"


def test_load_local_api_key_local_config_beats_text_file(monkeypatch, tmp_path):
    _isolate_local_key_sources(monkeypatch, tmp_path)
    (tmp_path / ".groq_api_key").write_text("gsk_from_file\n")
    (tmp_path / "local_config.py").write_text('GROQ_API_KEY = "gsk_from_config"\n')
    assert load_local_api_key() == "gsk_from_config"


def test_load_local_api_key_env_var_beats_everything(monkeypatch, tmp_path):
    _isolate_local_key_sources(monkeypatch, tmp_path)
    (tmp_path / ".groq_api_key").write_text("gsk_from_file\n")
    (tmp_path / "local_config.py").write_text('GROQ_API_KEY = "gsk_from_config"\n')
    monkeypatch.setenv("GROQ_API_KEY", "gsk_from_env")
    assert load_local_api_key() == "gsk_from_env"
