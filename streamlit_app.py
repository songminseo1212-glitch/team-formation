"""팀 프로젝트를 위한 팀 편성 웹 앱 (Streamlit + Supabase + OpenRouter).

화면 요소의 key는 공고 2.7절 표와 똑같이 둔다(자동 채점 프로그램이 key로 찾음).
규칙 판단은 engine.py, DB는 db.py, AI 호출은 ai.py에 있다.
"""
import json

import pandas as pd
import psycopg2
import streamlit as st

import ai
import db
import engine as E

st.set_page_config(page_title="팀 편성", page_icon="🧩", layout="wide")
REFRESH = 3  # 다른 사람의 변경을 다시 그리는 간격(초). 공고: 5초 이하

try:
    db.init()
except Exception as e:  # 키·주소를 화면에 내지 않도록 우리 메시지만 보여 줌
    st.error(f"앱을 시작하지 못함: {e if isinstance(e, RuntimeError) else type(e).__name__}")
    st.stop()


# ---------------------------------------------------------------- 공통
def say(msg: str):
    st.session_state["result"] = msg


def fill_result(box):
    """key가 result인 컨테이너에 마지막 결과 안내를 보여 줌. 첫 단어: 승인/대기/거절/막힘."""
    msg = st.session_state.get("result")
    with box:
        if not msg:
            st.caption("단추를 누르면 결과가 여기에 나옴")
        elif msg.startswith("승인"):
            st.success(msg)
        elif msg.startswith("대기"):
            st.info(msg)
        elif msg.startswith("거절"):
            st.warning(msg)
        else:
            st.error(msg)


def logout():
    for k in ("user_no", "result"):
        st.session_state.pop(k, None)


