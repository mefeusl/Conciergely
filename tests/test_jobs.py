from datetime import timedelta

from app import db, jobs

A, B = "+905551111111", "+905552222222"


def test_enqueue_and_claim_in_order():
    first = jobs.enqueue("inbound", A, {"n": 1})
    second = jobs.enqueue("send", B, {"n": 2})
    j = jobs.claim()
    assert j["id"] == first and j["payload"] == {"n": 1} and j["attempts"] == 1
    assert jobs.claim()["id"] == second
    assert jobs.claim() is None


def test_duplicate_dedup_key_is_ignored():
    assert jobs.enqueue("inbound", A, {}, dedup_key="in:wamid.1") is not None
    assert jobs.enqueue("inbound", A, {}, dedup_key="in:wamid.1") is None
    assert jobs.counts()["pending"] == 1


def test_inbound_of_one_client_is_strictly_ordered():
    one = jobs.enqueue("inbound", A, {"n": 1})
    two = jobs.enqueue("inbound", A, {"n": 2})
    other = jobs.enqueue("inbound", B, {"n": 3})
    assert jobs.claim()["id"] == one
    # A's first job is running, so A's second must wait, but B can go ahead.
    assert jobs.claim()["id"] == other
    assert jobs.claim() is None
    jobs.complete(one)
    assert jobs.claim()["id"] == two


def test_failed_job_blocks_later_inbound_until_retry_time():
    one = jobs.enqueue("inbound", A, {"n": 1})
    jobs.enqueue("inbound", A, {"n": 2})
    jobs.claim()
    assert jobs.fail(one, "boom") == "pending"
    assert jobs.claim() is None  # job 1 waits 10 s and job 2 must not jump ahead


def test_retry_backoff_then_dead_on_fifth_failure():
    job_id = jobs.enqueue("send", A, {})
    expected = [10, 60, 300, 900]
    for attempt in range(1, 5):
        # make the job ready again, then claim it
        with db.conn() as c:
            c.execute("UPDATE jobs SET run_at=? WHERE id=?", (db.now_utc_iso(), job_id))
        j = jobs.claim()
        assert j["attempts"] == attempt
        assert jobs.fail(job_id, "boom") == "pending"
        with db.conn() as c:
            run_at = c.execute("SELECT run_at FROM jobs WHERE id=?", (job_id,)).fetchone()["run_at"]
        delay = (db.from_iso(run_at) - db.now_utc()).total_seconds()
        assert abs(delay - expected[attempt - 1]) <= 2
    with db.conn() as c:
        c.execute("UPDATE jobs SET run_at=? WHERE id=?", (db.now_utc_iso(), job_id))
    assert jobs.claim()["attempts"] == 5
    assert jobs.fail(job_id, "boom") == "dead"
    assert jobs.counts()["dead"] == 1


def test_permanent_failure_goes_dead_immediately_and_can_be_retried():
    job_id = jobs.enqueue("send", A, {})
    jobs.claim()
    assert jobs.fail(job_id, "token expired", permanent=True) == "dead"
    assert jobs.list_jobs("dead")[0]["last_error"] == "token expired"
    assert jobs.retry_dead(job_id) is True
    j = jobs.claim()
    assert j["id"] == job_id and j["attempts"] == 1
    assert jobs.retry_dead(job_id) is False  # not dead any more


def test_requeue_stale_running_jobs_after_a_crash():
    job_id = jobs.enqueue("inbound", A, {})
    jobs.claim()
    assert jobs.claim() is None
    assert jobs.requeue_stale(older_than_seconds=0) == 1
    assert jobs.claim()["id"] == job_id


def test_future_jobs_wait_for_their_time():
    jobs.enqueue("send", A, {}, delay_seconds=3600)
    assert jobs.claim() is None


def test_recent_count_and_purge():
    for _ in range(3):
        jobs.enqueue("inbound", A, {})
    assert jobs.recent_count(A, "inbound") == 3
    assert jobs.recent_count(B, "inbound") == 0
    job_id = jobs.claim()["id"]
    jobs.complete(job_id)
    with db.conn() as c:
        old = db.to_iso(db.now_utc() - timedelta(days=30))
        c.execute("UPDATE jobs SET updated_at=? WHERE id=?", (old, job_id))
    assert jobs.purge_old(days=7) == 1
