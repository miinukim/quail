import datetime
from zoneinfo import ZoneInfo

from celery import current_app
from django.db import transaction
from django.utils import timezone

from .models import ExperimentRun, ExperimentSchedule


def normalize_schedule_type(value):
    raw = str(value or "NOW").upper()
    if raw in {"NOW", "TIMED", "RECURRING"}:
        return raw
    return "NOW"


def normalize_timezone_name(value):
    name = str(value or "").strip() or "UTC"
    try:
        ZoneInfo(name)
        return name
    except Exception:
        return "UTC"


def parse_client_datetime(value, timezone_name="UTC"):
    if value in (None, ""):
        return None
    if isinstance(value, datetime.datetime):
        dt = value
    else:
        raw = str(value).strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.datetime.fromisoformat(raw)
    if timezone.is_naive(dt):
        dt = dt.replace(tzinfo=ZoneInfo(normalize_timezone_name(timezone_name)))
    return dt.astimezone(datetime.timezone.utc)


def parse_local_time(value):
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("time is required")
    parts = raw.split(":")
    if len(parts) not in (2, 3):
        raise ValueError("time must be HH:MM or HH:MM:SS")
    hh, mm = int(parts[0]), int(parts[1])
    ss = int(parts[2]) if len(parts) == 3 else 0
    if hh < 0 or hh > 23 or mm < 0 or mm > 59 or ss < 0 or ss > 59:
        raise ValueError("time is out of range")
    return datetime.time(hour=hh, minute=mm, second=ss)


def normalize_recurrence(payload, timezone_name="UTC"):
    data = payload if isinstance(payload, dict) else {}
    kind = str(data.get("kind") or "interval").strip().lower()
    tz_name = normalize_timezone_name(data.get("timezone") or timezone_name)
    if kind == "interval":
        interval_min = int(data.get("interval_min") or 0)
        if interval_min <= 0:
            raise ValueError("interval_min must be a positive integer")
        start_immediately = bool(data.get("start_immediately", True))
        start_at = parse_client_datetime(data.get("start_at"), tz_name) if data.get("start_at") else None
        return {
            "kind": "interval",
            "interval_min": interval_min,
            "start_immediately": start_immediately,
            "start_at": start_at.isoformat() if start_at else None,
            "timezone": tz_name,
        }
    if kind == "daily":
        at_time = parse_local_time(data.get("time"))
        every_n_days = int(data.get("every_n_days") or 1)
        if every_n_days <= 0:
            raise ValueError("every_n_days must be a positive integer")
        start_date = str(data.get("start_date") or "").strip() or None
        if start_date:
            datetime.date.fromisoformat(start_date)
        return {
            "kind": "daily",
            "time": at_time.strftime("%H:%M:%S"),
            "every_n_days": every_n_days,
            "start_date": start_date,
            "timezone": tz_name,
        }
    if kind == "weekly":
        at_time = parse_local_time(data.get("time"))
        weekdays = data.get("weekdays")
        if not isinstance(weekdays, list):
            raise ValueError("weekdays must be a list")
        normalized_days = sorted({int(day) for day in weekdays})
        if not normalized_days or any(day < 0 or day > 6 for day in normalized_days):
            raise ValueError("weekdays must use 0-6 (Mon-Sun)")
        every_n_weeks = int(data.get("every_n_weeks") or 1)
        if every_n_weeks <= 0:
            raise ValueError("every_n_weeks must be a positive integer")
        start_date = str(data.get("start_date") or "").strip() or None
        if start_date:
            datetime.date.fromisoformat(start_date)
        return {
            "kind": "weekly",
            "time": at_time.strftime("%H:%M:%S"),
            "weekdays": normalized_days,
            "every_n_weeks": every_n_weeks,
            "start_date": start_date,
            "timezone": tz_name,
        }
    raise ValueError(f"Unsupported recurrence kind: {kind}")


