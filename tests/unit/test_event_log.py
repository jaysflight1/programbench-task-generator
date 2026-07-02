from pbgen.logging.event_log import EventLogger


def test_event_logger_writes_and_reads_jsonl(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    logger = EventLogger(path)
    logger.append(task_id="demo", stage="repo", event_type="repo_selected")
    events = logger.read_events()
    assert len(events) == 1
    assert events[0].event_type == "repo_selected"
    assert events[0].event_id
