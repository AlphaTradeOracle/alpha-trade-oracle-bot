"""Datenzugriff fuer das Ereignisprotokoll und geplante Jobs."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import EventSeverity
from app.core.logging import get_correlation_id
from app.core.time import ensure_utc, utc_now
from app.models.operations import ApplicationEvent, ScheduledJob


class EventRepository:
    """Fachliches Audit-Log. Enthaelt nie Secrets."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        event_type: str,
        message: str,
        *,
        severity: EventSeverity = EventSeverity.INFO,
        payload: dict[str, Any] | None = None,
    ) -> ApplicationEvent:
        event = ApplicationEvent(
            event_type=event_type,
            severity=severity.value,
            message=message[:4000],
            correlation_id=get_correlation_id(),
            payload=payload,
        )
        self._session.add(event)
        await self._session.flush()
        return event

    async def list_recent(
        self, *, event_type: str | None = None, limit: int = 50
    ) -> list[ApplicationEvent]:
        statement = select(ApplicationEvent).order_by(ApplicationEvent.created_at.desc())
        if event_type:
            statement = statement.where(ApplicationEvent.event_type == event_type)
        result = await self._session.execute(statement.limit(limit))
        return list(result.scalars())


class ScheduledJobRepository:
    """Zustandsverwaltung der Hintergrundjobs.

    :meth:`claim` macht Jobs idempotent: laeuft ein zweiter Worker parallel,
    faellt sein Aufruf durch, weil ``next_run_at`` noch in der Zukunft liegt.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, job_key: str) -> ScheduledJob | None:
        result = await self._session.execute(
            select(ScheduledJob).where(ScheduledJob.job_key == job_key)
        )
        return result.scalar_one_or_none()

    async def register(self, job_key: str, job_type: str, interval_seconds: int) -> ScheduledJob:
        existing = await self.get(job_key)
        if existing is not None:
            if existing.interval_seconds != interval_seconds:
                existing.interval_seconds = interval_seconds
                # Neues Intervall soll nicht hinter einem alten next_run_at blockieren.
                existing.next_run_at = utc_now()
            elif existing.last_run_at is None:
                # Job nie gelaufen (z. B. nach Intervall-Wechsel 60m -> 30m): sofort faehig machen.
                existing.next_run_at = utc_now()
            return existing

        job = ScheduledJob(
            job_key=job_key,
            job_type=job_type,
            interval_seconds=interval_seconds,
            next_run_at=utc_now(),
        )
        self._session.add(job)
        await self._session.flush()
        return job

    async def claim(self, job_key: str) -> bool:
        """Ausfuehrungsrecht beanspruchen.

        Gibt ``False`` zurueck, wenn der Job deaktiviert ist oder sein Intervall
        noch nicht abgelaufen ist. ``FOR UPDATE`` verhindert, dass zwei Worker
        denselben Job gleichzeitig beanspruchen.
        """
        result = await self._session.execute(
            select(ScheduledJob).where(ScheduledJob.job_key == job_key).with_for_update()
        )
        job = result.scalar_one_or_none()
        if job is None or not job.is_enabled:
            return False

        now = utc_now()
        # ensure_utc, weil nicht jeder Treiber den Zeitzonenanteil zurueckliefert.
        if job.next_run_at is not None and ensure_utc(job.next_run_at) > now:
            return False

        job.last_run_at = now
        job.next_run_at = now + timedelta(seconds=job.interval_seconds)
        job.run_count += 1
        job.last_status = "running"
        return True

    async def complete(self, job_key: str, *, success: bool, error: str | None = None) -> None:
        job = await self.get(job_key)
        if job is None:
            return
        job.last_status = "success" if success else "failed"
        job.last_error = error[:2000] if error else None
        if success:
            job.last_success_at = utc_now()

    async def list_all(self) -> list[ScheduledJob]:
        result = await self._session.execute(select(ScheduledJob).order_by(ScheduledJob.job_key))
        return list(result.scalars())

    async def disable_job_types(self, job_types: set[str], *, reason: str) -> list[str]:
        """Deaktiviert alle Jobs der genannten Typen (z. B. abgeschaffte Digests)."""
        if not job_types:
            return []
        result = await self._session.execute(
            select(ScheduledJob).where(ScheduledJob.job_type.in_(job_types))
        )
        disabled: list[str] = []
        for job in result.scalars():
            if not job.is_enabled and job.last_status == "disabled":
                continue
            job.is_enabled = False
            job.last_status = "disabled"
            job.last_error = reason[:2000]
            disabled.append(job.job_key)
        return disabled

    async def clear_stale_running(self) -> list[str]:
        """Reset jobs left as ``running`` after a worker kill/restart."""
        result = await self._session.execute(
            select(ScheduledJob).where(ScheduledJob.last_status == "running")
        )
        cleared: list[str] = []
        for job in result.scalars():
            job.last_status = "interrupted"
            job.last_error = "cleared_stale_running_on_startup"
            cleared.append(job.job_key)
        return cleared