def recurrence_summary(recurrence):
    data = recurrence if isinstance(recurrence, dict) else {}
    kind = data.get("kind")
    if kind == "interval":
        interval = data.get("interval_min")
        return f"Every {interval} minute(s)"
    if kind == "daily":
        return f"Daily at {data.get('time', '--:--')}"
    if kind == "weekly":
        labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        days = ",".join(labels[day] for day in data.get("weekdays", []) if 0 <= day < 7)
        return f"Weekly {days} at {data.get('time', '--:--')}".strip()
    return ""


def compute_next_run_at(recurrence, after_utc=None):
    data = recurrence if isinstance(recurrence, dict) else {}
    kind = data.get("kind")
    now_utc = (after_utc or timezone.now()).astimezone(datetime.timezone.utc)
    tz = ZoneInfo(normalize_timezone_name(data.get("timezone")))

    if kind == "interval":
        interval = datetime.timedelta(minutes=int(data["interval_min"]))
        start_at = parse_client_datetime(data.get("start_at"), data.get("timezone")) if data.get("start_at") else None
        if start_at and start_at > now_utc:
            return start_at
        if data.get("start_immediately", True) and not start_at:
            return now_utc
        base = start_at or now_utc
        if base > now_utc:
            return base
        elapsed = now_utc - base
        steps = int(elapsed.total_seconds() // interval.total_seconds()) + 1
        return base + interval * steps

    local_now = now_utc.astimezone(tz)

    if kind == "daily":
        time_value = parse_local_time(data["time"])
        every_n_days = int(data.get("every_n_days") or 1)
        anchor = datetime.date.fromisoformat(data["start_date"]) if data.get("start_date") else local_now.date()
        candidate_date = max(local_now.date(), anchor)
        while True:
            delta_days = (candidate_date - anchor).days
            if delta_days >= 0 and delta_days % every_n_days == 0:
                candidate_local = datetime.datetime.combine(candidate_date, time_value, tzinfo=tz)
                if candidate_local > local_now:
                    return candidate_local.astimezone(datetime.timezone.utc)
            candidate_date += datetime.timedelta(days=1)

    if kind == "weekly":
        time_value = parse_local_time(data["time"])
        weekdays = {int(day) for day in data.get("weekdays", [])}
        every_n_weeks = int(data.get("every_n_weeks") or 1)
        anchor = datetime.date.fromisoformat(data["start_date"]) if data.get("start_date") else local_now.date()
        anchor_monday = anchor - datetime.timedelta(days=anchor.weekday())
        for offset in range(0, 366 * 3):
            candidate_date = local_now.date() + datetime.timedelta(days=offset)
            if candidate_date.weekday() not in weekdays:
                continue
            week_start = candidate_date - datetime.timedelta(days=candidate_date.weekday())
            week_index = (week_start - anchor_monday).days // 7
            if week_index < 0 or week_index % every_n_weeks != 0:
                continue
            candidate_local = datetime.datetime.combine(candidate_date, time_value, tzinfo=tz)
            if candidate_local > local_now:
                return candidate_local.astimezone(datetime.timezone.utc)
        raise ValueError("Could not compute next weekly run time")

    raise ValueError("Unsupported recurrence")


def revoke_task(task_id):
    if not task_id:
        return
    try:
        current_app.control.revoke(task_id)
    except Exception:
        return


def create_run_record(*, user, script_path, arguments, name, priority, schedule_type, scheduled_at=None, interval_min=None, schedule=None):
    return ExperimentRun.objects.create(
        script_path=script_path,
        script=None,
        arguments=arguments or {},
        user=user,
        status='QUEUED',
        name=name or "",
        priority=priority,
        schedule_type=schedule_type,
        scheduled_at=scheduled_at,
        interval_min=interval_min,
        schedule=schedule,
    )


def ensure_next_recurring_run(schedule, *, after_utc=None):
    if schedule.status != "ACTIVE":
        return None
    queued_qs = schedule.runs.filter(status='QUEUED')
    if after_utc is not None:
        queued_qs = queued_qs.filter(scheduled_at__gt=after_utc)
    queued = queued_qs.order_by('scheduled_at', 'id').first()
    if queued is not None:
        return queued
    next_run_at = compute_next_run_at(schedule.recurrence, after_utc=after_utc)
    run = create_run_record(
        user=schedule.user,
        script_path=schedule.script_path,
        arguments=schedule.arguments,
        name=schedule.name,
        priority=schedule.priority,
        schedule_type='RECURRING',
        scheduled_at=next_run_at,
        interval_min=schedule.recurrence.get("interval_min"),
        schedule=schedule,
    )
    schedule.next_run_at = next_run_at
    schedule.save(update_fields=['next_run_at', 'updated_at'])
    return run


def cancel_schedule(schedule, *, mark_failed=False):
    with transaction.atomic():
        schedule.refresh_from_db()
        if schedule.status not in {"ACTIVE", "FAILED"}:
            return schedule
        schedule.status = "FAILED" if mark_failed else "CANCELLED"
        schedule.next_run_at = None
        schedule.save(update_fields=['status', 'next_run_at', 'updated_at'])
        queued_runs = list(schedule.runs.filter(status='QUEUED'))
        for run in queued_runs:
            revoke_task(run.dispatch_task_id)
            run.status = 'CANCELLED' if not mark_failed else 'FAILED'
            run.completed_at = timezone.now()
            run.save(update_fields=['status', 'completed_at'])
        return schedule


def create_first_recurring_run(schedule):
    from .tasks import launch_scheduled_run_task

    recurrence = schedule.recurrence or {}
    first_at = parse_client_datetime(recurrence.get('start_at'), schedule.timezone) if recurrence.get('start_at') else None
    if first_at is None:
        first_at = compute_next_run_at(recurrence, after_utc=timezone.now())
    run = create_run_record(
        user=schedule.user,
        script_path=schedule.script_path,
        arguments=schedule.arguments,
        name=schedule.name,
        priority=schedule.priority,
        schedule_type='RECURRING',
        scheduled_at=first_at,
        interval_min=recurrence.get('interval_min'),
        schedule=schedule,
    )
    schedule.next_run_at = first_at
    schedule.save(update_fields=['next_run_at', 'updated_at'])
    task = launch_scheduled_run_task.apply_async((run.id,), eta=first_at)
    run.dispatch_task_id = task.id
    run.save(update_fields=['dispatch_task_id'])
    return run


def schedule_run(*, user, script_path, arguments, name, priority, schedule_type, timezone_name="UTC", scheduled_at=None, recurrence=None, panel=None):
    from .tasks import launch_scheduled_run_task, run_experiment_task

    normalized_type = normalize_schedule_type(schedule_type)
    if normalized_type == 'NOW':
        run = create_run_record(
            user=user,
            script_path=script_path,
            arguments=arguments,
            name=name,
            priority=priority,
            schedule_type='NOW',
        )
        task = run_experiment_task.delay(run.id)
        run.dispatch_task_id = task.id
        run.save(update_fields=['dispatch_task_id'])
        return {"status": run.status, "rid": run.id}, run

    if normalized_type == 'TIMED':
        run_at = parse_client_datetime(scheduled_at, timezone_name)
        if run_at is None:
            raise ValueError("scheduled_at is required for TIMED runs")
        run = create_run_record(
            user=user,
            script_path=script_path,
            arguments=arguments,
            name=name,
            priority=priority,
            schedule_type='TIMED',
            scheduled_at=run_at,
        )
        task = launch_scheduled_run_task.apply_async((run.id,), eta=run_at)
        run.dispatch_task_id = task.id
        run.save(update_fields=['dispatch_task_id'])
        return {"status": "SCHEDULED", "rid": run.id}, run

    normalized_recurrence = normalize_recurrence(recurrence, timezone_name)
    schedule = ExperimentSchedule.objects.create(
        panel=panel,
        user=user,
        name=name,
        script_path=script_path,
        arguments=arguments,
        priority=priority,
        schedule_type='RECURRING',
        timezone=normalized_recurrence.get('timezone', timezone_name),
        recurrence=normalized_recurrence,
    )
    run = create_first_recurring_run(schedule)
    return {"status": "SCHEDULED", "rid": run.id, "schedule_id": schedule.schedule_id}, run
