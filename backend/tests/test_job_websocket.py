import inspect

from app.api.routes_ws import job_websocket


def test_job_websocket_subscribes_before_reading_event_backlog() -> None:
    source = inspect.getsource(job_websocket)

    subscribe = source.index('subscribe(f"events.job.{job_id}")')
    backlog = source.index("backlog = await session.execute")

    assert subscribe < backlog