def render_teams(state, me=None, apply_disabled=False):
    """팀 카드. key: team_1, team_2 …  학생 화면이면 지원 단추 apply_1, apply_2 …"""
    teams = state.teams_sorted()
    my_team = state.team_of(me.id) if me else None
    if not teams:
        st.caption("팀이 아직 없음(관리자가 명단을 올리면 생김)")
        return
    for start in range(0, len(teams), 4):
        cols = st.columns(4)
        for col, t in zip(cols, teams[start:start + 4]):
            with col:
                with st.container(key=f"team_{t.no}", border=True):
                    members = state.team_students(t.id)
                    mine = " · 🟦 **내 팀**" if my_team == t.id else ""
                    st.markdown(f"**{t.name}** ({len(members)}/4명){mine}")
                    if members:
                        st.markdown("\n".join(f"- {s.name} · {s.roles_text()}" for s in members))
                    else:
                        st.caption("빈 팀")
                    if me is not None:
                        st.button(
                            f"{t.name}에 지원", key=f"apply_{t.no}", disabled=apply_disabled,
                            on_click=cb_apply, args=(t.id,),
                        )
    if my_team is not None:
        n = state.teams[my_team].no
        st.markdown(
            f"<style>.st-key-team_{n}{{background:#eef4ff;border:2px solid #4a7bd0 !important;}}</style>",
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------- 단추 콜백(화면을 다시 그리기 전에 실행됨)
def cb_apply(team_id):
    say(db.act(E.act_apply, st.session_state.get("user_no"), team_id))


def cb_vote(app_id, yes):
    say(db.act(E.act_vote, st.session_state.get("user_no"), app_id, yes))


def admin_ok() -> bool:
    """관리자 기능은 매번 DB에서 관리자인지 다시 확인함(학생 계정으로 못 씀)."""
    if db.is_admin(st.session_state.get("user_no")):
        return True
    say("막힘: 관리자만 할 수 있음")
    return False


def cb_start():
    if not admin_ok():
        return
    f = st.session_state.get("admin_roster_file")
    n = int(st.session_state.get("admin_team_count") or 0)
    if f is None:
        say("막힘: 명단 CSV 파일을 먼저 올림")
        return
    if n < 1:
        say("막힘: 팀 수는 1 이상이어야 함")
        return
    try:
        rows = E.parse_roster(f.getvalue().decode("utf-8-sig"))  # BOM이 있어도 읽음
    except UnicodeDecodeError:
        say("막힘: UTF-8 CSV가 아님")
        return
    except ValueError as e:
        say(f"막힘: 명단 CSV가 틀림 · {e}")
        return
    try:
        db.reset(rows, n)
    except psycopg2.Error:
        say("막힘: DB 오류로 처음 상태를 만들지 못함")
        return
    say(f"승인: 학생 {len(rows)}명과 빈 팀 {n}개로 처음 상태를 만듦")


def cb_close():
    if admin_ok():
        say(db.act(E.act_close))


def cb_confirm():
    if not admin_ok():
        return
    prop = db.get_proposal(db.load())
    pairs = [tuple(p) for p in prop["pairs"]] if prop else None
    say(db.act(E.act_confirm, pairs))


# ---------------------------------------------------------------- AI 배정
def run_assign(box):
    """AI 배정 단추. 1차→2차→3차 모델, 모두 실패하면 앱이 직접 배정. 진행 상황을 box(assign_status)에 보여 줌."""
    if not admin_ok():
        return
    state = db.load()
    if not state.closed:
        say("막힘: 모집을 마감한 뒤에 배정할 수 있음")
        return
    if state.settings.get("confirmed") == "1":
        say("막힘: 이미 배정을 확정함(다시 하려면 명단을 다시 올림)")
        return

    solution = E.solve(state)  # 규칙을 지키는 배정이 있는지 먼저 확인
    log = []
    if solution is None:
        reasons = E.infeasible_reasons(state)
        log.append("규칙을 지키는 배정이 없음 · " + " / ".join(reasons))
        db.save_settings({"proposal": "", "assign_log": json.dumps(log, ensure_ascii=False)})
        say("막힘: 규칙을 지키는 배정이 없음 · " + reasons[0])
        return

    pairs, source = None, None
    if not state.unassigned():
        pairs, source = [], "배정할 학생 없음"
        log.append("팀이 없는 학생이 없어 AI를 부르지 않음")
    elif not st.session_state.get("use_ai", True):
        log.append("AI를 부르지 않음(체크 해제) → 앱이 직접 배정")
    else:
        api_key = db.secret("OPENROUTER_API_KEY")
        if not api_key:
            log.append("OPENROUTER_API_KEY 없음 → AI를 부르지 못함")
        else:
            prompt = ai.build_prompt(state)
            with box:
                ph = st.empty()
            for i, model in enumerate(ai.model_list(db.secret), start=1):
                tag = f"{i}차 모델 {model}"

                def tick(sec, tag=tag):
                    ph.info(f"⏳ {tag}의 응답을 기다리는 중 · {sec}초 (모델마다 최대 {ai.TIME_LIMIT}초)")

                tick(0)
                text, err, sec = ai.ask(model, prompt, api_key, tick=tick)
                if err:
                    log.append(f"❌ {tag}: {err} ({sec:.0f}초) → 다음 모델로")
                    db.log_ai(i, model, err, "", sec)
                    continue
                try:
                    cand = ai.parse_answer(text)
                except (ValueError, KeyError, TypeError) as e:
                    log.append(f"❌ {tag}: 답 형식이 틀림({e}) ({sec:.0f}초) → 다음 모델로")
                    db.log_ai(i, model, "형식 오류", text[:1000], sec)
                    continue
                errs = E.check_assignment(state, cand)  # 앱이 AI의 답을 규칙으로 검사
                if errs:
                    log.append(f"❌ {tag}: 검사 실패 · {'; '.join(errs[:3])} ({sec:.0f}초) → 다음 모델로")
                    db.log_ai(i, model, "검사 실패", "; ".join(errs) + "\n---\n" + text[:1000], sec)
                    continue
                pairs, source = cand, tag
                log.append(f"✅ {tag}: 검사 통과 ({sec:.0f}초)")
                db.log_ai(i, model, "통과", "", sec)
                break
            ph.empty()
            if pairs is None:
                log.append("3차 모델까지 실패 → 앱이 모든 경우를 따져 규칙을 지키는 배정을 찾음")

    if pairs is None:
        pairs, source = solution, "앱(모든 경우를 따져 찾은 배정)"
    pairs = sorted(dict(pairs).items())
    db.save_settings({
        "proposal": json.dumps({"source": source, "pairs": pairs}, ensure_ascii=False),
        "assign_log": json.dumps(log, ensure_ascii=False),
    })
    say(f"승인: 검사를 통과한 배정안을 만듦(출처: {source}) · 확인 후 '배정안 확정'을 누름")


# ---------------------------------------------------------------- 로그인 화면
def login_page():
    st.title("팀 편성")
    with st.container(border=True):
        st.text_input("학번", key="login_student_no")
        st.text_input("비밀번호", type="password", key="login_password")
        clicked = st.button("로그인", key="login_submit")
    if clicked:
        no = (st.session_state.get("login_student_no") or "").strip()
        pw = st.session_state.get("login_password") or ""
        if db.check_login(no, pw):
            st.session_state["user_no"] = no
            st.session_state["result"] = None
            st.rerun()
        else:
            st.error("막힘: 학번이나 비밀번호가 틀림")


# ---------------------------------------------------------------- 학생 화면(전체가 3초마다 바뀐 부분만 다시 그림)
@st.fragment(run_every=REFRESH)
def student_page():
    state = db.load()
    me = state.by_no(st.session_state.get("user_no"))
    if me is None or me.is_admin:  # 명단을 다시 올려 계정이 없어졌으면 로그아웃
        logout()
        st.rerun(scope="app")

    st.title("팀 편성")
    with st.container(key="whoami"):
        st.markdown(f"로그인: **{me.name}** ({me.no})")
    fill_result(st.container(key="result"))

    my_team = state.team_of(me.id)
    pending = state.pending_of(me.id)
    team_text = state.teams[my_team].name if my_team else "아직 팀이 없음"
    st.caption(
        f"모집 상태: **{'마감' if state.closed else '모집 중'}** · 내 팀: **{team_text}**"
        f" · 화면은 {REFRESH}초마다 저절로 바뀜"
    )

    if my_team is not None:
        st.subheader("내 팀에 들어온 지원")
        apps = [a for a in state.apps_sorted() if a.team_id == my_team and a.status == "대기"]
        if not apps:
            st.caption("대기 중인 지원 없음")
        for a in apps:
            s = state.students[a.student_id]
            c1, c2, c3 = st.columns([6, 1, 1])
            c1.write(f"{s.name} · 학번 {s.no} · {s.roles_text()} · {a.created}")
            my_vote = state.votes.get(a.id, {}).get(me.id)
            if my_vote:
                c2.write(f"내 투표: **{my_vote}**")
            else:
                c2.button("찬성", key=f"vote_yes_{s.no}", on_click=cb_vote, args=(a.id, True))
                c3.button("거부", key=f"vote_no_{s.no}", on_click=cb_vote, args=(a.id, False))

    st.subheader("팀 목록")
    why = None
    if state.closed:
        why = "모집이 마감됨"
    elif my_team is not None:
        why = f"이미 {team_text}에 속해 있음"
    elif pending is not None:
        why = f"{state.teams[pending.team_id].name}에 낸 지원이 대기 중임. 결과가 나온 뒤 다시 지원할 수 있음"
    if why:
        st.caption(f"지원 단추를 쓸 수 없음: {why}")
    render_teams(state, me, apply_disabled=why is not None)

    st.subheader("내 지원 기록")
    rows = [
        {"팀": state.teams[a.team_id].name, "상태": a.status, "사유": a.reason, "시각": a.created}
        for a in state.apps_sorted() if a.student_id == me.id
    ]
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    else:
        st.caption("지원 기록 없음")


# ---------------------------------------------------------------- 관리자 화면
@st.fragment(run_every=REFRESH)
def admin_unassigned():
    state = db.load()
    un = state.unassigned()
    with st.container(key="unassigned"):
        st.write(f"팀이 없는 학생 {len(un)}명: " + (", ".join(s.name for s in un) or "없음"))


@st.fragment(run_every=REFRESH)
def admin_live():
    state = db.load()
    st.subheader("현황")
    st.caption(f"학생 {sum(1 for s in state.students.values() if not s.is_admin)}명"
               f" · 팀이 없는 학생 {len(state.unassigned())}명 · 화면은 {REFRESH}초마다 저절로 바뀜")
    render_teams(state)
    st.subheader("지원 기록")
    rows = [
        {
            "지원자": state.students[a.student_id].name,
            "팀": state.teams[a.team_id].name,
            "상태": a.status,
            "사유": a.reason,
            "찬성": sum(1 for v in state.votes.get(a.id, {}).values() if v == "찬성"),
            "시각": a.created,
        }
        for a in state.apps_sorted()
    ]
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    else:
        st.caption("지원 기록 없음")


def admin_page(me):
    st.title("팀 편성 · 관리자")
    with st.container(key="whoami"):
        st.markdown(f"로그인: **{me.name}** ({me.no})")
    result_box = st.container(key="result")  # 내용은 맨 끝에 채움(AI 배정 결과까지 반영)

    st.subheader("명단 올리기")
    st.caption("명단 CSV(UTF-8, 머리글 student_no,name,main_role,sub_roles)와 팀 수를 넣고 단추를 누르면 "
               "지원, 투표, 팀원, 마감 표시를 모두 지우고 처음 상태를 만듦")
    st.file_uploader("명단 CSV", type=["csv"], key="admin_roster_file")
    st.number_input("팀 수", min_value=1, max_value=20, value=3, step=1, key="admin_team_count")
    st.button("명단으로 처음 상태 만들기", key="admin_start", on_click=cb_start)

    st.divider()
    st.subheader("마감과 배정")
    state = db.load()
    st.write(f"모집 상태: **{'마감' if state.closed else '모집 중'}**"
             + (" · 배정 확정됨" if state.settings.get("confirmed") == "1" else ""))
    st.button("모집 마감", key="admin_close", on_click=cb_close, type="primary")
    admin_unassigned()
    st.checkbox("AI를 부름(끄면 앱이 바로 배정함)", value=True, key="use_ai")
    clicked = st.button("AI 배정", key="admin_assign", type="primary")
    status_box = st.container(key="assign_status")
    if clicked:
        run_assign(status_box)
        state = db.load()

    with status_box:
        try:
            log = json.loads(state.settings.get("assign_log") or "[]")
        except ValueError:
            log = []
        for line in log:
            if line.startswith("규칙을 지키는 배정이 없음"):
                st.error(line)
            elif line.startswith("✅"):
                st.success(line)
            else:
                st.write(line)

    prop = db.get_proposal(state)
    if prop:
        st.markdown(f"**검사를 통과한 배정안** · 출처: {prop['source']}")
        rows = []
        for no, tn in prop["pairs"]:
            s, t = state.by_no(no), state.team_by_no(tn)
            if s and t:
                rows.append({"이름": s.name, "주 역할": s.main, "보조 역할": ", ".join(s.subs) or "없음", "배정된 팀": t.name})
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        else:
            st.caption("배정할 학생 없음(지금 구성을 그대로 확정함)")
    st.button("배정안 확정", key="admin_confirm", on_click=cb_confirm)

    with st.expander("AI 기록(최근 20건)"):
        logs = db.recent_ai_logs(20)
        if logs:
            st.dataframe(
                pd.DataFrame(logs, columns=["시각", "차수", "모델", "결과", "초", "내용"]),
                hide_index=True, use_container_width=True,
            )
        else:
            st.caption("기록 없음")

    st.divider()
    admin_live()
    fill_result(result_box)


# ---------------------------------------------------------------- 시작
user_no = st.session_state.get("user_no")
if not user_no:
    login_page()
    st.stop()

with st.sidebar:
    if st.button("로그아웃", key="logout"):
        logout()
        st.rerun()

if db.is_admin(user_no):
    admin_page(db.load().by_no(user_no))
else:
    student_page()
