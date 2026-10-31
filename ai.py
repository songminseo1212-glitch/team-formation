"""오픈라우터 AI 배정.

- 모델 3개를 1차, 2차, 3차로 차례로 부른다(openrouter/free 라우터는 쓰지 않음).
- 모델 하나마다 '부른 때부터 응답을 다 받을 때까지' 전체 시간을 재서 120초가 넘으면 끊는다(어려운 해결).
  requests의 timeout은 바이트 사이 간격만 재므로, 요청을 별도 스레드에서 돌리고 바깥에서 시계를 본다.
- 키는 출력하지 않는다. 오류는 종류 이름과 HTTP 상태 코드만 남긴다.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import re
import time

import requests

URL = "https://openrouter.ai/api/v1/chat/completions"
TIME_LIMIT = 120  # 모델 하나당 전체 대기 시간(초)

# Streamlit Secrets의 OPENROUTER_MODELS(쉼표로 구분)로 바꿀 수 있음.
# 무료 모델은 자주 바뀌므로 https://openrouter.ai/models?q=free 에서 지금 있는 모델로 확인하고 고를 것.
DEFAULT_MODELS = [
    "nvidia/nemotron-3.5-lightning:free",
    "dots-studio/dots-3-note-preview:free",
    "cohere/north-mini-code:free",
]

SYSTEM = "너는 대학 팀 프로젝트의 팀 배정 도우미다. 설명 없이 JSON 하나만 출력한다."

RULES = """아래 '팀이 없는 학생'을 팀에 배정하라.
규칙:
1. 팀이 없는 학생은 빠짐없이, 정확히 한 팀에 배정한다. 이미 팀에 있는 학생은 옮기지 않는다.
2. 배정한 뒤 모든 팀의 인원은 2명 이상 4명 이하다(빈 팀 없음).
3. 한 팀 안에서 main_role(주 역할)이 같은 학생이 2명 이상이면 안 된다.
4. 모든 팀에는 '기획자'를 맡을 학생과 '웹 프로그래머'를 맡을 학생이 서로 다른 사람으로 1명씩 있어야 한다.
   main_role이나 sub_roles(보조 역할) 어느 쪽으로든 맡을 수 있다. 한 학생이 둘을 동시에 맡을 수는 없다.
출력 형식(이 JSON 하나만, 코드 블록이나 설명 없이):
{"assignments": [{"student_no": "학번", "team": 팀 번호(정수)}, ...]}"""


def model_list(secret_fn) -> list[str]:
    raw = secret_fn("OPENROUTER_MODELS")
    if raw:
        models = [m.strip() for m in raw.split(",") if m.strip()]
        if models:
            return models[:3]
    return DEFAULT_MODELS


def build_prompt(state) -> str:
    def stud(s):
        return {"student_no": s.no, "name": s.name, "main_role": s.main, "sub_roles": list(s.subs)}

    data = {
        "teams": [
            {"team": t.no, "members": [stud(s) for s in state.team_students(t.id)]}
            for t in state.teams_sorted()
        ],
        "unassigned": [stud(s) for s in state.unassigned()],
    }
    return RULES + "\n\n입력:\n" + json.dumps(data, ensure_ascii=False, indent=1)


def parse_answer(text: str) -> list[tuple[str, int]]:
    """AI 답에서 (학번, 팀 번호) 목록을 꺼낸다. 형식이 틀리면 ValueError."""
    cleaned = re.sub(r"```(?:json)?", "", text)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("답에서 JSON을 찾지 못함")
    obj = json.loads(cleaned[start:end + 1])
    items = obj.get("assignments") if isinstance(obj, dict) else None
    if not isinstance(items, list):
        raise ValueError("assignments 목록이 없음")
    pairs = []
    for it in items:
        no = str(it["student_no"]).strip()
        team = int(re.sub(r"[^0-9]", "", str(it["team"])))
        pairs.append((no, team))
    return pairs


def ask(model: str, prompt: str, api_key: str, limit: int = TIME_LIMIT, tick=None):
    """모델 하나를 부른다. (답 글, 실패 이유, 걸린 초)를 돌려준다. 성공이면 실패 이유는 None."""
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
        "max_tokens": 8000,          # 추론 모델이 생각하느라 한도를 다 써서 빈 응답을 내는 일을 줄임
        "temperature": 0.2,
        "reasoning": {"effort": "low"},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "X-Title": "team-formation"}
    pool = cf.ThreadPoolExecutor(max_workers=1)
    t0 = time.monotonic()
    future = pool.submit(requests.post, URL, json=payload, headers=headers, timeout=(10, limit))
    try:
        while True:
            left = limit - (time.monotonic() - t0)
            if left <= 0:
                return None, f"{limit}초 안에 응답을 다 받지 못함(시간 초과)", float(limit)
            try:
                resp = future.result(timeout=min(1.0, left))
                break
            except cf.TimeoutError:
                if tick:
                    tick(int(time.monotonic() - t0))
    except Exception as e:  # 연결 실패 등. 메시지에 키가 섞이지 않게 종류 이름만 남김
        return None, f"요청 실패({type(e).__name__})", time.monotonic() - t0
    finally:
        pool.shutdown(wait=False, cancel_futures=True)  # 시간 초과면 기다리지 않고 버림

    sec = time.monotonic() - t0
    if resp.status_code != 200:
        return None, f"HTTP {resp.status_code}", sec
    try:
        content = resp.json()["choices"][0]["message"].get("content") or ""
    except Exception:
        return None, "응답 형식이 이상함", sec
    if not content.strip():
        return None, "빈 응답", sec
    return content, None, sec
