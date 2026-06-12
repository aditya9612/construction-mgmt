from datetime import date, datetime
from typing import Optional, TYPE_CHECKING
from sqlalchemy import (
    DECIMAL,
    JSON,
    TIMESTAMP,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    Index,
    Enum as SAEnum,
    func,
)
from sqlalchemy.orm import Mapped, backref, mapped_column, relationship
from decimal import Decimal
from app.core.enums import (
    AttendanceStatus,
    ChecklistStatus,
    DocumentStatus,
    MilestoneStatus,
    OTPolicyType,
    SafetyChecklistStatus,
    WorkActivityStatus,
)
from app.models.base import Base, TimestampMixin
from app.models.labour import Labour
from app.core.enums import (
    IssueCategory,
    IssuePriority,
    IssueStatus,
    ProjectStatus,
    TaskStatus,
    WeatherType,
    ProjectType,
    LocationType,
)

if TYPE_CHECKING:
    from app.models.owner import Owner
    from app.models.user import User
    from app.models.project import Project
    from app.models.contractor import Contractor


# ===================== PROJECT =====================


class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    business_id: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False, index=True
    )

    project_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    status: Mapped[ProjectStatus] = mapped_column(
        SAEnum(ProjectStatus), default=ProjectStatus.PLANNED
    )

    type: Mapped[Optional[ProjectType]] = mapped_column(
        SAEnum(ProjectType), nullable=True
    )
    location_type: Mapped[Optional[LocationType]] = mapped_column(
        SAEnum(LocationType), nullable=True
    )
    site_address: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    state: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    pincode: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    shift_start_time = mapped_column(Time, nullable=True)
    shift_end_time = mapped_column(Time, nullable=True)
    grace_period_minutes: Mapped[int] = mapped_column(Integer, default=15)

    owner_id: Mapped[int] = mapped_column(
        ForeignKey("owners.id"), nullable=False, index=True
    )

    owner: Mapped["Owner"] = relationship("Owner", back_populates="projects")

    members = relationship(
        "ProjectMember", back_populates="project", cascade="all, delete-orphan"
    )
    milestones = relationship(
        "Milestone",back_populates="project",
        cascade="all, delete-orphan",lazy="selectin",
    )

    tasks = relationship(
        "Task",back_populates="project",
        cascade="all, delete-orphan",lazy="selectin",
    )
    dsr_entries = relationship(
        "DailySiteReport", back_populates="project", cascade="all, delete-orphan"
    )
    issues = relationship(
        "Issue", back_populates="project", cascade="all, delete-orphan"
    )

    qc_records = relationship(
        "QCRecord", back_populates="project", cascade="all, delete-orphan"
    )
    safety_incidents = relationship("SafetyIncident", back_populates="project")
    checklists = relationship("Checklist", back_populates="project")

    ot_policy = relationship(
        "ProjectOTPolicy",
        uselist=False,
        back_populates="project",
        cascade="all, delete-orphan"
    )

    @property
    def total_milestones(self) -> int:
        milestones = self.__dict__.get("milestones", [])
        return len(milestones)

    @property
    def total_tasks(self) -> int:
        tasks = self.__dict__.get("tasks", [])
        return len(tasks)

    @property
    def completed_tasks(self) -> int:
        tasks = self.__dict__.get("tasks", [])
        if not tasks:
            return 0
        from app.core.enums import TaskStatus

        return sum(1 for t in tasks if t.status == TaskStatus.COMPLETED)

    @property
    def delayed_tasks(self) -> int:
        tasks = self.__dict__.get("tasks", [])
        if not tasks:
            return 0
        return sum(1 for t in tasks if getattr(t, "is_delayed", False))

    @property
    def execution_completion_percentage(self) -> float:
        milestones = self.__dict__.get("milestones", [])
        tasks = self.__dict__.get("tasks", [])

        if milestones:
            return sum(m.completion_percentage for m in milestones) / len(milestones)

        if tasks:
            return sum(t.completion_percentage or 0 for t in tasks) / len(tasks)

        return 0.0

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
        Index("idx_project_member_user", "user_id"),
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
    actual_start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    actual_end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    status = mapped_column(SAEnum(MilestoneStatus), default=MilestoneStatus.PLANNED)
    project: Mapped["Project"] = relationship("Project", back_populates="milestones")

    @property
    def total_tasks(self) -> int:
        tasks = self.__dict__.get("tasks", [])
        return len(tasks)

    @property
    def completed_tasks(self) -> int:
        tasks = self.__dict__.get("tasks", [])
        if not tasks:
            return 0
        from app.core.enums import TaskStatus

        return sum(1 for t in tasks if t.status == TaskStatus.COMPLETED)

    @property
    def pending_tasks(self) -> int:
        return self.total_tasks - self.completed_tasks

    @property
    def is_delayed(self) -> bool:
        from datetime import date

        today = date.today()
        if self.end_date and self.end_date < today and self.completion_percentage < 100:
            return True
        return False

    @property
    def execution_completion_percentage(self) -> float:
        return self.completion_percentage

    @property
    def delayed_tasks(self) -> int:
        tasks = self.__dict__.get("tasks", [])
        if not tasks:
            return 0
        return sum(1 for t in tasks if getattr(t, "is_delayed", False))

    @property
    def completion_percentage(self) -> float:
        tasks = self.__dict__.get("tasks", [])

        if not tasks:
            return 0.0

        return sum(t.completion_percentage or 0 for t in tasks) / len(tasks)

    __table_args__ = (
        UniqueConstraint("project_id", "title", name="uq_milestone_project_title"),
    )


