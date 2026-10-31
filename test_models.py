import json, os, time, urllib.request, urllib.error
from dotenv import load_dotenv

load_dotenv()
KEY = os.getenv("OPENROUTER_API_KEY")
print("키:", "있음" if KEY else "없음")

MODELS = [
    "nvidia/nemotron-3.5-lightning:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "poolside/laguna-s-2.1:free",
    "thinkingmachines/inkling-small:free",
    "qwen/qwen3.8-27b:free",
    "openai/gpt-oss-20b:free",
]

for m in MODELS:
    body = json.dumps({
        "model": m,
        "messages": [{"role": "user", "content": "숫자 1, 2, 3을 JSON 배열로만 답해."}],
        "max_tokens": 2000,
        "reasoning": {"effort": "low"},
    }).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
    )
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
        text = (data["choices"][0]["message"].get("content") or "").strip()
        result = "성공" if text else "빈 응답"
        print(f"{m}: {result} ({time.time()-t:.0f}초) {text[:60]}")
    except urllib.error.HTTPError as e:
        print(f"{m}: 실패 HTTP {e.code} {e.read().decode(errors='ignore')[:150]}")
    except Exception as e:
        print(f"{m}: 실패 {type(e).__name__}")