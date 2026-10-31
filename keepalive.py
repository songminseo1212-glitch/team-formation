"""DB(Supabase)가 일시정지되지 않도록 하루 한 번 질의를 보낸다.

GitHub Actions(.github/workflows/keepalive.yml)가 매일 실행함(외부 예약 서비스).
DATABASE_URL은 GitHub 저장소의 Secrets에서 받으며, 값은 출력하지 않는다.
"""
import os
import sys

import psycopg2

dsn = os.getenv("DATABASE_URL")
if not dsn:
    print("DATABASE_URL: 없음")
    sys.exit(1)
with psycopg2.connect(dsn, connect_timeout=15) as conn, conn.cursor() as cur:
    cur.execute("select count(*) from tf_students")
    print("DB 질의 성공, 학생 수:", cur.fetchone()[0])