# ===================== TASK =====================


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE")
    )

    milestone_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("milestones.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    milestone: Mapped[Optional["Milestone"]] = relationship(
        "Milestone",
        backref=backref("tasks", lazy="selectin"),
    )

    boq_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("boq_items.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)

    activity_type_id = Column(Integer, ForeignKey("activity_types.id"), nullable=True)

    priority: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[TaskStatus] = mapped_column(
        SAEnum(TaskStatus), default=TaskStatus.PLANNED
    )

    start_date: Mapped[Optional[date]] = mapped_column(Date)
    end_date: Mapped[Optional[date]] = mapped_column(Date)
    actual_start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    actual_end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    assigned_user_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id")
    )
    completion_percentage: Mapped[float] = mapped_column(Float, default=0)
    discipline: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    @property
    def planned_cost(self) -> float:
        # Simplistic mapping for now: if linked to a boq_item, use its total_cost.
        # Ideally, this should be pro-rated by task if multiple tasks map to same BOQ item.
        return 0.0

    @property
    def actual_cost(self) -> float:
        # Aggregated actual cost (labour + material + equipment + billing)
        return 0.0

    @property
    def is_delayed(self) -> bool:
        from datetime import date

        today = date.today()
        if self.end_date and today > self.end_date and self.completion_percentage < 100:
            return True
        return False

    @property
    def execution_duration(self) -> int:
        if self.actual_start_date:
            from datetime import date

            end = self.actual_end_date or date.today()
            return (end - self.actual_start_date).days
        return 0

    @property
    def delay_days(self) -> int:
        if self.is_delayed and self.end_date:
            from datetime import date

            today = date.today()
            return (today - self.end_date).days
        return 0

    # ================= TASK INSTRUCTION MEDIA =================

    audio_instruction_url: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
    )

    instruction_image_url: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
    )

    task_icon: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )

    # in Task model

    created_by_user_id = mapped_column(Integer, ForeignKey("users.id"), nullable=False)

    project = relationship("Project", back_populates="tasks")

    progress_entries = relationship(
        "TaskProgress", back_populates="task", cascade="all, delete-orphan"
    )
    comments = relationship(
        "Comment", back_populates="task", cascade="all, delete-orphan"
    )

    qc_records = relationship("QCRecord", back_populates="task")

    assignments = relationship(
        "TaskAssignment", back_populates="task", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("project_id", "title", name="uq_task_project_title"),
        #  ONLY KEEP THIS (high value composite index)
        Index(
            "idx_task_project_status_assigned",
            "project_id",
            "status",
            "assigned_user_id",
        ),
    )


