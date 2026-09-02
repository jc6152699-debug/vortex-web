"""
Asesor de IA (Groq) para los resultados del análisis: arma un resumen
numérico compacto del chequeo estructural (parales/vigas más críticos,
parámetros sísmicos usados, conteo de elementos que no cumplen) y lo envía
a un modelo LLM a través de la API de Groq (compatible con el formato
"Chat Completions" de OpenAI) para obtener recomendaciones de ingeniería
en lenguaje natural.

Esto es una ayuda de lectura rápida de resultados, NO un chequeo
normativo: las recomendaciones del modelo de lenguaje no reemplazan el
criterio del ingeniero calculista responsable ni las verificaciones ya
hechas por `vortex.design`.
"""
from __future__ import annotations

import os
from typing import List, TYPE_CHECKING

import requests

if TYPE_CHECKING:
    from ..analysis.pipeline import PipelineInputs, PipelineResult
    from ..geometry.model import RackModel

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"
DEFAULT_MODEL = "llama-3.3-70b-versatile"
# Lista de referencia (offline) para cuando aún no se ha consultado la API
# de Groq: los modelos realmente disponibles cambian con el tiempo y según
# la cuenta, así que la GUI ofrece un botón "🔄" que llama a
# `list_available_models()` y reemplaza esta lista con la real.
AVAILABLE_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "openai/gpt-oss-120b",
]

# Ruta de un archivo local (NO versionado, ver .gitignore) donde se puede
# guardar la API key de Groq una sola vez para no tener que volver a
# escribirla cada vez que se abre la aplicación. Prioridad de lectura:
# variable de entorno GROQ_API_KEY primero, luego este archivo.
_LOCAL_KEY_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    ".groq_api_key",
)


