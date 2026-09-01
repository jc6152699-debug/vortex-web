"""
Sistema de unidades del proyecto.

Unidades internas de cálculo (consistentes, estilo SAP2000 "KN, m, C"):
    Longitud ........ m
    Fuerza .......... kN
    Masa ............ kg
    Momento ......... kN*m
    Esfuerzo ........ kPa  (kN/m2)
    Aceleracion (g) . adimensional (fracción de g)

Todas las funciones de conversión reciben y devuelven `float`. Se exponen
además utilidades para mostrar resultados en las unidades "de obra"
(kgf, mm, kgf/cm2) que es como habitualmente se leen las memorias de
cálculo colombianas, sin que el motor interno deje de trabajar en
unidades consistentes SI.
"""
from __future__ import annotations

G = 9.80665  # m/s2, aceleración de la gravedad estándar

# ---- Fuerza -----------------------------------------------------------
KGF_TO_KN = G / 1000.0          # 1 kgf = 0.00980665 kN
KN_TO_KGF = 1.0 / KGF_TO_KN
LBF_TO_KN = 0.0044482216153
KN_TO_LBF = 1.0 / LBF_TO_KN

# ---- Longitud -----------------------------------------------------------
MM_TO_M = 0.001
M_TO_MM = 1000.0
CM_TO_M = 0.01
M_TO_CM = 100.0
IN_TO_M = 0.0254
FT_TO_M = 0.3048

# ---- Esfuerzo / presión --------------------------------------------------
MPA_TO_KPA = 1000.0
KPA_TO_MPA = 0.001
KGF_CM2_TO_KPA = KGF_TO_KN * 1000.0 / (CM_TO_M ** 2) / 1000.0 * 1000.0  # kgf/cm2 -> kPa
# 1 kgf/cm2 = 98.0665 kPa
KGF_CM2_TO_KPA = 98.0665
KPA_TO_KGF_CM2 = 1.0 / KGF_CM2_TO_KPA
MPA_TO_KGF_CM2 = 1000.0 * KPA_TO_KGF_CM2
KGF_CM2_TO_MPA = 1.0 / MPA_TO_KGF_CM2


def kgf_to_kn(v: float) -> float:
    return v * KGF_TO_KN


def kn_to_kgf(v: float) -> float:
    return v * KN_TO_KGF


def mm_to_m(v: float) -> float:
    return v * MM_TO_M


def m_to_mm(v: float) -> float:
    return v * M_TO_MM


def mpa_to_kpa(v: float) -> float:
    return v * MPA_TO_KPA


def kgf_cm2_to_kpa(v: float) -> float:
    return v * KGF_CM2_TO_KPA


def kpa_to_kgf_cm2(v: float) -> float:
    return v * KPA_TO_KGF_CM2


def kpa_to_mpa(v: float) -> float:
    return v * KPA_TO_MPA
