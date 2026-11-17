"""Developer, AI and productivity endpoints. Each group answers 501 when its integration is off."""

from __future__ import annotations

import asyncio
import secrets
from typing import Any, Literal

from fastapi import APIRouter, Response
from pydantic import Field
from sqlalchemy import select

from app.api.deps import Ctx, CurrentDevice
from app.api.lan import In
from app.core.errors import ApiError
from app.database.models import Note, Task, iso, utcnow
from app.integrations import dev

router = APIRouter()


def _need(enabled: bool, what: str) -> None:
    if not enabled:
        raise ApiError(501, "unsupported", f"{what} is turned off on this PC.")


# ---- developer -------------------------------------------------------------


@router.get("/dev/projects")
async def dev_projects(ctx: Ctx, _: CurrentDevice) -> dict[str, Any]:
    _need(ctx.config.integrations.git, "Git integration")
    return await ctx.hub.sample_once("dev.projects")


@router.get("/dev/projects/{project_id}/git")
async def dev_project_git(project_id: str, ctx: Ctx, _: CurrentDevice) -> dict[str, Any]:
    _need(ctx.config.integrations.git, "Git integration")
    project = next((p for p in ctx.config.dev.projects if p.name == project_id), None)
    if project is None:
        raise ApiError(404, "not_found", "Project not found.")
    status, commits = await asyncio.gather(
        asyncio.to_thread(dev.project_status, project), asyncio.to_thread(dev.project_commits, project)
    )
    return {**status, "commits": commits}


@router.get("/dev/ports")
async def dev_ports(ctx: Ctx, _: CurrentDevice) -> dict[str, Any]:
    _need(ctx.config.integrations.ports, "Port monitoring")
    return await ctx.hub.sample_once("dev.ports")


# ---- AI tools --------------------------------------------------------------


@router.get("/ai/sessions")
async def ai_sessions(ctx: Ctx, _: CurrentDevice) -> dict[str, Any]:
    return ctx.ai.sessions()


@router.get("/ai/events")
async def ai_events(ctx: Ctx, _: CurrentDevice, since: int = 0) -> dict[str, Any]:
    return {"events": ctx.ai.events(since)}


@router.get("/ai/usage")
async def ai_usage(ctx: Ctx, _: CurrentDevice) -> dict[str, Any]:
    _need(ctx.config.integrations.claude_code, "Claude Code integration")
    return ctx.ai.usage()


@router.get("/ai/permissions")
async def ai_permissions(ctx: Ctx, _: CurrentDevice) -> dict[str, Any]:
    return ctx.permissions.pending()


class PermissionAnswer(In):
    decision: Literal["allow", "deny"]


@router.post("/ai/permissions/{request_id}")
async def answer_permission(
    request_id: str, body: PermissionAnswer, ctx: Ctx, device: CurrentDevice
) -> dict[str, Any]:
    on = ctx.config.integrations
    _need(on.claude_code and on.claude_permissions, "Answering permission prompts")
    if not ctx.limits.actions.allow(device.id):
        raise ApiError(429, "rate_limited", "Too many answers. Slow down.", headers={"Retry-After": "5"})
    if ctx.permissions.answer(request_id, body.decision) is None:
        raise ApiError(404, "not_found", "That request was already answered or has expired.")
    return {"status": "answered", "decision": body.decision}


# ---- GitHub and media ------------------------------------------------------


@router.get("/github")
async def github_overview(ctx: Ctx, _: CurrentDevice) -> dict[str, Any]:
    _need(ctx.config.integrations.github and "github.overview" in ctx.hub.topics, "GitHub")
    return await ctx.hub.sample_once("github.overview")


@router.get("/media/art")
async def media_art(_: CurrentDevice) -> Response:
    from app.platform import winmedia

    art = winmedia.current_art()
    if art is None:
        raise ApiError(404, "not_found", "The current track has no cover art.")
    return Response(art[0], media_type=art[1], headers={"Cache-Control": "private, max-age=3600"})


