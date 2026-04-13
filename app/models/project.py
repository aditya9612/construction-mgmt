from datetime import date
from typing import Optional, TYPE_CHECKING
from app.schemas.project import WeatherType
from sqlalchemy import (
    CheckConstraint,
    Date,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.schemas.project import IssueCategory, IssuePriority, IssueStatus, ProjectStatus, TaskStatus, WeatherType

if TYPE_CHECKING:
    from app.models.owner import Owner
    from app.models.user import User


class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    project_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    status: Mapped[ProjectStatus] = mapped_column(
        Enum(ProjectStatus), default=ProjectStatus.PLANNED
    )

    owner_id: Mapped[int] = mapped_column(
        ForeignKey("owners.id"), nullable=False, index=True
    )

    owner: Mapped["Owner"] = relationship("Owner", back_populates="projects")

    members: Mapped[list["ProjectMember"]] = relationship(
        "ProjectMember",
        back_populates="project",
        cascade="all, delete-orphan",
    )

    milestones: Mapped[list["Milestone"]] = relationship(
        "Milestone",
        back_populates="project",
        cascade="all, delete-orphan",
    )

    tasks: Mapped[list["Task"]] = relationship(
        "Task",
        back_populates="project",
        cascade="all, delete-orphan",
    )

    dsr_entries: Mapped[list["DailySiteReport"]] = relationship(
        "DailySiteReport",
        back_populates="project",
        cascade="all, delete-orphan",
    )

    issues: Mapped[list["Issue"]] = relationship(
        "Issue",
        back_populates="project",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint(
            "end_date IS NULL OR start_date IS NULL OR end_date >= start_date",
            name="check_project_dates",
        ),
    )



class ProjectMember(Base):
    __tablename__ = "project_members"

    project_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "project_id", "user_id", name="uq_project_members_project_id_user_id"
        ),
    )

    project: Mapped["Project"] = relationship("Project", back_populates="members")
    user: Mapped["User"] = relationship("User")


class Milestone(Base):
    __tablename__ = "milestones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    project_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    project: Mapped["Project"] = relationship("Project", back_populates="milestones")

    __table_args__ = (
        UniqueConstraint("project_id", "title", name="uq_milestone_project_title"),
    )

class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    project_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus), default=TaskStatus.PLANNED
    )

    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    assigned_user_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    completion_percentage: Mapped[float] = mapped_column(Float, default=0)

    project: Mapped["Project"] = relationship("Project", back_populates="tasks")

    progress_entries: Mapped[list["TaskProgress"]] = relationship(
        "TaskProgress",
        back_populates="task",
        cascade="all, delete-orphan",
    )

    comments: Mapped[list["Comment"]] = relationship(
        "Comment",
        back_populates="task",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("project_id", "title", name="uq_task_project_title"),
    )


class TaskProgress(Base, TimestampMixin):
    __tablename__ = "task_progress"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    task_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    percentage: Mapped[int] = mapped_column(Integer, nullable=False)
    remarks: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_by_user_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    task: Mapped["Task"] = relationship("Task", back_populates="progress_entries")

    __table_args__ = (CheckConstraint("percentage >= 0 AND percentage <= 100"),)


class Comment(Base, TimestampMixin):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    task_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    author_user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    content: Mapped[str] = mapped_column(Text, nullable=False)

    task: Mapped["Task"] = relationship("Task", back_populates="comments")


# ===================== DSR =====================

class DailySiteReport(Base, TimestampMixin):
    __tablename__ = "daily_site_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    project_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )

    created_by_user_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    created_by: Mapped[Optional["User"]] = relationship("User")

    report_date: Mapped[date] = mapped_column(Date, index=True)

    site_location: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    contractor_name = mapped_column(String(255), index=True)

    weather: Mapped[Optional[WeatherType]] = mapped_column(
        Enum(WeatherType),
        nullable=True
    )
    work_done: Mapped[str] = mapped_column(Text)
    work_planned: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    labour_count: Mapped[int] = mapped_column(Integer, default=0)

    machinery_used: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    material_received: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    material_used: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    issues: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    safety_observations: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    remarks: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    latitude = mapped_column(Float, index=True, nullable=True)
    longitude = mapped_column(Float, index=True, nullable=True)

    photos = relationship(
        "DSRPhoto",
        back_populates="dsr",
        cascade="all, delete-orphan"
    )

    project: Mapped["Project"] = relationship(
        "Project",
        back_populates="dsr_entries"
    )

    __table_args__ = (
        UniqueConstraint("project_id", "report_date", name="uq_project_dsr_date"),
    )


# ===================== ISSUE =====================

class Issue(Base, TimestampMixin):
    __tablename__ = "issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    project_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)

    category: Mapped[IssueCategory] = mapped_column(
        Enum(IssueCategory), nullable=False
    )

    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    reported_date: Mapped[date] = mapped_column(Date, nullable=False)

    priority: Mapped[IssuePriority] = mapped_column(
        Enum(IssuePriority), default=IssuePriority.MEDIUM
    )

    status: Mapped[IssueStatus] = mapped_column(
        Enum(IssueStatus), default=IssueStatus.OPEN
    )

    assigned_to: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True
    )

    resolution: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    project: Mapped["Project"] = relationship(
        "Project",
        back_populates="issues"
    )

    __table_args__ = (
        UniqueConstraint("project_id", "title", name="uq_issue_project_title"),
    )

class DSRPhoto(Base, TimestampMixin):
    __tablename__ = "dsr_photos"

    id = mapped_column(Integer, primary_key=True)
    dsr_id = mapped_column(
        ForeignKey("daily_site_reports.id", ondelete="CASCADE"),
        index=True
    )
    file_url = mapped_column(String(500), nullable=False)

    dsr = relationship("DailySiteReport", back_populates="photos")