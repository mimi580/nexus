from sqlalchemy import func, select

from app.core.context import build_context
from app.database.models import ScheduledJob, Task
from app.orchestrator.orchestrator import Orchestrator
from app.scheduler.scheduler import Scheduler


def test_running_tasks_are_requeued_after_a_restart(ctx):
    objective = ctx.tasks.create_objective("restart test")
    task, _ = ctx.tasks.create_task(agent="market_research", objective_id=objective.id, task_input={"product_category": "refurbished_laptop"})
    ctx.tasks.mark_running(task)
    assert task.status == "running"

    # Simulate a process restart with a fresh context over the same database.
    fresh = build_context(ctx.session, ctx.settings, clock=ctx.clock)
    recovered = Orchestrator(fresh).recover()
    assert recovered == 1
    assert ctx.session.get(Task, task.id).status == "pending"


def test_scheduler_jobs_are_idempotent_within_a_window(ctx):
    scheduler = Scheduler(ctx)
    first = scheduler.run_due()
    second = scheduler.run_due()
    assert first != []
    assert second == []  # nothing is due twice in the same window


def test_scheduler_survives_a_failing_job(ctx):
    def broken(_ctx):
        raise RuntimeError("job exploded")

    scheduler = Scheduler(ctx, jobs={"broken": (broken, 60), "ok": (lambda c: {"fine": True}, 60)})
    results = scheduler.run_due()
    statuses = {r["job"]: r["status"] for r in results}
    assert statuses == {"broken": "error", "ok": "ok"}
    row = ctx.session.scalar(select(ScheduledJob).where(ScheduledJob.name == "broken"))
    assert row.next_run_at > ctx.now


def test_emergency_stop_prevents_scheduled_work(ctx):
    ctx.memory.set_control_state(emergency_stop=True)
    assert Scheduler(ctx).run_due() == []


def test_duplicate_tasks_are_not_created_for_the_same_key(ctx):
    objective = ctx.tasks.create_objective("dedupe")
    for _ in range(3):
        ctx.tasks.create_task(
            agent="market_research", objective_id=objective.id,
            task_input={"product_category": "used_iphone"}, idempotency_key="same-key",
        )
    assert ctx.session.scalar(select(func.count()).select_from(Task)) == 1
