"""Smoke test dos 3 modelos Anthropic atualizados (Opus 4.6 / Sonnet 4.5 / Haiku 4.5)."""
import os, time, asyncio, httpx
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

PROMPT = "Responda apenas: OK"
MODELS = [
    ("claude-opus-4-6",   15.00, 75.00),
    ("claude-sonnet-4-5",  3.00, 15.00),
    ("claude-haiku-4-5",   0.80,  4.00),
]

async def test(model, in_p, out_p):
    t0 = time.time()
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                     "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": model, "max_tokens": 10,
                  "messages":[{"role":"user","content":PROMPT}]})
    dt = time.time() - t0
    if r.status_code != 200:
        return (model, "FAIL", dt, r.status_code, r.text[:300], 0)
    j = r.json()
    inp, out = j["usage"]["input_tokens"], j["usage"]["output_tokens"]
    cost = inp/1e6*in_p + out/1e6*out_p
    return (model, "OK", dt, 200, j["content"][0]["text"][:30], cost)

async def main():
    results = await asyncio.gather(*[test(m, i, o) for m, i, o in MODELS], return_exceptions=True)
    print(f"\n{'Modelo':<22} {'Status':<6} {'Lat':<8} {'HTTP':<6} {'Custo':<14} Resp")
    print("-" * 90)
    for r in results:
        if isinstance(r, Exception):
            print(f"EXC: {r}")
            continue
        m, s, dt, h, t, c = r
        print(f"{m:<22} {s:<6} {dt:>5.2f}s   {h:<6} ${c:<13.6f} {t}")

asyncio.run(main())
