"""Supabase(PostgreSQL) 접근.

- 연결은 st.cache_resource로 만든 연결 풀 하나를 앱 전체가 다시 쓴다(질의마다 새로 연결하지 않음).
- 상태를 바꾸는 동작은 모두 한 트랜잭션 안에서 pg_advisory_xact_lock으로 줄을 세운 뒤
  상태를 읽고 → engine으로 판단하고 → 결과(ops)를 적용한다. 두 사람이 동시에 눌러도 꼬이지 않음.
- 키와 DB 주소는 절대 화면이나 로그에 출력하지 않는다.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager

import bcrypt
import psycopg2
import psycopg2.pool
import streamlit as st
from dotenv import load_dotenv

import engine as E

load_dotenv()  # 로컬 실행 때 .env를 읽음(배포에서는 Streamlit Secrets를 씀)

LOCK_KEY = 20261102
SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")


def secret(name: str):
    """Streamlit Secrets → 환경 변수(.env) 순서로 찾는다. 값을 출력하지 않는다."""
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return os.getenv(name)


@st.cache_resource(show_spinner=False)
def _pool():
    dsn = secret("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL이 없음(.env 또는 Streamlit Secrets 확인)")
    return psycopg2.pool.ThreadedConnectionPool(
        1, 8, dsn, connect_timeout=10, keepalives=1, keepalives_idle=30
    )


@contextmanager
def _tx(lock: bool):
    pool = _pool()
    conn = pool.getconn()
    broken = False
    try:
        with conn:  # 정상 종료면 commit, 예외면 rollback
            with conn.cursor() as cur:
                if lock:
                    cur.execute("select pg_advisory_xact_lock(%s)", (LOCK_KEY,))
                yield cur
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        broken = True
        raise
    finally:
        pool.putconn(conn, close=broken)


def run(fn, lock: bool = True):
    """fn(cur)을 트랜잭션 안에서 실행. 오래 놀던 연결이 끊겼으면 한 번 다시 시도."""
    for attempt in range(2):
        try:
            with _tx(lock) as cur:
                return fn(cur)
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            if attempt:
                raise


# ---------------------------------------------------------------- 시작할 때 한 번
@st.cache_resource(show_spinner=False)
def init():
    """테이블을 만들고(없을 때만), 관리자 계정을 ADMIN_PASSWORD로 만들거나 비밀번호를 갱신한다."""
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        schema = f.read()
    run(lambda cur: cur.execute(schema))

    pw = secret("ADMIN_PASSWORD")
    if not pw:
        raise RuntimeError("ADMIN_PASSWORD가 없음(.env 또는 Streamlit Secrets 확인)")

    def ensure_admin(cur):
        cur.execute("select password_hash from tf_students where student_no = 'admin'")
        row = cur.fetchone()
        if row is None:
            cur.execute(
                "insert into tf_students (student_no, name, main_role, password_hash, is_admin)"
                " values ('admin', '관리자', null, %s, true)",
                (_hash(pw),),
            )
        elif not bcrypt.checkpw(pw.encode(), row[0].encode()):
            cur.execute("update tf_students set password_hash = %s where student_no = 'admin'", (_hash(pw),))

    run(ensure_admin)
    return True


def _hash(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=10)).decode()


# ---------------------------------------------------------------- 로그인
def check_login(student_no: str, password: str) -> bool:
    if not student_no or not password:
        return False

    def body(cur):
        cur.execute("select password_hash from tf_students where student_no = %s", (student_no,))
        return cur.fetchone()

    row = run(body, lock=False)
    return bool(row) and bcrypt.checkpw(password.encode(), row[0].encode())


def is_admin(student_no) -> bool:
    if not student_no:
        return False

    def body(cur):
        cur.execute("select is_admin from tf_students where student_no = %s", (student_no,))
        return cur.fetchone()

    row = run(body, lock=False)
    return bool(row and row[0])


# ---------------------------------------------------------------- 상태 읽기
def _load_state(cur) -> E.State:
    s = E.State()
    cur.execute("select student_id, role from tf_student_sub_roles")
    subs = {}
    for sid, role in cur.fetchall():
        subs.setdefault(sid, []).append(role)
    cur.execute("select id, student_no, name, main_role, is_admin from tf_students")
    for sid, no, name, main, adm in cur.fetchall():
        sub = tuple(sorted(subs.get(sid, []), key=E.ROLES.index))
        s.students[sid] = E.Student(sid, no, name, main, sub, adm)
    cur.execute("select id, team_no, name from tf_teams")
    for tid, n, name in cur.fetchall():
        s.teams[tid] = E.Team(tid, n, name)
        s.members[tid] = []
    cur.execute("select team_id, student_id from tf_members order by team_id, slot_no")
    for tid, sid in cur.fetchall():
        s.members.setdefault(tid, []).append(sid)
    cur.execute(
        "select id, team_id, student_id, status, coalesce(reason, ''),"
        " to_char(created_at at time zone 'Asia/Seoul', 'MM-DD HH24:MI:SS')"  # 서버는 UTC → 한국 시각
        " from tf_applications order by id"
    )
    for row in cur.fetchall():
        s.apps[row[0]] = E.App(*row)
    cur.execute("select application_id, voter_id, vote from tf_votes")
    for aid, vid, vote in cur.fetchall():
        s.votes.setdefault(aid, {})[vid] = vote
    cur.execute("select key, coalesce(value, '') from tf_settings")
    s.settings = dict(cur.fetchall())
    return s


def load() -> E.State:
    return run(_load_state, lock=False)


# ---------------------------------------------------------------- 상태 바꾸기
class SlotTaken(Exception):
    pass


def _apply_ops(cur, ops):
    for op in ops:
        kind = op[0]
        if kind == "insert_app":
            _, tid, sid, status, reason = op
            cur.execute(
                "insert into tf_applications (team_id, student_id, status, reason, decided_at)"
                " values (%s, %s, %s, %s, case when %s = '대기' then null else now() end)",
                (tid, sid, status, reason, status),
            )
        elif kind == "set_app":
            _, aid, status, reason = op
            cur.execute(
                "update tf_applications set status = %s, reason = %s, decided_at = now()"
                " where id = %s and status = '대기'",
                (status, reason, aid),
            )
        elif kind == "add_member":
            _, tid, sid = op
            # 비어 있는 가장 앞 자리(1~4)에 넣는다. 자리가 없으면 아무것도 넣지 않음
            cur.execute(
                "insert into tf_members (team_id, slot_no, student_id)"
                " select %s, min(s), %s from generate_series(1, 4) s"
                " where s not in (select slot_no from tf_members where team_id = %s)"
                " having min(s) is not null",
                (tid, sid, tid),
            )
            if cur.rowcount != 1:
                raise SlotTaken()
        elif kind == "remove_member":
            cur.execute("delete from tf_members where student_id = %s", (op[1],))
        elif kind == "insert_vote":
            _, aid, vid, vote = op
            cur.execute(
                "insert into tf_votes (application_id, voter_id, vote) values (%s, %s, %s)",
                (aid, vid, vote),
            )
        elif kind == "set_setting":
            _set_setting(cur, op[1], op[2])
        else:
            raise ValueError(kind)


def _set_setting(cur, key, value):
    cur.execute(
        "insert into tf_settings (key, value) values (%s, %s)"
        " on conflict (key) do update set value = excluded.value",
        (key, value),
    )


def act(fn, *args) -> str:
    """잠금 → 상태 읽기 → engine 판단 → DB 적용. 결과 안내 문구를 돌려준다."""
    def body(cur):
        msg, ops = fn(_load_state(cur), *args)
        _apply_ops(cur, ops)
        return msg

    try:
        return run(body)
    except (psycopg2.IntegrityError, SlotTaken):
        # DB 유일성 제약이 막은 경우: 먼저 도착한 요청만 기록되고 이 요청은 되돌려짐
        return "막힘: 같은 자리를 다른 요청이 먼저 차지함(DB 제약). 화면을 보고 다시 시도"
    except psycopg2.Error:
        return "막힘: DB 오류로 처리하지 못함"


def reset(rows, n_teams: int):
    """명단으로 처음 상태 만들기. 관리자 계정은 남긴다."""
    hashed = [(r, _hash("1234")) for r in rows]  # 해시는 잠그기 전에 미리 계산

    def body(cur):
        for sql in (
            "delete from tf_votes",
            "delete from tf_applications",
            "delete from tf_members",
            "delete from tf_teams",
            "delete from tf_student_sub_roles where student_id in"
            " (select id from tf_students where not is_admin)",
            "delete from tf_students where not is_admin",
            "delete from tf_settings",
        ):
            cur.execute(sql)
        for i in range(1, n_teams + 1):
            cur.execute("insert into tf_teams (team_no, name) values (%s, %s)", (i, f"팀{i}"))
        for r, h in hashed:
            cur.execute(
                "insert into tf_students (student_no, name, main_role, password_hash, is_admin)"
                " values (%s, %s, %s, %s, false) returning id",
                (r["no"], r["name"], r["main"], h),
            )
            sid = cur.fetchone()[0]
            for role in r["subs"]:
                cur.execute(
                    "insert into tf_student_sub_roles (student_id, role) values (%s, %s)", (sid, role)
                )

    run(body)


# ---------------------------------------------------------------- 배정안, AI 기록
def save_settings(values: dict):
    def body(cur):
        for k, v in values.items():
            _set_setting(cur, k, v)

    run(body)


def get_proposal(state: E.State):
    raw = state.settings.get("proposal") or ""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def log_ai(attempt, model, outcome, detail, seconds):
    def body(cur):
        cur.execute(
            "insert into tf_ai_logs (attempt, model, outcome, detail, seconds) values (%s, %s, %s, %s, %s)",
            (attempt, model, outcome, (detail or "")[:2000], round(seconds, 1)),
        )

    try:
        run(body, lock=False)
    except psycopg2.Error:
        pass


def recent_ai_logs(limit=20):
    def body(cur):
        cur.execute(
            "select to_char(created_at at time zone 'Asia/Seoul', 'MM-DD HH24:MI:SS'),"
            " attempt, model, outcome, seconds, detail from tf_ai_logs order by id desc limit %s",
            (limit,),
        )
        return cur.fetchall()

    return run(body, lock=False)
