-- 팀 편성 앱의 테이블 (모두 tf_ 로 시작)
-- 앱이 시작할 때 이 파일을 그대로 실행한다(IF NOT EXISTS라서 여러 번 실행해도 안전함).
-- Supabase SQL 편집기에 붙여 넣어 직접 만들어도 된다.

create table if not exists tf_students (
    id            serial primary key,
    student_no    text not null unique,                 -- 학번(유일). 관리자는 'admin'
    name          text not null,
    main_role     text check (main_role in ('기획자', '웹 프로그래머', '프론트엔드 디자이너',
                                            '테스터', '보고서 작성자', '발표자')),  -- 관리자는 null
    password_hash text not null,                        -- bcrypt 해시(원래 비밀번호는 저장하지 않음)
    is_admin      boolean not null default false
);

create table if not exists tf_student_sub_roles (
    student_id integer not null references tf_students(id) on delete cascade,
    role       text not null check (role in ('기획자', '웹 프로그래머', '프론트엔드 디자이너',
                                             '테스터', '보고서 작성자', '발표자')),
    primary key (student_id, role)
);

create table if not exists tf_teams (
    id      serial primary key,
    team_no integer not null unique,                    -- 팀1, 팀2 …의 번호
    name    text not null unique
);

-- 팀원. (team_id, slot_no) 기본 키 + student_id 유일 + slot_no 1~4
--  → 마지막 자리에 두 명이 동시에 들어오면 먼저 도착한 학생만 기록되고,
--    한 학생은 한 팀에만 들어가며, 다섯 번째 자리는 생기지 않음
create table if not exists tf_members (
    team_id    integer not null references tf_teams(id) on delete cascade,
    slot_no    integer not null check (slot_no between 1 and 4),
    student_id integer not null unique references tf_students(id) on delete cascade,
    primary key (team_id, slot_no)
);

create table if not exists tf_applications (
    id         serial primary key,
    team_id    integer not null references tf_teams(id) on delete cascade,
    student_id integer not null references tf_students(id) on delete cascade,
    status     text not null check (status in ('대기', '승인', '거절')),
    reason     text,
    created_at timestamptz not null default now(),
    decided_at timestamptz
);

-- 한 학생은 결과를 기다리는 지원을 하나만 가질 수 있음
create unique index if not exists tf_applications_one_pending
    on tf_applications (student_id) where status = '대기';

-- 투표. 한 지원에 한 사람이 한 번만(바꿀 수 없음)
create table if not exists tf_votes (
    application_id integer not null references tf_applications(id) on delete cascade,
    voter_id       integer not null references tf_students(id) on delete cascade,
    vote           text not null check (vote in ('찬성', '거부')),
    created_at     timestamptz not null default now(),
    primary key (application_id, voter_id)
);

-- 마감 여부, 배정안, 배정 진행 기록 등
create table if not exists tf_settings (
    key   text primary key,
    value text
);

-- AI 호출 기록(README의 실패 사례 근거로 씀)
create table if not exists tf_ai_logs (
    id         serial primary key,
    created_at timestamptz not null default now(),
    attempt    integer,
    model      text,
    outcome    text,          -- 통과, 시간 초과, HTTP 429, 빈 응답, 검사 실패 …
    detail     text,
    seconds    real
);
