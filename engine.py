"""팀 편성 규칙 엔진.

DB와 화면을 전혀 모르는 순수 함수만 둔다.
- 각 동작 함수(act_*)는 지금 상태(State)를 받아 (결과 안내 문구, 바꿀 내용 목록 ops)를 돌려준다.
- db.py가 잠금(트랜잭션) 안에서 상태를 읽고, 이 함수를 부르고, ops를 DB에 적용한다.
- 그래서 tests/test_engine.py 처럼 DB 없이도 시나리오를 시험할 수 있다.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from itertools import combinations

ROLES = ("기획자", "웹 프로그래머", "프론트엔드 디자이너", "테스터", "보고서 작성자", "발표자")
PL, WP = "기획자", "웹 프로그래머"
REQUIRED = (PL, WP)
MIN_SIZE, MAX_SIZE = 2, 4


# ---------------------------------------------------------------- 자료 구조
@dataclass
class Student:
    id: int
    no: str
    name: str
    main: str | None
    subs: tuple = ()
    is_admin: bool = False

    def can(self, role: str) -> bool:
        """주 역할이나 보조 역할로 그 역할을 맡을 수 있는가."""
        return self.main == role or role in self.subs

    def roles_text(self) -> str:
        sub = ", ".join(self.subs) if self.subs else "없음"
        return f"주 역할 {self.main} · 보조 역할 {sub}"


@dataclass
class Team:
    id: int
    no: int
    name: str


@dataclass
class App:
    id: int
    team_id: int
    student_id: int
    status: str  # 대기, 승인, 거절
    reason: str = ""
    created: str = ""


@dataclass
class State:
    students: dict = field(default_factory=dict)  # id -> Student
    teams: dict = field(default_factory=dict)     # id -> Team
    members: dict = field(default_factory=dict)   # team_id -> [student_id] (자리 순서)
    apps: dict = field(default_factory=dict)      # id -> App
    votes: dict = field(default_factory=dict)     # app_id -> {voter_id: '찬성' | '거부'}
    settings: dict = field(default_factory=dict)  # key -> value

    @property
    def closed(self) -> bool:
        return self.settings.get("closed") == "1"

    def by_no(self, no):
        for s in self.students.values():
            if s.no == no:
                return s
        return None

    def team_by_no(self, n):
        for t in self.teams.values():
            if t.no == n:
                return t
        return None

    def teams_sorted(self):
        return sorted(self.teams.values(), key=lambda t: t.no)

    def team_of(self, sid):
        for tid, ms in self.members.items():
            if sid in ms:
                return tid
        return None

    def pending_of(self, sid):
        for a in self.apps.values():
            if a.student_id == sid and a.status == "대기":
                return a
        return None

    def team_students(self, tid):
        return [self.students[i] for i in self.members.get(tid, [])]

    def unassigned(self):
        return sorted(
            (s for s in self.students.values() if not s.is_admin and self.team_of(s.id) is None),
            key=lambda s: s.no,
        )

    def apps_sorted(self):
        return sorted(self.apps.values(), key=lambda a: a.id)


# ---------------------------------------------------------------- 규칙 계산
def required_covered(studs) -> int:
    """필수 포함 역할(기획자, 웹 프로그래머) 가운데 '서로 다른 학생'으로 채울 수 있는 수(0~2).

    한 학생이 둘 다 할 수 있어도 하나로만 센다.
    """
    pl = [s for s in studs if s.can(PL)]
    wp = [s for s in studs if s.can(WP)]
    if any(a.id != b.id for a in pl for b in wp):
        return 2
    return 1 if (pl or wp) else 0


def missing_text(studs) -> str:
    has_pl = any(s.can(PL) for s in studs)
    has_wp = any(s.can(WP) for s in studs)
    if not has_pl and not has_wp:
        return "기획자와 웹 프로그래머를 맡을 학생이 없음"
    if not has_pl:
        return "기획자를 맡을 학생이 없음"
    if not has_wp:
        return "웹 프로그래머를 맡을 학생이 없음"
    return "기획자와 웹 프로그래머를 서로 다른 학생이 맡을 수 없음"


def join_problem(studs, s):
    """학생 s가 팀원 studs가 있는 팀에 들어갈 때 어기는 규칙. 없으면 None.

    정원, 주 역할 중복, 필수 포함 역할 자리(어려운 해결)를 검사한다.
    지원할 때와 승인 직전에 같은 함수를 쓴다.
    """
    if len(studs) >= MAX_SIZE:
        return "팀이 꽉 참"
    if any(m.main == s.main for m in studs):
        return "같은 주 역할의 팀원이 있음"
    after = list(studs) + [s]
    if MAX_SIZE - len(after) < len(REQUIRED) - required_covered(after):
        return "필수 포함 역할을 맡을 학생의 자리가 모자람"
    return None


def team_errors(team: Team, studs) -> list[str]:
    """확정할 팀 구성이 2.3절 규칙을 지키는지."""
    errs = []
    if not (MIN_SIZE <= len(studs) <= MAX_SIZE):
        errs.append(f"{team.name}이 {len(studs)}명임(2~4명이어야 함)")
    mains = [s.main for s in studs]
    dup = sorted({m for m in mains if mains.count(m) > 1})
    if dup:
        errs.append(f"{team.name}에 주 역할 중복({', '.join(dup)})")
    if studs and required_covered(studs) < len(REQUIRED):
        errs.append(f"{team.name}에 {missing_text(studs)}")
    return errs


# ---------------------------------------------------------------- 학생 동작
def act_apply(st: State, student_no: str, team_id: int):
    s = st.by_no(student_no)
    if s is None or s.is_admin:
        return "막힘: 학생만 지원할 수 있음", []
    if team_id not in st.teams:
        return "막힘: 없는 팀임", []
    if st.closed:
        return "막힘: 모집이 마감됨", []
    if st.team_of(s.id) is not None:
        return "막힘: 이미 팀이 있음", []
    if st.pending_of(s.id) is not None:
        return "막힘: 결과를 기다리는 지원이 있음", []
    studs = st.team_students(team_id)
    problem = join_problem(studs, s)
    if problem:
        return f"막힘: {problem}", []
    name = st.teams[team_id].name
    if not studs:
        ops = [("insert_app", team_id, s.id, "승인", "빈 팀에 지원"), ("add_member", team_id, s.id)]
        return f"승인: 빈 팀이라 바로 {name}에 들어감", ops
    return f"대기: {name}에 지원함. 지금 팀원 모두의 투표를 기다림", [("insert_app", team_id, s.id, "대기", "")]


def act_vote(st: State, voter_no: str, app_id: int, yes: bool):
    v = st.by_no(voter_no)
    a = st.apps.get(app_id)
    if v is None or v.is_admin or a is None:
        return "막힘: 투표할 수 없음", []
    if a.status != "대기":
        return f"{a.status}: 이미 결과가 나온 지원임", []
    if st.closed:
        return "막힘: 모집이 마감됨", []
    members = st.members.get(a.team_id, [])
    if v.id not in members:
        return "막힘: 그 팀의 팀원만 투표할 수 있음", []
    votes = dict(st.votes.get(a.id, {}))
    if v.id in votes:  # 한 번 투표하면 바꿀 수 없음
        return f"대기: 이미 투표함(내 투표: {votes[v.id]})", []

    applicant = st.students[a.student_id]
    vote = "찬성" if yes else "거부"
    ops = [("insert_vote", a.id, v.id, vote)]
    if not yes:  # 한 명이라도 거부하면 바로 거절
        ops.append(("set_app", a.id, "거절", "팀원이 거부함"))
        return f"거절: 거부하여 {applicant.name}의 지원이 거절됨", ops

    votes[v.id] = vote
    # 투표할 사람은 '지금' 팀원 전원(나중에 들어온 팀원 포함)
    if all(votes.get(m) == "찬성" for m in members):
        problem = join_problem(st.team_students(a.team_id), applicant)  # 승인 직전 재검사
        if problem:
            ops.append(("set_app", a.id, "거절", f"승인 직전 검사: {problem}"))
            return f"거절: 승인 직전 검사에서 걸림({problem})", ops
        ops += [("set_app", a.id, "승인", "팀원 모두 찬성"), ("add_member", a.team_id, a.student_id)]
        return f"승인: 팀원 모두 찬성하여 {applicant.name}이(가) 팀에 들어옴", ops
    return "대기: 찬성함. 다른 팀원의 투표를 기다림", ops


# ---------------------------------------------------------------- 관리자 동작
def act_close(st: State):
    if st.closed:
        return "막힘: 이미 마감됨", []
    ops = [("set_setting", "closed", "1")]
    rejected = []
    for a in st.apps_sorted():
        if a.status == "대기":
            ops.append(("set_app", a.id, "거절", "모집 마감"))
            rejected.append(st.students[a.student_id].name)
    removed = []
    for t in st.teams_sorted():
        ms = st.members.get(t.id, [])
        if len(ms) == 1:
            ops.append(("remove_member", ms[0]))
            removed.append(st.students[ms[0]].name)
    msg = (f"승인: 모집을 마감함 · 거절한 지원: {', '.join(rejected) or '없음'}"
           f" · 팀에서 뺀 학생: {', '.join(removed) or '없음'}")
    return msg, ops


def check_assignment(st: State, pairs) -> list[str]:
    """배정안 검사(2.5절 4항의 다섯 가지). pairs = [(학번, 팀 번호), ...]. 문제가 없으면 []."""
    un = {s.no: s for s in st.unassigned()}
    team_nos = {t.no for t in st.teams.values()}
    errs, seen = [], {}
    for no, tn in pairs:
        if no not in un:
            errs.append(f"{no}은(는) 팀이 없는 학생이 아님")
        elif tn not in team_nos:
            errs.append(f"없는 팀 번호 {tn}")
        elif no in seen and seen[no] != tn:
            errs.append(f"{un[no].name}을(를) 두 팀에 배정함")  # 한 학생은 한 팀에만
        else:
            seen[no] = tn
    missing = [s.name for no, s in un.items() if no not in seen]
    if missing:
        errs.append("배정되지 않은 학생: " + ", ".join(missing))  # 빠짐없이 배정
    if errs:
        return errs
    for t in st.teams_sorted():
        studs = st.team_students(t.id) + [un[no] for no, tn in seen.items() if tn == t.no]
        errs += team_errors(t, studs)  # 인원, 주 역할 중복, 필수 포함 역할
    return errs


def solve(st: State):
    """규칙을 모두 지키는 배정을 모든 경우를 따져(되추적) 찾는다. 없으면 None.

    3차 모델까지 실패했을 때 쓰는 '어려운 해결'이며, AI를 부르기 전 '배정이 가능한가'를 판단하는 데도 쓴다.
    """
    teams = st.teams_sorted()
    groups = {t.id: list(st.team_students(t.id)) for t in teams}
    un = st.unassigned()
    # 맡을 수 있는 팀이 적은 학생부터 놓으면 빨리 찾는다
    def options(s):
        return sum(1 for t in teams if len(groups[t.id]) < MAX_SIZE and all(m.main != s.main for m in groups[t.id]))
    un.sort(key=options)
    chosen = {}

    def need_more():
        return sum(max(0, MIN_SIZE - len(g)) for g in groups.values())

    def bt(i):
        if need_more() > len(un) - i:
            return False
        if i == len(un):
            return all(not team_errors(t, groups[t.id]) for t in teams)
        s = un[i]
        for t in sorted(teams, key=lambda t: (len(groups[t.id]), t.no)):  # 적은 팀부터 → 고르게
            g = groups[t.id]
            if len(g) >= MAX_SIZE or any(m.main == s.main for m in g):
                continue
            g.append(s)
            chosen[s.no] = t.no
            if MAX_SIZE - len(g) >= len(REQUIRED) - required_covered(g) and bt(i + 1):
                return True
            g.pop()
            del chosen[s.no]
        return False

    if not bt(0):
        return None
    return sorted(chosen.items())


def infeasible_reasons(st: State) -> list[str]:
    """규칙을 지키는 배정이 없을 때 관리자에게 보여 줄 이유."""
    teams = st.teams_sorted()
    un = st.unassigned()
    total = sum(len(st.members.get(t.id, [])) for t in teams) + len(un)
    reasons = []
    if total < MIN_SIZE * len(teams):
        reasons.append(f"학생 {total}명으로는 팀 {len(teams)}개를 모두 2명 이상으로 채울 수 없음")
    if total > MAX_SIZE * len(teams):
        reasons.append(f"학생 {total}명을 팀 {len(teams)}개에 4명 이하로 나눌 수 없음")
    for t in teams:
        base = st.team_students(t.id)
        room = MAX_SIZE - len(base)
        possible = any(
            not team_errors(t, base + list(c))
            for k in range(0, max(room, 0) + 1)
            for c in combinations(un, k)
        )
        if possible:
            continue
        cand = base + [s for s in un if all(m.main != s.main for m in base)] if room > 0 else base
        if not any(s.can(WP) for s in cand):
            reasons.append(f"{t.name}에 웹 프로그래머를 맡을 학생이 없음")
        elif not any(s.can(PL) for s in cand):
            reasons.append(f"{t.name}에 기획자를 맡을 학생이 없음")
        else:
            reasons.append(f"{t.name}을(를) 규칙에 맞게 채울 수 없음")
    if not reasons:
        reasons.append("팀마다 따로는 가능하지만 모든 팀의 조건을 한꺼번에 맞추는 조합이 없음")
    return reasons


def act_confirm(st: State, pairs):
    if not st.closed:
        return "막힘: 모집을 마감한 뒤 확정할 수 있음", []
    if pairs is None:
        return "막힘: 확정할 배정안이 없음(먼저 AI 배정을 누름)", []
    pairs = sorted(dict(pairs).items())
    errs = check_assignment(st, pairs)
    if errs:
        return f"막힘: 배정안이 지금 상태와 맞지 않음({errs[0]})", []
    ops = [("add_member", st.team_by_no(tn).id, st.by_no(no).id) for no, tn in pairs]
    ops += [("set_setting", "proposal", ""), ("set_setting", "confirmed", "1")]
    return "승인: 배정을 확정해 DB에 저장함", ops


# ---------------------------------------------------------------- 명단 CSV
def parse_roster(text: str) -> list[dict]:
    """명단 CSV(utf-8-sig로 디코딩한 글)를 검사하고 학생 목록으로 바꾼다. 틀리면 ValueError."""
    reader = csv.reader(io.StringIO(text.lstrip("\ufeff")))
    header = next(reader, None)
    if not header or [h.strip() for h in header] != ["student_no", "name", "main_role", "sub_roles"]:
        raise ValueError("머리글이 student_no,name,main_role,sub_roles 가 아님")
    rows, seen = [], set()
    for line_no, row in enumerate(reader, start=2):
        if not row or all(not c.strip() for c in row):
            continue
        if len(row) != 4:
            raise ValueError(f"{line_no}번째 줄의 열이 4개가 아님")
        no, name, main, subs_raw = (c.strip() for c in row)
        if not no or not name:
            raise ValueError(f"{line_no}번째 줄에 학번이나 이름이 비어 있음")
        if no.lower() == "admin":
            raise ValueError("학번 admin은 관리자용이라 쓸 수 없음")
        if no in seen:
            raise ValueError(f"학번 {no}이(가) 두 번 나옴")
        if main not in ROLES:
            raise ValueError(f"{line_no}번째 줄의 주 역할 '{main}'이(가) 6개 역할에 없음")
        subs = tuple(r.strip() for r in subs_raw.split(";") if r.strip())
        bad = [r for r in subs if r not in ROLES]
        if bad:
            raise ValueError(f"{line_no}번째 줄의 보조 역할 '{bad[0]}'이(가) 6개 역할에 없음")
        if len(subs) > 2:
            raise ValueError(f"{line_no}번째 줄의 보조 역할이 2개보다 많음")
        seen.add(no)
        rows.append({"no": no, "name": name, "main": main, "subs": tuple(r for r in subs if r != main)})
    if not rows:
        raise ValueError("명단에 학생이 없음")
    return rows


# ---------------------------------------------------------------- 시험용: ops를 메모리 상태에 적용
def apply_ops_in_memory(st: State, ops) -> None:
    for op in ops:
        kind = op[0]
        if kind == "insert_app":
            _, tid, sid, status, reason = op
            new_id = max(st.apps, default=0) + 1
            st.apps[new_id] = App(new_id, tid, sid, status, reason)
        elif kind == "set_app":
            _, aid, status, reason = op
            st.apps[aid].status, st.apps[aid].reason = status, reason
        elif kind == "add_member":
            _, tid, sid = op
            ms = st.members.setdefault(tid, [])
            assert len(ms) < MAX_SIZE and st.team_of(sid) is None, "DB 제약 위반"
            ms.append(sid)
        elif kind == "remove_member":
            for ms in st.members.values():
                if op[1] in ms:
                    ms.remove(op[1])
        elif kind == "insert_vote":
            _, aid, vid, vote = op
            assert vid not in st.votes.get(aid, {}), "DB 기본 키 위반"
            st.votes.setdefault(aid, {})[vid] = vote
        elif kind == "set_setting":
            st.settings[op[1]] = op[2]
        else:
            raise ValueError(kind)
