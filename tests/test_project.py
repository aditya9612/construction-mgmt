from datetime import date

import pytest
from pydantic import ValidationError

from app.schemas.project import (
    CommentCreate,
    ProjectCreate,
    ProjectOut,
    TaskCreate,
    TaskOut,
    TaskProgressUpdate,
)


def test_project_schema_validation():
    payload = ProjectCreate(name="My Project", description=None, start_date=date(2026, 1, 1))
    assert payload.name == "My Project"


def test_project_out_completion_percentage_default():
    payload = ProjectOut(
        id=1,
        name="My Project",
        description=None,
        start_date=date(2026, 1, 1),
        end_date=None,
        status="Planned",
    )
    assert payload.completion_percentage == 0.0


def test_task_progress_percentage_range_validates():
    with pytest.raises(ValidationError):
        TaskProgressUpdate(percentage=-1, remarks="bad")
    with pytest.raises(ValidationError):
        TaskProgressUpdate(percentage=101, remarks="bad")

    ok = TaskProgressUpdate(percentage=55, remarks=None)
    assert ok.percentage == 55


def test_task_create_requires_assigned_user_id():
    with pytest.raises(ValidationError):
        TaskCreate(title="t", assigned_user_id=None)  # type: ignore[arg-type]

    ok = TaskCreate(title="t", assigned_user_id=1)
    assert ok.priority == 0
    assert ok.status == "Planned"


def test_task_out_includes_is_delayed_and_completion_percentage():
    out = TaskOut(
        id=1,
        project_id=1,
        title="t",
        description=None,
        priority=0,
        status="Planned",
        start_date=None,
        end_date=None,
        assigned_user_id=1,
        completion_percentage=0,
        is_delayed=False,
    )
    assert out.is_delayed is False
    assert out.completion_percentage == 0


def test_comment_create_validates():
    c = CommentCreate(content="hello")
    assert c.content == "hello"

