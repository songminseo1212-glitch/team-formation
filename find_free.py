import json, os, urllib.request, urllib.error
from dotenv import load_dotenv

load_dotenv()
KEY = os.getenv("OPENROUTER_API_KEY")
print("키:", "있음" if KEY else "없음")

# 1) 지금 무료인 텍스트 모델 목록 받기 (AI 호출 횟수에 안 들어감)
with urllib.request.urlopen("https://openrouter.ai/api/v1/models", timeout=30) as r:
    models = json.loads(r.read())["data"]
free = [m["id"] for m in models
        if m["id"].endswith(":free")
        and "text" in m.get("architecture", {}).get("output_modalities", ["text"])]
print(f"\n지금 무료 모델 {len(free)}개:")
for mid in free:
    print("  ", mid)

# 2) 시험 (이미 시험한 것은 건너뜀, nvidia가 아닌 회사부터)
def test(m):
    body = json.dumps({
        "model": m,
        "messages": [{"role": "user", "content": "숫자 1, 2, 3을 JSON 배열로만 답해."}],
        "max_tokens": 2000,
        "reasoning": {"effort": "low"},
    }).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
        text = (data["choices"][0]["message"].get("content") or "").strip()
        return bool(text), ("성공 " + text[:40]) if text else "빈 응답"
    except urllib.error.HTTPError as e:
        return False, f"실패 HTTP {e.code} {e.read().decode(errors='ignore')[:100]}"
    except Exception as e:
        return False, f"실패 {type(e).__name__}"

tested = {
    "nvidia/nemotron-3.5-lightning:free", "nvidia/nemotron-3-super-120b-a12b:free",
    "poolside/laguna-s-2.1:free", "thinkingmachines/inkling-small:free",
    "qwen/qwen3.8-27b:free", "openai/gpt-oss-20b:free",
}
cands = sorted([m for m in free if m not in tested], key=lambda m: m.startswith("nvidia/"))

print("\n시험 시작 (최대 10개):")
found, companies, calls = [], {"nvidia"}, 0
for m in cands:
    co = m.split("/")[0]
    if co in companies and len(cands) > 3:
        continue
    if calls >= 10 or len(found) >= 2:
        break
    calls += 1
    ok, msg = test(m)
    print(f"  {m}: {msg}")
    if ok:
        found.append(m)
        companies.add(co)

print("\n추천 1차, 2차, 3차:", ["nvidia/nemotron-3.5-lightning:free"] + found)