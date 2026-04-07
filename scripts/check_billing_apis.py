"""Cross-check via APIs admin onde disponivel."""
import os, time, json, asyncio, httpx
from pathlib import Path
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

START = "2026-03-01"
END = "2026-04-08"

async def anthropic_admin():
    """Tenta admin API. Precisa admin key (sk-ant-admin-...). Nossa key parece ser API key normal."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    async with httpx.AsyncClient(timeout=20) as c:
        # tenta organizations/usage_report (admin only)
        r = await c.get("https://api.anthropic.com/v1/organizations/usage_report/messages",
                        headers=headers,
                        params={"starting_at": f"{START}T00:00:00Z", "ending_at": f"{END}T00:00:00Z"})
    return ("anthropic", r.status_code, r.text[:400])

async def openai_usage():
    """Admin usage API. Needs admin key."""
    key = os.environ.get("OPENAI_API_KEY", "")
    headers = {"Authorization": f"Bearer {key}"}
    start_ts = int(time.mktime(time.strptime(START, "%Y-%m-%d")))
    end_ts = int(time.mktime(time.strptime(END, "%Y-%m-%d")))
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get("https://api.openai.com/v1/organization/usage/completions",
                        headers=headers,
                        params={"start_time": start_ts, "end_time": end_ts, "bucket_width": "1d"})
    return ("openai", r.status_code, r.text[:600])

async def perplexity_usage():
    """Perplexity nao tem usage API publica que eu saiba."""
    key = os.environ.get("PERPLEXITY_API_KEY", "")
    headers = {"Authorization": f"Bearer {key}"}
    async with httpx.AsyncClient(timeout=20) as c:
        try:
            r = await c.get("https://api.perplexity.ai/usage", headers=headers)
            return ("perplexity", r.status_code, r.text[:300])
        except Exception as e:
            return ("perplexity", 0, str(e))

async def groq_usage():
    """Groq nao tem usage API publica."""
    key = os.environ.get("GROQ_API_KEY", "")
    headers = {"Authorization": f"Bearer {key}"}
    async with httpx.AsyncClient(timeout=20) as c:
        try:
            r = await c.get("https://api.groq.com/openai/v1/usage", headers=headers)
            return ("groq", r.status_code, r.text[:300])
        except Exception as e:
            return ("groq", 0, str(e))

async def main():
    results = await asyncio.gather(
        anthropic_admin(), openai_usage(), perplexity_usage(), groq_usage(),
        return_exceptions=True
    )
    for r in results:
        if isinstance(r, tuple):
            name, status, body = r
            print(f"\n=== {name.upper()} ===")
            print(f"HTTP: {status}")
            print(f"Body: {body}")
        else:
            print(f"EXC: {r}")

asyncio.run(main())
