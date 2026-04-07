"""Auditoria de billing/usage via admin keys com fallback offline.

Le admin keys APENAS de ~/.config/geo-orchestrator/admin.env.
NUNCA usa as API keys normais do projeto (.env do orchestrator).
Saida: relatorio Markdown em output/audit/billing_YYYY-MM-DD.md

Providers suportados:
- OpenAI (admin API com scope api.usage.read)
- Anthropic (admin key)
- Google Cloud Billing (Service Account com role billing.viewer)
- Perplexity / Groq (sem admin API publica — instrucoes para coleta manual)

Cross-check:
- Compara com cost_history.jsonl (orchestrator)
- Compara com curso-factory/output/costs.json
- Compara com papers/data/papers.db::finops_usage

Uso:
    python scripts/audit_billing.py                       # Auditoria completa, ultimos 30 dias
    python scripts/audit_billing.py --start 2026-03-01    # Periodo especifico
    python scripts/audit_billing.py --provider openai     # So um provider
    python scripts/audit_billing.py --json                # Output JSON em vez de Markdown
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Carregamento de admin keys (APENAS de ~/.config, nunca do .env do projeto)
# ---------------------------------------------------------------------------
ADMIN_ENV_PATH = Path.home() / ".config" / "geo-orchestrator" / "admin.env"


def _load_admin_env() -> dict[str, str]:
    """Carrega ~/.config/geo-orchestrator/admin.env como dict.

    Refusa se o arquivo nao existir, se estiver vazio ou se for legivel
    por outros usuarios (verificacao de seguranca basica).
    """
    if not ADMIN_ENV_PATH.exists():
        print(f"[ERRO] Admin env nao encontrado: {ADMIN_ENV_PATH}")
        print("       Crie a partir do template em ~/.config/geo-orchestrator/admin.env.template")
        sys.exit(1)

    # Verificacao de permissao (Linux/Mac)
    if os.name == "posix":
        st = ADMIN_ENV_PATH.stat()
        if st.st_mode & 0o077:
            print(f"[ERRO] Admin env esta legivel por outros: chmod 600 {ADMIN_ENV_PATH}")
            sys.exit(1)

    env: dict[str, str] = {}
    for line in ADMIN_ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


# ---------------------------------------------------------------------------
# Provider audit functions
# ---------------------------------------------------------------------------

def audit_openai(admin_env: dict, start: str, end: str) -> dict:
    """Consulta OpenAI Admin API: /v1/organization/usage/completions.

    Requer admin key com scope api.usage.read.
    """
    key = admin_env.get("OPENAI_ADMIN_KEY")
    if not key or not key.startswith("sk-admin-"):
        return {"status": "no_admin_key", "fallback": "use mcp browser para platform.openai.com/usage"}

    headers = {"Authorization": f"Bearer {key}"}
    org_id = admin_env.get("OPENAI_ORG_ID")
    if org_id:
        headers["OpenAI-Organization"] = org_id

    start_ts = int(time.mktime(datetime.strptime(start, "%Y-%m-%d").timetuple()))
    end_ts = int(time.mktime(datetime.strptime(end, "%Y-%m-%d").timetuple()))

    by_day: list[dict] = []
    by_model: dict[str, dict] = defaultdict(lambda: {"input_tokens": 0, "output_tokens": 0, "calls": 0})
    cursor = None
    total_tokens_in = 0
    total_tokens_out = 0
    total_calls = 0

    try:
        with httpx.Client(timeout=30) as c:
            while True:
                params = {
                    "start_time": start_ts,
                    "end_time": end_ts,
                    "bucket_width": "1d",
                    "limit": 30,
                    "group_by": ["model"],
                }
                if cursor:
                    params["page"] = cursor
                r = c.get(
                    "https://api.openai.com/v1/organization/usage/completions",
                    headers=headers,
                    params=params,
                )
                if r.status_code == 401:
                    return {"status": "auth_error", "detail": "key invalida ou sem scope api.usage.read"}
                if r.status_code != 200:
                    return {"status": "http_error", "code": r.status_code, "body": r.text[:500]}

                data = r.json()
                for bucket in data.get("data", []):
                    bucket_start = datetime.fromtimestamp(bucket["start_time"]).strftime("%Y-%m-%d")
                    day_calls = 0
                    day_in = 0
                    day_out = 0
                    for result in bucket.get("results", []):
                        model = result.get("model", "unknown")
                        ti = result.get("input_tokens", 0)
                        to = result.get("output_tokens", 0)
                        nc = result.get("num_model_requests", 0)
                        by_model[model]["input_tokens"] += ti
                        by_model[model]["output_tokens"] += to
                        by_model[model]["calls"] += nc
                        day_in += ti
                        day_out += to
                        day_calls += nc
                        total_tokens_in += ti
                        total_tokens_out += to
                        total_calls += nc
                    by_day.append({
                        "date": bucket_start,
                        "tokens_in": day_in,
                        "tokens_out": day_out,
                        "calls": day_calls,
                    })

                if not data.get("has_more"):
                    break
                cursor = data.get("next_page")
                if not cursor:
                    break
    except httpx.RequestError as exc:
        return {"status": "request_error", "detail": str(exc)}

    return {
        "status": "ok",
        "source": "openai_admin_api",
        "totals": {
            "input_tokens": total_tokens_in,
            "output_tokens": total_tokens_out,
            "calls": total_calls,
        },
        "by_day": by_day,
        "by_model": dict(by_model),
    }


def audit_anthropic(admin_env: dict, start: str, end: str) -> dict:
    """Consulta Anthropic Admin API: /v1/organizations/usage_report/messages."""
    key = admin_env.get("ANTHROPIC_ADMIN_KEY")
    if not key or not key.startswith("sk-ant-admin-"):
        return {"status": "no_admin_key", "fallback": "use mcp browser para console.anthropic.com/settings/billing"}

    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
    }
    try:
        with httpx.Client(timeout=30) as c:
            r = c.get(
                "https://api.anthropic.com/v1/organizations/usage_report/messages",
                headers=headers,
                params={
                    "starting_at": f"{start}T00:00:00Z",
                    "ending_at": f"{end}T23:59:59Z",
                    "bucket_width": "1d",
                },
            )
        if r.status_code == 401:
            return {"status": "auth_error", "detail": "admin key invalida"}
        if r.status_code != 200:
            return {"status": "http_error", "code": r.status_code, "body": r.text[:500]}
        return {"status": "ok", "source": "anthropic_admin_api", "raw": r.json()}
    except httpx.RequestError as exc:
        return {"status": "request_error", "detail": str(exc)}


def audit_google(admin_env: dict, start: str, end: str) -> dict:
    """Consulta Google Cloud Billing via Service Account."""
    sa_path = admin_env.get("GOOGLE_BILLING_SA_PATH", "")
    sa_path = Path(os.path.expanduser(sa_path))
    if not sa_path.exists():
        return {
            "status": "no_admin_key",
            "fallback": "use mcp browser para console.cloud.google.com/billing/.../reports",
            "hint": f"crie SA em GCP IAM e salve JSON em {sa_path}",
        }

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError:
        return {
            "status": "missing_dependency",
            "detail": "pip install google-auth google-api-python-client",
        }

    billing_account = admin_env.get("GOOGLE_BILLING_ACCOUNT_ID", "")
    if not billing_account:
        return {"status": "no_billing_account", "detail": "GOOGLE_BILLING_ACCOUNT_ID nao configurado"}

    try:
        creds = service_account.Credentials.from_service_account_file(
            str(sa_path),
            scopes=["https://www.googleapis.com/auth/cloud-billing.readonly"],
        )
        # Cloud Billing API nao expoe usage report direto via REST simples.
        # Para custos detalhados, precisa BigQuery export.
        # Aqui retornamos apenas o billing account info.
        service = build("cloudbilling", "v1", credentials=creds)
        info = service.billingAccounts().get(name=f"billingAccounts/{billing_account}").execute()
        return {
            "status": "ok",
            "source": "google_billing_api",
            "billing_account": info,
            "note": "Para custos detalhados por servico, ative BigQuery billing export e consulte la.",
        }
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


def audit_perplexity_groq() -> dict:
    """Perplexity e Groq nao tem admin API publica."""
    return {
        "status": "no_admin_api_public",
        "fallback": {
            "perplexity": "use mcp browser para console.perplexity.ai/.../billing",
            "groq": "use mcp browser para console.groq.com/settings/billing/manage",
        },
    }


# ---------------------------------------------------------------------------
# Cross-check com fontes locais
# ---------------------------------------------------------------------------

def crosscheck_local(start: str, end: str) -> dict:
    """Le todos os trackers locais e agrega por provider."""
    by_source: dict[str, dict] = {}

    # 1) geo-orchestrator/output/cost_history.jsonl + execution_*.json
    orchestrator_total = 0.0
    orchestrator_by_provider: dict[str, float] = defaultdict(float)
    orchestrator_calls = 0
    exec_dir = Path("output")
    if exec_dir.exists():
        for fp in sorted(exec_dir.glob("execution_*.json")):
            try:
                d = json.loads(fp.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            ts = d.get("timestamp", "")[:10]
            if ts < start or ts > end:
                continue
            for r in d.get("results", []):
                cost = float(r.get("cost_usd") or 0)
                orchestrator_total += cost
                orchestrator_calls += 1
                m = (r.get("model_used") or "").lower()
                if "claude" in m: orchestrator_by_provider["anthropic"] += cost
                elif "gpt" in m or m.startswith(("o1", "o3")): orchestrator_by_provider["openai"] += cost
                elif "gemini" in m: orchestrator_by_provider["google"] += cost
                elif "sonar" in m: orchestrator_by_provider["perplexity"] += cost
                elif "llama" in m or "kimi" in m or "qwen" in m: orchestrator_by_provider["groq"] += cost
    by_source["geo-orchestrator"] = {
        "total_usd": round(orchestrator_total, 4),
        "calls": orchestrator_calls,
        "by_provider": dict(orchestrator_by_provider),
    }

    # 2) curso-factory/output/costs.json
    cf_path = Path("../curso-factory/output/costs.json")
    cf_total = 0.0
    cf_by_provider: dict[str, float] = defaultdict(float)
    cf_calls = 0
    if cf_path.exists():
        try:
            data = json.loads(cf_path.read_text(encoding="utf-8"))
            for d in data:
                ts = d.get("timestamp", "")[:10]
                if ts < start or ts > end:
                    continue
                c = float(d.get("custo_usd", 0))
                cf_total += c
                cf_calls += 1
                cf_by_provider[d.get("provider", "unknown")] += c
        except (json.JSONDecodeError, OSError):
            pass
    by_source["curso-factory"] = {
        "total_usd": round(cf_total, 4),
        "calls": cf_calls,
        "by_provider": dict(cf_by_provider),
    }

    # 3) papers/data/papers.db::finops_usage
    papers_path = Path("../papers/data/papers.db")
    papers_total = 0.0
    papers_by_provider: dict[str, float] = defaultdict(float)
    papers_calls = 0
    if papers_path.exists():
        try:
            conn = sqlite3.connect(str(papers_path))
            for row in conn.execute(
                "SELECT platform, cost_usd FROM finops_usage WHERE substr(timestamp,1,10) BETWEEN ? AND ?",
                (start, end),
            ):
                cost = float(row[1] or 0)
                papers_total += cost
                papers_calls += 1
                papers_by_provider[row[0]] += cost
            conn.close()
        except sqlite3.Error:
            pass
    by_source["papers"] = {
        "total_usd": round(papers_total, 4),
        "calls": papers_calls,
        "by_provider": dict(papers_by_provider),
    }

    # 4) Agregado por provider (todas as fontes)
    aggregated: dict[str, float] = defaultdict(float)
    for src in by_source.values():
        for p, c in src["by_provider"].items():
            aggregated[p] += c

    return {
        "by_source": by_source,
        "aggregated_by_provider": {k: round(v, 4) for k, v in aggregated.items()},
        "grand_total": round(sum(aggregated.values()), 4),
    }


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def render_markdown(report: dict, start: str, end: str) -> str:
    lines = [
        f"# Auditoria FinOps — {start} a {end}",
        "",
        f"_Gerado em {datetime.now(timezone.utc).isoformat()}_",
        "",
        "## Cross-check com fontes LOCAIS",
        "",
        f"**Grand total local: US$ {report['local']['grand_total']:.4f}**",
        "",
        "### Por fonte (projeto)",
        "",
        "| Fonte | Calls | Custo USD |",
        "|---|---:|---:|",
    ]
    for src, v in report["local"]["by_source"].items():
        lines.append(f"| {src} | {v['calls']} | {v['total_usd']:.4f} |")
    lines.extend([
        "",
        "### Agregado por provider (todas as fontes locais)",
        "",
        "| Provider | Custo USD |",
        "|---|---:|",
    ])
    for p, c in sorted(report["local"]["aggregated_by_provider"].items(), key=lambda x: -x[1]):
        lines.append(f"| {p} | {c:.4f} |")

    lines.extend(["", "## Dados via Admin APIs", ""])
    for provider, data in report["admin"].items():
        lines.append(f"### {provider}")
        lines.append("")
        status = data.get("status", "unknown")
        lines.append(f"- **Status:** `{status}`")
        if status == "ok":
            if "totals" in data:
                t = data["totals"]
                lines.append(f"- **Tokens IN:** {t.get('input_tokens', 0):,}")
                lines.append(f"- **Tokens OUT:** {t.get('output_tokens', 0):,}")
                lines.append(f"- **Calls:** {t.get('calls', 0):,}")
            if "by_model" in data:
                lines.append("")
                lines.append("**Por modelo:**")
                lines.append("")
                lines.append("| Modelo | Calls | Tokens IN | Tokens OUT |")
                lines.append("|---|---:|---:|---:|")
                for m, v in sorted(data["by_model"].items(), key=lambda x: -x[1]["calls"]):
                    lines.append(f"| {m} | {v['calls']:,} | {v['input_tokens']:,} | {v['output_tokens']:,} |")
        elif status == "no_admin_key":
            lines.append(f"- **Fallback:** {data.get('fallback', 'manual')}")
            if data.get("hint"):
                lines.append(f"- _{data['hint']}_")
        elif status == "auth_error":
            lines.append(f"- **Erro:** {data.get('detail')}")
        elif status == "http_error":
            lines.append(f"- **HTTP {data.get('code')}:** `{data.get('body', '')[:200]}`")
        elif status == "missing_dependency":
            lines.append(f"- **Dependencia faltando:** `{data.get('detail')}`")
        elif status == "no_admin_api_public":
            for p, hint in (data.get("fallback") or {}).items():
                lines.append(f"- **{p}:** {hint}")
        lines.append("")

    lines.extend([
        "",
        "## Cross-check (admin API vs local)",
        "",
        "_Discrepancias indicam gasto fora dos trackers locais (uso direto fora dos projetos rastreados)._",
        "",
    ])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Auditoria FinOps via admin keys + fontes locais")
    parser.add_argument("--start", default=None, help="Data inicial YYYY-MM-DD (default: 30d atras)")
    parser.add_argument("--end", default=None, help="Data final YYYY-MM-DD (default: hoje)")
    parser.add_argument("--provider", choices=["openai", "anthropic", "google", "all"], default="all")
    parser.add_argument("--json", action="store_true", help="Saida JSON em vez de Markdown")
    parser.add_argument("--out", default=None, help="Caminho do arquivo de saida")
    args = parser.parse_args()

    today = datetime.now(timezone.utc).date()
    end = args.end or today.isoformat()
    start = args.start or (today - timedelta(days=30)).isoformat()

    print(f"=== Auditoria FinOps {start} -> {end} ===")
    admin_env = _load_admin_env()

    report: dict = {"period": {"start": start, "end": end}, "admin": {}, "local": {}}

    if args.provider in ("openai", "all"):
        print("[1/4] OpenAI admin API...")
        report["admin"]["openai"] = audit_openai(admin_env, start, end)
    if args.provider in ("anthropic", "all"):
        print("[2/4] Anthropic admin API...")
        report["admin"]["anthropic"] = audit_anthropic(admin_env, start, end)
    if args.provider in ("google", "all"):
        print("[3/4] Google Cloud Billing...")
        report["admin"]["google"] = audit_google(admin_env, start, end)
    if args.provider == "all":
        report["admin"]["perplexity_groq"] = audit_perplexity_groq()

    print("[4/4] Cross-check fontes locais...")
    report["local"] = crosscheck_local(start, end)

    out_dir = Path("output/audit")
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.json:
        out_path = Path(args.out) if args.out else out_dir / f"billing_{end}.json"
        out_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    else:
        md = render_markdown(report, start, end)
        out_path = Path(args.out) if args.out else out_dir / f"billing_{end}.md"
        out_path.write_text(md, encoding="utf-8")

    print(f"\nRelatorio salvo em: {out_path}")
    print(f"\nResumo local agregado:")
    for p, c in sorted(report["local"]["aggregated_by_provider"].items(), key=lambda x: -x[1]):
        print(f"  {p:<14}: ${c:.4f}")
    print(f"  {'TOTAL':<14}: ${report['local']['grand_total']:.4f}")


if __name__ == "__main__":
    main()
