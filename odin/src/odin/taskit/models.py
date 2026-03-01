"""Taskit data models."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    BACKLOG = "backlog"
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    EXECUTING = "executing"
    REVIEW = "review"
    TESTING = "testing"
    DONE = "done"
    FAILED = "failed"


class Comment(BaseModel):
    """Inter-agent message attached to a task."""

    author: str
    content: str
    attachments: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)


class Task(BaseModel):
    """A tracked task in the Taskit system."""

    id: str
    title: str
    description: str
    status: TaskStatus = TaskStatus.BACKLOG
    assigned_agent: Optional[str] = None
    spec_id: Optional[str] = None
    parent_task_id: Optional[str] = None
    depends_on: List[str] = Field(default_factory=list)
    comments: List[Comment] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    metadata: Dict[str, Any] = Field(default_factory=dict)
