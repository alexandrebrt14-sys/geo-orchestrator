"""Smoke test: chama cada um dos 5 LLMs com prompt curto e mede tempo+custo."""
import os, time, json, asyncio, httpx
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

PROMPT = "Responda apenas: OK"

async def test_anthropic():
    t0 = time.time()
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                     "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-opus-4-6", "max_tokens": 10,
                  "messages": [{"role": "user", "content": PROMPT}]})
    dt = time.time() - t0
    if r.status_code != 200:
        return ("Anthropic", "FAIL", dt, r.status_code, r.text[:200], 0)
    j = r.json()
    inp, out = j["usage"]["input_tokens"], j["usage"]["output_tokens"]
    cost = inp/1e6*15 + out/1e6*75  # opus-4.6: $15/$75
    return ("Anthropic claude-opus-4.6", "OK", dt, 200, j["content"][0]["text"][:50], cost)

async def test_openai():
    t0 = time.time()
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post("https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}", "Content-Type": "application/json"},
            json={"model": "gpt-4o", "max_tokens": 10,
                  "messages": [{"role": "user", "content": PROMPT}]})
    dt = time.time() - t0
    if r.status_code != 200:
        return ("OpenAI gpt-4o", "FAIL", dt, r.status_code, r.text[:200], 0)
    j = r.json()
    inp, out = j["usage"]["prompt_tokens"], j["usage"]["completion_tokens"]
    cost = inp/1e6*2.5 + out/1e6*10  # gpt-4o: $2.5/$10
    return ("OpenAI gpt-4o", "OK", dt, 200, j["choices"][0]["message"]["content"][:50], cost)

async def test_google():
    t0 = time.time()
    key = os.environ["GOOGLE_AI_API_KEY"]
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-pro:generateContent?key={key}",
            json={"contents": [{"parts": [{"text": PROMPT}]}],
                  "generationConfig": {"maxOutputTokens": 10}})
    dt = time.time() - t0
    if r.status_code != 200:
        return ("Google gemini-2.5-pro", "FAIL", dt, r.status_code, r.text[:200], 0)
    j = r.json()
    usage = j.get("usageMetadata", {})
    inp = usage.get("promptTokenCount", 0)
    out = usage.get("candidatesTokenCount", 0)
    cost = inp/1e6*1.25 + out/1e6*5.00  # gemini 2.5 pro
    txt = ""
    try:
        txt = j["candidates"][0]["content"]["parts"][0]["text"][:50]
    except: txt = str(j)[:80]
    return ("Google gemini-2.5-pro", "OK", dt, 200, txt, cost)

async def test_perplexity():
    t0 = time.time()
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post("https://api.perplexity.ai/chat/completions",
            headers={"Authorization": f"Bearer {os.environ['PERPLEXITY_API_KEY']}", "Content-Type": "application/json"},
            json={"model": "sonar-pro", "max_tokens": 10,
                  "messages": [{"role": "user", "content": PROMPT}]})
    dt = time.time() - t0
    if r.status_code != 200:
        return ("Perplexity sonar-pro", "FAIL", dt, r.status_code, r.text[:200], 0)
    j = r.json()
    u = j.get("usage", {})
    inp, out = u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
    cost = inp/1e6*3 + out/1e6*15 + 0.005  # sonar-pro: $3/$15 + req fee
    return ("Perplexity sonar-pro", "OK", dt, 200, j["choices"][0]["message"]["content"][:50], cost)

async def test_groq():
    t0 = time.time()
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post("https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}", "Content-Type": "application/json"},
            json={"model": "llama-3.3-70b-versatile", "max_tokens": 10,
                  "messages": [{"role": "user", "content": PROMPT}]})
    dt = time.time() - t0
    if r.status_code != 200:
        return ("Groq llama-3.3-70b", "FAIL", dt, r.status_code, r.text[:200], 0)
    j = r.json()
    u = j.get("usage", {})
    inp, out = u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
    cost = inp/1e6*0.59 + out/1e6*0.79
    return ("Groq llama-3.3-70b", "OK", dt, 200, j["choices"][0]["message"]["content"][:50], cost)

async def main():
    results = await asyncio.gather(
        test_anthropic(), test_openai(), test_google(), test_perplexity(), test_groq(),
        return_exceptions=True
    )
    print(f"\n{'Provider':<28} {'Status':<6} {'Latencia':<10} {'HTTP':<6} {'Custo USD':<12} Resposta")
    print("-" * 110)
    total = 0
    for r in results:
        if isinstance(r, Exception):
            print(f"EXC: {r}")
            continue
        name, status, dt, http, txt, cost = r
        total += cost
        print(f"{name:<28} {status:<6} {dt:>7.2f}s   {http:<6} ${cost:<11.6f} {txt}")
    print("-" * 110)
    print(f"{'TOTAL custo do teste:':<58} ${total:.6f}")

asyncio.run(main())
