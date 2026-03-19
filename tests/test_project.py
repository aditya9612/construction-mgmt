from datetime import date

from app.schemas.project import ProjectCreate


def test_project_schema_validation():
    payload = ProjectCreate(name="My Project", description=None, start_date=date(2026, 1, 1))
    assert payload.name == "My Project"