def load_local_api_key() -> str:
    """
    Devuelve la API key de Groq guardada localmente: primero la variable
    de entorno GROQ_API_KEY, si no, el contenido (una sola línea) del
    archivo `.groq_api_key` en la raíz del proyecto (creado a mano por el
    usuario; nunca se sube a git — ver .gitignore). Devuelve "" si no hay
    ninguna configurada.
    """
    env_key = os.environ.get("GROQ_API_KEY", "").strip()
    if env_key:
        return env_key
    try:
        with open(_LOCAL_KEY_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def save_local_api_key(api_key: str) -> None:
    """Guarda `api_key` en el archivo local `.groq_api_key` (raíz del
    proyecto, ignorado por git) para que quede disponible entre sesiones
    sin tener que volver a escribirla en la GUI."""
    with open(_LOCAL_KEY_FILE, "w", encoding="utf-8") as f:
        f.write(api_key.strip() + "\n")

SYSTEM_PROMPT = (
    "Eres un ingeniero estructural senior, experto en diseño de estanterías "
    "industriales de acero (racks selectivos) según NSR-10 Título F (perfiles "
    "conformados en frío, AISI S100), NTC 5689:2009 y RMI ANSI MH16.1. "
    "Recibes un resumen numérico de un chequeo estructural generado por el "
    "software Vortex (análisis matricial 3D + verificación miembro por "
    "miembro). Da recomendaciones de ingeniería concretas y priorizadas por "
    "severidad, en español, dirigidas a un calculista que va a firmar la "
    "memoria de cálculo. Sé específico: nombra el elemento, la relación "
    "demanda/capacidad, y una acción concreta (cambiar sección, revisar "
    "arriostramiento, verificar un dato de entrada, etc.). No inventes "
    "valores que no estén en el resumen. Si algo luce como un posible error "
    "de datos de entrada (por ejemplo Aa=0 en una ciudad de amenaza sísmica "
    "alta), dilo explícitamente. Responde en viñetas, máximo ~300 palabras."
)


class AdvisorError(RuntimeError):
    """Error al construir el resumen o al consultar la API de Groq."""


def build_results_summary(
    model: "RackModel", result: "PipelineResult", inputs: "PipelineInputs",
    n_worst: int = 10,
) -> str:
    """
    Resumen textual compacto (no exhaustivo) del modelo y de los resultados
    del análisis, pensado para caber en el contexto de un LLM sin exponer
    la tabla completa de elementos.
    """
    rows = sorted(result.member_rows.values(), key=lambda r: -r.ratio)
    n_fail = sum(1 for r in rows if r.ratio > 1.0)
    worst = rows[:n_worst]

    lines = [
        f"Modelo: {len(model.nodes)} nudos, {len(model.members)} elementos.",
        (
            f"Sismo transversal: Aa={inputs.seismic.aa}, Av={inputs.seismic.av}, "
            f"suelo={inputs.seismic.soil_type}, Cs={result.seismic_transversal.cs:.4f}, "
            f"V={result.seismic_transversal.v_base:.2f} kN."
        ),
        (
            f"Sismo longitudinal: Cs={result.seismic_longitudinal.cs:.4f}, "
            f"V={result.seismic_longitudinal.v_base:.2f} kN."
        ),
        (
            f"Carga de producto: {inputs.pl_per_level_kn:.2f} kN/nivel-bahía. "
            f"Carga viva: {inputs.ll_kn_m2:.2f} kN/m²."
        ),
        f"Elementos verificados: {len(rows)}. No cumplen (ratio > 1.0): {n_fail}.",
        f"Los {len(worst)} elementos más críticos (ratio de utilización descendente):",
    ]
    for r in worst:
        lines.append(f"  - {r.label} ({r.kind}), combo {r.combo}: ratio={r.ratio:.2f}, {r.detail}")
    return "\n".join(lines)


def get_recommendations(
    summary: str, api_key: str, model: str = DEFAULT_MODEL, timeout: float = 30.0,
) -> str:
    """Envía `summary` a la API de Groq (Chat Completions) y devuelve el
    texto de la respuesta. Lanza `AdvisorError` con un mensaje claro ante
    cualquier falla (sin API key, red, HTTP, formato de respuesta)."""
    if not api_key:
        raise AdvisorError(
            "No se configuró una API key de Groq. Consigue una gratis en "
            "https://console.groq.com/keys y pégala en el campo correspondiente "
            "(o defínela como variable de entorno GROQ_API_KEY)."
        )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": summary},
        ],
        "temperature": 0.2,
        "max_tokens": 900,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        resp = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=timeout)
    except requests.exceptions.RequestException as exc:
        raise AdvisorError(f"Error de red al contactar Groq: {exc}") from exc

    if resp.status_code == 401:
        raise AdvisorError("API key de Groq inválida o expirada (HTTP 401).")
    if resp.status_code == 429:
        raise AdvisorError(
            "Límite de tasa de la API de Groq alcanzado (HTTP 429). Intente de nuevo en unos segundos."
        )
    if resp.status_code == 404:
        code = _error_code(resp)
        if code == "model_not_found":
            raise AdvisorError(
                f"El modelo '{model}' no existe o tu cuenta de Groq no tiene acceso a él "
                f"(HTTP 404). Los modelos disponibles cambian con el tiempo — presiona el "
                f"botón '🔄' junto al selector de modelo para listar los que sí puede usar "
                f"tu cuenta, y elige uno de ahí."
            )
        raise AdvisorError(f"Groq respondió con error HTTP 404: {resp.text[:500]}")
    if resp.status_code >= 400:
        raise AdvisorError(f"Groq respondió con error HTTP {resp.status_code}: {resp.text[:500]}")

    try:
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, ValueError) as exc:
        raise AdvisorError(f"Respuesta inesperada de Groq: {exc}") from exc


def _error_code(resp: "requests.Response") -> str:
    try:
        return str(resp.json().get("error", {}).get("code", ""))
    except (ValueError, AttributeError):
        return ""


def list_available_models(api_key: str, timeout: float = 15.0) -> List[str]:
    """
    Consulta la lista real de modelos disponibles para esta API key
    (GET /openai/v1/models de Groq) — evita depender de una lista de
    nombres fija en el código, que queda desactualizada cuando Groq agrega,
    renombra o retira modelos. Lanza `AdvisorError` con el mismo criterio
    que `get_recommendations`.
    """
    if not api_key:
        raise AdvisorError(
            "No se configuró una API key de Groq. Consigue una gratis en "
            "https://console.groq.com/keys."
        )
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        resp = requests.get(GROQ_MODELS_URL, headers=headers, timeout=timeout)
    except requests.exceptions.RequestException as exc:
        raise AdvisorError(f"Error de red al contactar Groq: {exc}") from exc

    if resp.status_code == 401:
        raise AdvisorError("API key de Groq inválida o expirada (HTTP 401).")
    if resp.status_code >= 400:
        raise AdvisorError(f"Groq respondió con error HTTP {resp.status_code}: {resp.text[:500]}")

    try:
        data = resp.json()
        ids = sorted(m["id"] for m in data["data"])
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        raise AdvisorError(f"Respuesta inesperada de Groq: {exc}") from exc
    if not ids:
        raise AdvisorError("Groq no devolvió ningún modelo disponible para esta API key.")
    return ids
