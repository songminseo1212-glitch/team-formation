"""연습용 시나리오 3개(공고 부록)를 DB 없이 engine만으로 돌려 보는 시험.

실행: python tests/test_engine.py   (저장소 맨 위 폴더에서)
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import engine as E  # noqa: E402


def make_state(csv_name, n_teams):
    with open(os.path.join(ROOT, "data", csv_name), encoding="utf-8-sig") as f:
        rows = E.parse_roster(f.read())
    st = E.State()
    for i, r in enumerate(rows, start=1):
        st.students[i] = E.Student(i, r["no"], r["name"], r["main"], r["subs"])
    for n in range(1, n_teams + 1):
        st.teams[100 + n] = E.Team(100 + n, n, f"팀{n}")
    return st


class Runner:
    def __init__(self, st, prefix):
        self.st, self.prefix, self.fail = st, prefix, 0

    def no(self, k):
        return f"{self.prefix}{k:02d}"

    def check(self, label, msg, expect):
        ok = msg.split(":")[0] == expect
        self.fail += not ok
        print(f"  {'OK ' if ok else 'XX '} {label:<26} 기대={expect:<3} 결과={msg}")

    def apply(self, k, team_no, expect):
        t = self.st.team_by_no(team_no)
        msg, ops = E.act_apply(self.st, self.no(k), t.id)
        E.apply_ops_in_memory(self.st, ops)
        self.check(f"학생{k} → 팀{team_no} 지원", msg, expect)

    def vote(self, voter, applicant, yes, expect):
        sid = self.st.by_no(self.no(applicant)).id
        app = self.st.pending_of(sid)
        assert app is not None, f"학생{applicant}의 대기 지원 없음"
        msg, ops = E.act_vote(self.st, self.no(voter), app.id, yes)
        E.apply_ops_in_memory(self.st, ops)
        self.check(f"학생{voter} {'찬성' if yes else '거부'} → 학생{applicant}", msg, expect)

    def close(self):
        msg, ops = E.act_close(self.st)
        E.apply_ops_in_memory(self.st, ops)
        self.check("관리자 마감", msg, "승인")
        return msg

    def assign_and_confirm(self):
        pairs = E.solve(self.st)
        if pairs is None:
            print("  XX  배정 없음:", E.infeasible_reasons(self.st))
            self.fail += 1
            return
        errs = E.check_assignment(self.st, pairs)
        self.fail += bool(errs)
        print(f"  {'OK ' if not errs else 'XX '} 앱 배정안 검사 통과: {pairs}")
        msg, ops = E.act_confirm(self.st, pairs)
        E.apply_ops_in_memory(self.st, ops)
        self.check("관리자 확정", msg, "승인")
        for t in self.st.teams_sorted():
            studs = self.st.team_students(t.id)
            errs = E.team_errors(t, studs)
            self.fail += bool(errs)
            print(f"      {t.name}: {', '.join(s.name for s in studs)} {'' if not errs else errs}")


def easy():
    print("부록 1. 쉬움")
    r = Runner(make_state("쉬움.csv", 3), "20261")
    r.apply(1, 1, "승인"); r.apply(2, 2, "승인"); r.apply(3, 3, "승인")
    r.apply(4, 1, "대기"); r.vote(1, 4, True, "승인")
    r.apply(5, 2, "대기"); r.vote(2, 5, True, "승인")
    r.apply(7, 1, "대기"); r.vote(1, 7, True, "대기"); r.vote(4, 7, False, "거절")
    r.apply(6, 3, "대기"); r.vote(3, 6, True, "승인")
    r.close(); r.assign_and_confirm()
    return r.fail


def middle():
    print("부록 2. 중간")
    r = Runner(make_state("중간.csv", 4), "20262")
    r.apply(1, 1, "승인")
    r.apply(5, 1, "대기"); r.vote(1, 5, True, "승인")
    r.apply(2, 1, "막힘")  # 주 역할 중복 (막혔으니 투표는 건너뜀)
    r.apply(6, 2, "승인")
    r.apply(9, 2, "대기"); r.vote(6, 9, True, "승인")
    r.apply(10, 2, "대기"); r.vote(6, 10, True, "대기"); r.vote(9, 10, False, "거절")
    r.apply(3, 3, "승인"); r.apply(11, 4, "승인")
    r.apply(12, 4, "대기"); r.apply(12, 3, "막힘")
    msg = r.close()
    assert "학생12" in msg and "학생3" in msg and "학생11" in msg, msg
    r.apply(2, 3, "막힘")  # 마감
    r.assign_and_confirm()
    return r.fail


def hard():
    print("부록 3. 어려움")
    r = Runner(make_state("어려움.csv", 4), "20263")
    r.apply(1, 1, "승인")
    r.apply(4, 1, "대기"); r.vote(1, 4, True, "승인")
    r.apply(7, 1, "대기"); r.vote(1, 7, True, "대기"); r.vote(4, 7, True, "승인")
    r.apply(2, 2, "승인")
    r.apply(8, 2, "대기"); r.vote(2, 8, True, "승인")
    r.apply(5, 2, "대기"); r.apply(6, 2, "대기")
    r.vote(2, 5, True, "대기"); r.vote(8, 5, True, "승인")
    r.vote(2, 6, True, "대기"); r.vote(8, 6, True, "대기"); r.vote(5, 6, True, "거절")  # 승인 직전 검사
    r.apply(11, 3, "승인")
    r.apply(15, 3, "대기"); r.vote(11, 15, True, "승인")
    r.apply(14, 3, "막힘")  # 필수 포함 역할 자리
    r.apply(10, 4, "승인")
    r.apply(13, 1, "대기"); r.vote(1, 13, True, "대기")
    msg = r.close()
    assert "학생13" in msg and "학생10" in msg, msg
    r.assign_and_confirm()
    return r.fail


def impossible():
    print("추가: 규칙을 지키는 배정이 없는 경우")
    st = make_state("쉬움.csv", 3)
    # 팀1을 웹 프로그래머를 못 맡는 학생들로 채움(1, 7, 8) → 남은 자리 1개
    # 학생4·5·6(웹 프로그래머)은 이미 다른 팀에 넣어 두어, 팀1에 웹 프로그래머가 올 수 없게 만듦
    for k in (1, 7, 8):
        st.members.setdefault(101, []).append(st.by_no(f"20261{k:02d}").id)
    for k, tid in ((2, 102), (4, 102), (3, 103), (5, 103)):
        st.members.setdefault(tid, []).append(st.by_no(f"20261{k:02d}").id)
    st.settings["closed"] = "1"  # 남은 학생: 학생6(WP;TT)뿐 → 팀1에 넣을 수 있음. 그래서 학생6도 팀2에 넣음
    st.members[102].append(st.by_no("2026106").id)
    sol = E.solve(st)
    reasons = E.infeasible_reasons(st)
    ok = sol is None and any("팀1" in r for r in reasons)
    print(f"  {'OK ' if ok else 'XX '} 배정 없음, 이유: {reasons}")
    return 0 if ok else 1


if __name__ == "__main__":
    fails = easy() + middle() + hard() + impossible()
    print("\n결과:", "모두 통과" if fails == 0 else f"{fails}개 실패")
    sys.exit(1 if fails else 0)