class TaskAssignment(Base):
    __tablename__ = "task_assignments"
    
    __table_args__ = (
        UniqueConstraint("task_id", "user_id", name="uq_task_assignment"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    assigned_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    assigned_by_user_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    task = relationship("Task", back_populates="assignments")
    user = relationship("User", foreign_keys=[user_id])
    assigned_by = relationship("User", foreign_keys=[assigned_by_user_id])


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

    business_id: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False, index=True
    )

    project_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )

    task_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    task: Mapped[Optional["Task"]] = relationship("Task")

    created_by_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    created_by: Mapped[Optional["User"]] = relationship("User")

    report_date: Mapped[date] = mapped_column(Date, index=True)

    site_location: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    weather: Mapped[Optional[WeatherType]] = mapped_column(
        SAEnum(WeatherType), nullable=True
    )

    work_done: Mapped[str] = mapped_column(Text)
    work_planned: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    contractor_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("contractors.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )

    contractor: Mapped[Optional["Contractor"]] = relationship("Contractor")

    status = Column(String(20), default="Draft", index=True)

    total_labour = Column(Integer, default=0)
    skilled_labour = Column(Integer, default=0)
    unskilled_labour = Column(Integer, default=0)

    machinery_used: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    material_received: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    material_used: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    issues: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    safety_observations: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    remarks: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    latitude: Mapped[Optional[float]] = mapped_column(Float, index=True, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, index=True, nullable=True)

    photos = relationship(
        "DSRPhoto", back_populates="dsr", cascade="all, delete-orphan", lazy="selectin"
    )

    project: Mapped["Project"] = relationship("Project", back_populates="dsr_entries")

    labours: Mapped[list["DSRLabour"]] = relationship(
        "DSRLabour", back_populates="dsr", cascade="all, delete-orphan"
    )

    qc_records = relationship("QCRecord", back_populates="dsr")

    __table_args__ = (
        UniqueConstraint("project_id", "report_date", name="uq_project_dsr_date"),
        Index("idx_dsr_lat_lng", "latitude", "longitude"),
    )


# ===================== DSR LABOUR =====================


class DSRLabour(Base):
    __tablename__ = "dsr_labour"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    dsr_id: Mapped[int] = mapped_column(
        ForeignKey("daily_site_reports.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    task_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    task: Mapped[Optional["Task"]] = relationship("Task")

    labour_id: Mapped[int] = mapped_column(
        ForeignKey("labour.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    status: Mapped[AttendanceStatus] = mapped_column(
        SAEnum(AttendanceStatus),
        default=AttendanceStatus.PRESENT,
        nullable=False,
    )

    working_hours: Mapped[Decimal] = mapped_column(
        DECIMAL(5, 2),
        nullable=False,
    )

    overtime_hours: Mapped[Decimal] = mapped_column(
        DECIMAL(5, 2),
        default=Decimal("0"),
        nullable=False,
    )

    dsr: Mapped["DailySiteReport"] = relationship(
        "DailySiteReport", back_populates="labours"
    )

    labour: Mapped["Labour"] = relationship("Labour")

    __table_args__ = (
        UniqueConstraint("dsr_id", "labour_id", name="uq_dsr_labour"),
        Index("idx_dsr_labour_dsr", "dsr_id"),
        Index("idx_dsr_labour_labour", "labour_id"),
        Index("idx_dsr_labour_dsr_labour", "dsr_id", "labour_id"),
        CheckConstraint("working_hours >= 0"),
        CheckConstraint("overtime_hours >= 0"),
    )


# ===================== ISSUE =====================


class Issue(Base, TimestampMixin):
    __tablename__ = "issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    business_id: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False, index=True
    )

    project_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)

    category: Mapped[IssueCategory] = mapped_column(
        SAEnum(IssueCategory), nullable=False
    )

    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    reported_date: Mapped[date] = mapped_column(Date, nullable=False)

    priority: Mapped[IssuePriority] = mapped_column(
        SAEnum(IssuePriority), default=IssuePriority.MEDIUM
    )

    status: Mapped[IssueStatus] = mapped_column(
        SAEnum(IssueStatus), default=IssueStatus.OPEN
    )

    assigned_to: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    resolution: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    project: Mapped["Project"] = relationship("Project", back_populates="issues")

    __table_args__ = (
        UniqueConstraint("project_id", "title", name="uq_issue_project_title"),
        Index("idx_issue_reported_date", "reported_date"),
        Index("idx_issue_priority", "priority"),
        Index("idx_issue_status", "status"),
        Index("idx_issue_project_status", "project_id", "status"),
        Index("idx_issue_project_priority", "project_id", "priority"),
    )


class DSRPhoto(Base, TimestampMixin):
    __tablename__ = "dsr_photos"

    id = mapped_column(Integer, primary_key=True)
    dsr_id = mapped_column(
        ForeignKey("daily_site_reports.id", ondelete="CASCADE"), index=True
    )
    file_url = mapped_column(String(500), nullable=False)

    dsr = relationship("DailySiteReport", back_populates="photos")


# ===================== QC =====================


class QCRecord(Base, TimestampMixin):
    __tablename__ = "qc_records"

    id = Column(Integer, primary_key=True)

    project_id = Column(Integer, ForeignKey("projects.id"))
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=True)
    dsr_id = Column(Integer, ForeignKey("daily_site_reports.id"), nullable=True)

    inspection_type = Column(String(100))
    test_type = Column(String(100))
    result = Column(Float)
    standard_value = Column(Float)
    status = Column(String(20))
    engineer_name = Column(String(100))
    remarks = Column(Text)
    report_file_url = Column(String(255))

    project = relationship("Project", back_populates="qc_records")
    task = relationship("Task", back_populates="qc_records")
    dsr = relationship("DailySiteReport", back_populates="qc_records")

    __table_args__ = (Index("idx_qc_project", "project_id"),)


# ===================== SAFETY =====================


# class SafetyIncident(Base, TimestampMixin):
#     __tablename__ = "safety_incidents"

#     id = Column(Integer, primary_key=True)

#     project_id = Column(Integer, ForeignKey("projects.id"))

#     date = Column(Date)
#     violation_type = Column(String(100))
#     description = Column(Text)
#     injury_details = Column(Text)
#     action_taken = Column(Text)
#     responsible_person = Column(String(100))

#     project = relationship("Project", back_populates="safety_incidents")

#     __table_args__ = (Index("idx_safety_project", "project_id"),)


class SafetyIncident(Base, TimestampMixin):
    __tablename__ = "safety_incidents"

    id = Column(Integer, primary_key=True)

    project_id = Column(Integer, ForeignKey("projects.id"))

    task_id = Column(
        Integer, ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    task = relationship("Task")

    date = Column(Date)

    safety_checklist_status = Column(SAEnum(SafetyChecklistStatus), nullable=False)
    ppe_compliance = Column(Boolean, default=True)

    violation_type = Column(String(100))
    description = Column(Text)
    injury_details = Column(Text)
    action_taken = Column(Text)
    responsible_person = Column(String(100))

    project = relationship("Project", back_populates="safety_incidents")

    __table_args__ = (Index("idx_safety_project", "project_id"),)


# ===================== CHECKLIST ====================


class Checklist(Base):
    __tablename__ = "checklists"

    id = Column(Integer, primary_key=True)

    project_id = Column(Integer, ForeignKey("projects.id"))

    name = Column(String(100))
    type = Column(String(50))

    project = relationship("Project", back_populates="checklists")
    items = relationship(
        "ChecklistItem", back_populates="checklist", cascade="all, delete-orphan"
    )
    logs = relationship(
        "ChecklistLog", back_populates="checklist", cascade="all, delete-orphan"
    )


class ChecklistItem(Base):
    __tablename__ = "checklist_items"

    id = Column(Integer, primary_key=True)
    checklist_id = Column(Integer, ForeignKey("checklists.id"))

    item = Column(String(255))

    checklist = relationship("Checklist", back_populates="items")


class ChecklistLog(Base):
    __tablename__ = "checklist_logs"

    id = Column(Integer, primary_key=True)

    project_id = Column(Integer)
    checklist_id = Column(Integer, ForeignKey("checklists.id"))

    status = Column(SAEnum(ChecklistStatus), nullable=False)
    remarks = Column(Text)

    checklist = relationship("Checklist", back_populates="logs")


# ======================
# SITE PHOTOS
# ======================


class SitePhoto(Base, TimestampMixin):
    __tablename__ = "site_photos"

    id = Column(Integer, primary_key=True)

    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"))
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=True)
    dsr_id = Column(
        Integer,
        ForeignKey("daily_site_reports.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    task = relationship("Task")
    dsr = relationship("DailySiteReport")

    photo_url = Column(String(500), nullable=False)

    date = Column(Date)

    activity_tag = Column(String(100))
    location_tag = Column(String(100))
    description = Column(Text)

    project = relationship("Project")
    task = relationship("Task")


class DrawingDocument(Base, TimestampMixin):
    __tablename__ = "drawing_documents"

    id = Column(Integer, primary_key=True)

    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    drawing_name = Column(
        String(255),
        nullable=False,
        index=True,
    )

    version = Column(
        String(50),
        nullable=False,
    )

    file_url = Column(
        String(500),
        nullable=False,
    )

    date = Column(
        Date,
        nullable=True,
    )

    remarks = Column(
        Text,
        nullable=True,
    )

    # ================= APPROVAL =================

    approval_status = Column(
        SAEnum(DocumentStatus),
        default=DocumentStatus.PENDING,
        nullable=False,
        index=True,
    )

    approval_id = Column(
        Integer,
        ForeignKey("approvals.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ================= VERSION CONTROL =================

    is_latest_version = Column(
        Boolean,
        default=True,
        nullable=False,
        index=True,
    )

    revision_no = Column(
        Integer,
        default=1,
        nullable=False,
    )

    # ================= RELATIONSHIPS =================

    project = relationship("Project")

    approval = relationship(
        "Approval",
        foreign_keys=[approval_id],
    )

    # ================= CONSTRAINTS / INDEXES =================

    __table_args__ = (
        # ================= UNIQUE CONSTRAINTS =================
        UniqueConstraint(
            "project_id",
            "drawing_name",
            "version",
            name="uq_project_drawing_version",
        ),
        UniqueConstraint(
            "project_id",
            "drawing_name",
            "revision_no",
            name="uq_project_drawing_revision",
        ),
        # ================= INDEXES =================
        Index(
            "idx_drawing_project",
            "project_id",
        ),
        Index(
            "idx_drawing_project_status",
            "project_id",
            "approval_status",
        ),
        Index(
            "idx_drawing_latest",
            "project_id",
            "drawing_name",
            "is_latest_version",
        ),
        Index(
            "idx_drawing_revision",
            "project_id",
            "drawing_name",
            "revision_no",
        ),
        Index(
            "idx_drawing_approval",
            "approval_status",
            "is_latest_version",
        ),
    )


class SiteRequest(Base, TimestampMixin):
    __tablename__ = "site_requests"

    id = Column(Integer, primary_key=True)

    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"))

    request_type = Column(String(50))  # Material / Work
    description = Column(Text)
    quantity = Column(Float)

    requested_by = Column(Integer, ForeignKey("users.id"))
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    status = Column(String(20), default="Pending")  # Pending / Approved / Rejected

    project = relationship("Project")


# =================work progress===========================


class WorkActivity(Base, TimestampMixin):

    __tablename__ = "work_activities"

    id = Column(Integer, primary_key=True, index=True)

    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    boq_code = Column(Integer, nullable=True)

    activity_name = Column(String(255), nullable=False)

    planned_quantity = Column(DECIMAL(18, 2), default=0)

    unit = Column(String(50))

    engineer_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    work_order_id = Column(
        Integer,
        ForeignKey("work_orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    total_completed = Column(DECIMAL(18, 2), default=0)

    remaining_quantity = Column(DECIMAL(18, 2), default=0)

    completion_percentage = Column(DECIMAL(5, 2), default=0)

    discipline = Column(String(100), nullable=True)

    status = Column(
        SAEnum(WorkActivityStatus),
        default=WorkActivityStatus.NOT_STARTED,
        nullable=False,
    )

    start_date = Column(Date)

    end_date = Column(Date)

    created_at = Column(
        TIMESTAMP,
        server_default=func.now(),
    )

    # ================= RELATIONSHIPS =================

    project = relationship("Project")

    engineer = relationship("User")

    progress_entries = relationship(
        "DailyProgressEntry",
        back_populates="activity",
        cascade="all, delete-orphan",
    )

    # ================= UPDATED HISTORY RELATIONSHIP =================

    history_logs = relationship(
        "ActivityHistory",
        back_populates="activity",
    )

    # ================= CONSTRAINTS =================

    __table_args__ = (
        CheckConstraint(
            "planned_quantity >= 0",
            name="check_planned_quantity_positive",
        ),
        CheckConstraint(
            "end_date >= start_date",
            name="check_activity_dates",
        ),
        CheckConstraint(
            "completion_percentage >= 0 AND completion_percentage <= 100",
            name="check_completion_percentage_range",
        ),
    )


# ================= DAILY PROGRESS ENTRY =================


class DailyProgressEntry(Base, TimestampMixin):

    __tablename__ = "daily_progress_entries"

    id = Column(Integer, primary_key=True)

    activity_id = Column(
        Integer,
        ForeignKey("work_activities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    entry_date = Column(Date, nullable=False, index=True)

    today_progress = Column(
        DECIMAL(18, 2),
        default=0,
        nullable=False,
    )

    remarks = Column(Text)

    created_by = Column(
        Integer,
        ForeignKey("users.id"),
        nullable=True,
    )

    created_at = Column(
        TIMESTAMP,
        server_default=func.now(),
    )

    activity = relationship(
        "WorkActivity",
        back_populates="progress_entries",
    )

    __table_args__ = (
        UniqueConstraint(
            "activity_id",
            "entry_date",
            name="uq_activity_entry_date",
        ),
        CheckConstraint(
            "today_progress >= 0",
            name="check_today_progress_positive",
        ),
    )


# ================= ACTIVITY HISTORY =================


class ActivityHistory(Base):

    __tablename__ = "activity_history"

    id = Column(Integer, primary_key=True)

    # ================= UPDATED FOREIGN KEY =================

    activity_id = Column(
        Integer,
        ForeignKey("work_activities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # CREATE / UPDATE / DELETE / DAILY_PROGRESS_UPDATE
    action = Column(String(50), nullable=False)

    # old values before change
    old_value = Column(JSON, nullable=True)

    # new values after change
    new_value = Column(JSON, nullable=True)

    changed_by = Column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
    )

    remarks = Column(
        Text,
        nullable=True,
    )

    created_at = Column(
        TIMESTAMP,
        server_default=func.now(),
    )

    updated_at = Column(
        TIMESTAMP,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # ================= RELATIONSHIPS =================

    activity = relationship(
        "WorkActivity",
        back_populates="history_logs",
    )

    user = relationship("User")

    # ================= INDEXES =================

    __table_args__ = (
        Index(
            "idx_activity_history_activity",
            "activity_id",
        ),
        Index(
            "idx_activity_history_changed_by",
            "changed_by",
        ),
    )


from sqlalchemy import DECIMAL, Enum as SAEnum
from app.core.enums import OTPolicyType


class ProjectOTPolicy(Base, TimestampMixin):
    __tablename__ = "project_ot_policy"

    id = Column(Integer, primary_key=True)

    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    policy_type = Column(
        SAEnum(OTPolicyType),
        nullable=False,
        default=OTPolicyType.MULTIPLIER,
    )

    normal_day_multiplier = Column(DECIMAL(5, 2), default=1.5)

    sunday_multiplier = Column(DECIMAL(5, 2), default=2.0)

    holiday_multiplier = Column(DECIMAL(5, 2), default=3.0)

    fixed_ot_rate = Column(DECIMAL(10, 2), nullable=True)

    project = relationship("Project", back_populates="ot_policy")