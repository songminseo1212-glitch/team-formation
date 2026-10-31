# 팀 프로젝트를 위한 팀 편성 웹 앱

작성자: 송민서

## 1. 배포 주소
https://team-formation-ms26.streamlit.app

## 2. PRD와 구현 계획의 위치
- PRD: `docs/PRD.md`
- 구현 계획: `docs/구현 계획.md`
- DB 테이블: `schema.sql`

## 3. 고른 해결 방법
| 기능 | 해결 | 설명 |
|---|---|---|
| 다른 사람의 변경 보기 | 어려운 해결 | `st.fragment(run_every=3)`로 3초마다 바뀐 부분만 다시 그림 |
| 필수 포함 역할을 지킬 수 없는 팀 | 어려운 해결 | 지원할 때 남는 자리 수가 아직 못 채운 필수 역할 수보다 적으면 막음 (`engine.join_problem`). 부록 3의 11단계에서 "막힘: 필수 포함 역할을 맡을 학생의 자리가 모자람" 확인 (`img/hard-step11-blocked.png`) |
| 같은 팀에 대기 중인 지원이 여럿 | 어려운 해결 | 마지막 찬성이 들어온 순간 규칙을 다시 검사함. 처리 순서는 투표가 끝난 순서. 부록 3의 8단계에서 "거절: 승인 직전 검사에서 걸림" 확인 (`img/hard-step8-recheck.png`) |
| 마지막 자리에 두 명이 동시에 들어감 | 어려운 해결 | `tf_members`의 (team_id, slot_no) 기본 키와 student_id 유일성 제약(`schema.sql`), DB 잠금 |
| AI 응답이 늦을 때 | 어려운 해결 | 모델마다 전체 대기 시간 120초를 재서 끊고 다음 모델로 넘어감 (`ai.ask`) |
| 3차 모델까지 실패 | 어려운 해결 | 앱이 모든 경우를 따져 다섯 가지 검사를 통과하는 배정을 찾음 (`engine.solve`) |
| AI 대기 중 화면 | 어려운 해결 | "n차 모델 …의 응답을 기다리는 중 · k초"를 1초마다 갱신 (`img/hard-ai-waiting.png`) |
| DB 일시정지 막기 | 어려운 해결 | GitHub Actions가 매일 DB에 질의 (`.github/workflows/keepalive.yml`). 수동 실행 성공 확인 (`img/keepalive-success.png`) |

## 4. 쓴 모델 3개
- 1차: `nvidia/nemotron-3.5-lightning:free`
- 2차: `dots-studio/dots-3-note-preview:free`
- 3차: `cohere/north-mini-code:free`

## 5. 오픈라우터 AI 모델 호출 실패 사례
- **시간 초과 → 다음 모델:** 부록 3(어려움)의 15단계 AI 배정에서 1차 모델이 120초 안에 응답을 다 보내지 않아 끊었고, 2차 모델은 85초 뒤 빈 응답을 내서 다시 넘어감. 3차 모델이 81초 만에 검사를 통과하는 배정안을 냄. 화면 `img/hard-ai-timeout-fallback.png`, AI 기록 `img/hard-ai-log.png`
- **빈 응답 → 다음 모델:** 부록 2(중간)의 11단계 AI 배정에서 1차 모델(109초), 2차 모델(84초)이 모두 빈 응답을 내서 3차 모델로 넘어감. 3차 모델이 36초 만에 통과. 화면 `img/medium-ai-fallback.png`, AI 기록 `img/medium-ai-log.png`
- **모델 고르는 단계의 실패:** 후보 모델을 시험하는 동안 HTTP 404(무료 제공 종료, 제공자 없음), 403(에이전트 전용), 503(제공 서버 없음), 429(사용량 초과)를 받음. 화면 `img/free-model-test.png`, `img/free-model-find.png`

## 6. 클로드가 잘못된 추론이나 답변을 한 사례와 알아챈 방법
- **사례:** 클로드가 추천한 무료 모델 후보 6개 가운데 5개가 이미 무료로 쓸 수 없는 모델이었음. 그중 `nvidia/nemotron-3-super-120b-a12b:free`는 클로드가 만든 `ai.py`의 기본 모델이기도 했음. 클로드는 몇 주 전 웹 검색 결과를 근거로 골랐는데, 그 사이 무료 모델 목록이 바뀌었음. 예: `nvidia/nemotron-3-super-120b-a12b:free`는 "무료로 쓸 수 없음"(404), `poolside/laguna-s-2.1:free`는 "제공자가 없음"(404), `thinkingmachines/inkling-small:free`는 "에이전트 전용"(403).
- **알아챈 방법:** 모델 이름을 앱에 넣기 전에 시험 스크립트(`test_models.py`)로 6개를 한 번씩 불러 봄. 1개만 성공하고 5개가 실패함 (화면 `img/free-model-test.png`).
- **고친 방법:** 추측 대신 오픈라우터의 모델 목록 API에서 지금 무료인 모델을 받아 차례로 시험하는 스크립트(`find_free.py`)를 만들어, 성공한 모델 가운데 회사가 다른 3개를 고름 (화면 `img/free-model-find.png`). `ai.py`의 `DEFAULT_MODELS`를 바꿔 커밋 `887f9ed`에 반영함.

## 7. 발견한 문제점이나 개선 필요 사항
- **AI 진행 안내에서 모델 이름이 깨져 보임:** 부록 1(쉬움)의 6단계에서 `assign_status`의 모델 이름 `…lightning:free` 중 `:free` 자리가 깨진 네모 글자로 보임. Streamlit이 `:free`를 이모지 단축 코드로 읽은 것으로 보임. 중간, 어려움 시나리오에서도 같음. 배정 기능에는 영향이 없으나, 모델 이름을 코드 서식(`` ` ``)으로 감싸 표시하도록 고칠 필요가 있음. 화면 `img/easy-ai-pass.png`
- **탭을 많이 열면 앱이 다시 시작됨:** 부록 2(중간)의 7단계에서 학생 탭을 10개 가까이 열어 시험하자 앱이 "Your app is waking up" 화면과 함께 다시 시작되고 모든 탭의 로그인이 풀림. 탭마다 3초마다 다시 그리는 부담이 무료 서버의 자원을 넘은 것으로 추정함. DB에 저장된 진행 상황은 남아 있어 다시 로그인한 뒤 이어서 시험함. 갱신 간격을 5초로 늘리는 것을 개선안으로 둠. 화면 `img/medium-app-restart.png`
- **AI가 실패하면 배정이 오래 걸림:** 부록 3(어려움)의 15단계에서 1차, 2차 모델이 실패해 배정안이 나오기까지 약 5분(120초 + 85초 + 81초)이 걸림. 120초 제한 안에서도 빈 응답이 늦게 오면 기다리는 시간이 김. 화면 `img/hard-ai-timeout-fallback.png`

## 로컬 실행
```bash
pip install -r requirements.txt
cp .env.example .env     # 값을 채움
python tests/test_engine.py   # 연습용 시나리오 3개를 DB 없이 시험
streamlit run streamlit_app.py
```