# ---- productivity ----------------------------------------------------------


class TaskIn(In):
    title: str = Field(min_length=1, max_length=200)
    priority: bool = False


class TaskPatch(In):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    done: bool | None = None
    priority: bool | None = None


class NoteIn(In):
    text: str = Field(min_length=1, max_length=4000)


def _task(t: Task) -> dict[str, Any]:
    return {"id": t.id, "title": t.title, "done": t.done, "priority": t.priority,
            "created_at": iso(t.created_at), "done_at": iso(t.done_at)}  # fmt: skip


def _note(n: Note) -> dict[str, Any]:
    return {"id": n.id, "text": n.text, "updated_at": iso(n.updated_at)}


@router.get("/tasks")
async def list_tasks(ctx: Ctx, _: CurrentDevice) -> dict[str, Any]:
    with ctx.db() as db:
        rows = db.scalars(select(Task).order_by(Task.done, Task.priority.desc(), Task.created_at)).all()
    return {"tasks": [_task(t) for t in rows]}


@router.post("/tasks", status_code=201)
async def create_task(body: TaskIn, ctx: Ctx, _: CurrentDevice) -> dict[str, Any]:
    with ctx.db() as db:
        task = Task(id="t_" + secrets.token_hex(6), title=body.title, priority=body.priority)
        db.add(task)
        db.commit()
    ctx.hub.publish("sync", {"what": "tasks"})
    return _task(task)


@router.patch("/tasks/{task_id}")
async def update_task(task_id: str, body: TaskPatch, ctx: Ctx, _: CurrentDevice) -> dict[str, Any]:
    with ctx.db() as db:
        task = db.get(Task, task_id)
        if task is None:
            raise ApiError(404, "not_found", "Task not found.")
        for key, value in body.model_dump(exclude_none=True).items():
            setattr(task, key, value)
        if body.done is not None:
            task.done_at = utcnow() if body.done else None
        db.commit()
    ctx.hub.publish("sync", {"what": "tasks"})
    return _task(task)


@router.delete("/tasks/{task_id}", status_code=204)
async def delete_task(task_id: str, ctx: Ctx, _: CurrentDevice) -> None:
    with ctx.db() as db:
        task = db.get(Task, task_id)
        if task is None:
            raise ApiError(404, "not_found", "Task not found.")
        db.delete(task)
        db.commit()
    ctx.hub.publish("sync", {"what": "tasks"})


@router.get("/notes")
async def list_notes(ctx: Ctx, _: CurrentDevice) -> dict[str, Any]:
    with ctx.db() as db:
        rows = db.scalars(select(Note).order_by(Note.updated_at.desc())).all()
    return {"notes": [_note(n) for n in rows]}


@router.post("/notes", status_code=201)
async def create_note(body: NoteIn, ctx: Ctx, _: CurrentDevice) -> dict[str, Any]:
    with ctx.db() as db:
        note = Note(id="n_" + secrets.token_hex(6), text=body.text)
        db.add(note)
        db.commit()
    ctx.hub.publish("sync", {"what": "notes"})
    return _note(note)


@router.patch("/notes/{note_id}")
async def update_note(note_id: str, body: NoteIn, ctx: Ctx, _: CurrentDevice) -> dict[str, Any]:
    with ctx.db() as db:
        note = db.get(Note, note_id)
        if note is None:
            raise ApiError(404, "not_found", "Note not found.")
        note.text = body.text
        db.commit()
    ctx.hub.publish("sync", {"what": "notes"})
    return _note(note)


@router.delete("/notes/{note_id}", status_code=204)
async def delete_note(note_id: str, ctx: Ctx, _: CurrentDevice) -> None:
    with ctx.db() as db:
        note = db.get(Note, note_id)
        if note is None:
            raise ApiError(404, "not_found", "Note not found.")
        db.delete(note)
        db.commit()
    ctx.hub.publish("sync", {"what": "notes"})
