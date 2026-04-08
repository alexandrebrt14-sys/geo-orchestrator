"""Catalog YAML loader + validator (sprint 5 — 2026-04-08).

Le `catalog/model_catalog.yaml` (single source of truth declarado no README)
e expoe helpers para resto do codigo. Tambem implementa um validator que
compara o catalog com `src/config.LLM_CONFIGS` e levanta `CatalogDriftError`
se houverem inconsistencias — usado pelos testes de Sprint 5 para travar
drift entre o YAML publicavel e o codigo executavel.

A migracao completa para o catalog como SoT runtime fica para um sprint
futuro (mexe em 5 modulos). Por enquanto o loader serve para:

- Auditar drift no CI (test_sprint5)
- Alimentar a pagina publica https://alexandrecaramaschi.com/geo-orchestrator
- Servir como referencia humana versionada
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

import os

# Sprint 7: hot-reload via env var GEO_CATALOG_PATH
CATALOG_PATH: Path = Path(
    os.environ.get(
        "GEO_CATALOG_PATH",
        str(Path(__file__).resolve().parent.parent / "catalog" / "model_catalog.yaml"),
    )
)


class CatalogDriftError(RuntimeError):
    """Levantado quando catalog YAML e config.LLM_CONFIGS divergem."""


def load_catalog(path: Path | None = None) -> dict[str, Any]:
    """Le e parseia o catalog YAML.

    Lazy import de yaml: se PyYAML nao estiver instalado, faz parse manual
    minimo para evitar adicionar dependencia obrigatoria. (O orchestrator
    nao quebra sem PyYAML — apenas o validator desabilita.)
    """
    p = path or CATALOG_PATH
    if not p.exists():
        raise FileNotFoundError(f"Catalog nao encontrado: {p}")
    text = p.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text) or {}
    except ImportError:
        logger.warning("PyYAML ausente — catalog_loader retornando parse vazio.")
        return {}


def get_models_with_aliases(catalog: dict[str, Any]) -> dict[str, dict]:
    """Retorna {config_alias: model_dict} achatado a partir do catalog.

    config_alias e a chave usada em src/config.LLM_CONFIGS (ex.: 'claude',
    'claude_sonnet', 'gpt4o'). Modelos sem alias declarado sao pulados.

    Sprint 7: cada entrada agora tambem inclui `provider` (string) e
    `api_key_env` (herdado do bloco do provider) para suportar a
    construcao de LLMConfig direto do catalog.
    """
    out: dict[str, dict] = {}
    providers = catalog.get("providers", {}) or {}
    for provider_name, pdata in providers.items():
        api_key_env = (pdata or {}).get("api_key_env")
        models = (pdata or {}).get("models", {}) or {}
        for model_id, mdata in models.items():
            alias = (mdata or {}).get("config_alias")
            if not alias:
                continue
            out[alias] = {
                "model_id": model_id,
                "provider": provider_name,
                "api_key_env": api_key_env,
                **mdata,
            }
    return out


def build_llm_configs_from_catalog(
    catalog: dict[str, Any] | None = None,
    strengths_overrides: dict[str, list[str]] | None = None,
    role_overrides: dict[str, str] | None = None,
) -> dict:
    """Sprint 7 (2026-04-08): constroi LLMConfig dict a partir do catalog YAML.

    Migra LLM_CONFIGS de hardcoded em config.py para fonte unica no
    catalog/model_catalog.yaml. Os campos `strengths` e `role` (metadata
    de apresentacao, nao roteamento) ainda vem de overrides estaticos.

    Returns dict[str, LLMConfig] pronto para substituir o LLM_CONFIGS
    hardcoded. Levanta CatalogDriftError se algum campo essencial faltar.

    Lazy import de LLMConfig para evitar circular: catalog_loader e
    importado por config.py.
    """
    cat = catalog if catalog is not None else load_catalog()
    if not cat:
        return {}

    from .config import LLMConfig, Provider  # circular-safe (import dentro func)

    aliased = get_models_with_aliases(cat)
    strengths_overrides = strengths_overrides or {}
    role_overrides = role_overrides or {}

    configs: dict = {}
    for alias, m in aliased.items():
        provider_name = m.get("provider")
        api_key_env = m.get("api_key_env")
        if not provider_name or not api_key_env:
            raise CatalogDriftError(
                f"alias '{alias}': provider/api_key_env ausente no catalog"
            )
        try:
            provider_enum = Provider(provider_name)
        except ValueError:
            raise CatalogDriftError(
                f"alias '{alias}': provider '{provider_name}' nao mapeia "
                f"para Provider enum"
            )

        cost_in_per_1k = float(m.get("input_cost_per_mtok", 0)) / 1000.0
        cost_out_per_1k = float(m.get("output_cost_per_mtok", 0)) / 1000.0
        max_tokens = int(m.get("max_tokens", 4096))
        capabilities = list(m.get("capabilities", []) or [])
        # strengths cai para capabilities se nao houver override
        strengths = strengths_overrides.get(alias, capabilities)
        role = role_overrides.get(alias, m.get("display", alias))

        configs[alias] = LLMConfig(
            name=alias,
            provider=provider_enum,
            model=m["model_id"],
            api_key_env=api_key_env,
            strengths=strengths,
            cost_per_1k_input=cost_in_per_1k,
            cost_per_1k_output=cost_out_per_1k,
            max_tokens=max_tokens,
            role=role,
        )
    return configs


def validate_catalog_vs_config(
    catalog: dict[str, Any] | None = None,
    config_module: Any | None = None,
) -> list[str]:
    """Compara catalog com src/config.LLM_CONFIGS. Retorna lista de erros.

    Lista vazia = consistente. Caso contrario, lista de strings descrevendo
    cada divergencia (model_id, custo, max_tokens).
    """
    cat = catalog if catalog is not None else load_catalog()
    if not cat:
        return ["catalog vazio (PyYAML ausente ou arquivo invalido)"]

    if config_module is None:
        from . import config as config_module  # type: ignore

    aliased = get_models_with_aliases(cat)
    errors: list[str] = []

    cfgs = config_module.LLM_CONFIGS  # type: ignore[attr-defined]
    for alias, cfg in cfgs.items():
        cat_entry = aliased.get(alias)
        if cat_entry is None:
            errors.append(f"alias '{alias}' presente em LLM_CONFIGS mas ausente do catalog")
            continue
        if cat_entry["model_id"] != cfg.model:
            errors.append(
                f"alias '{alias}': model_id catalog='{cat_entry['model_id']}' "
                f"!= config='{cfg.model}'"
            )
        # Custos: catalog em $/Mtok, config em $/1k tok. Convertemos.
        cat_in_per_1k = float(cat_entry.get("input_cost_per_mtok", 0)) / 1000.0
        cat_out_per_1k = float(cat_entry.get("output_cost_per_mtok", 0)) / 1000.0
        if abs(cat_in_per_1k - cfg.cost_per_1k_input) > 1e-6:
            errors.append(
                f"alias '{alias}': input cost catalog={cat_in_per_1k} "
                f"!= config={cfg.cost_per_1k_input}"
            )
        if abs(cat_out_per_1k - cfg.cost_per_1k_output) > 1e-6:
            errors.append(
                f"alias '{alias}': output cost catalog={cat_out_per_1k} "
                f"!= config={cfg.cost_per_1k_output}"
            )

    # Verifica que todos os aliases do catalog tambem existem em LLM_CONFIGS
    for alias in aliased:
        if alias not in cfgs:
            errors.append(
                f"alias '{alias}' presente no catalog mas ausente de LLM_CONFIGS"
            )

    return errors


def assert_catalog_consistent() -> None:
    """Helper para CI/tests: levanta CatalogDriftError se houver drift."""
    errors = validate_catalog_vs_config()
    if errors:
        bullet = "\n  - ".join(errors)
        raise CatalogDriftError(
            f"Catalog YAML divergiu de src/config.LLM_CONFIGS:\n  - {bullet}"
        )
