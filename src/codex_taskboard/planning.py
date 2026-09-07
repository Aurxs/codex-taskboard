"""Interactive task planning, retained independently of execution threads."""
from __future__ import annotations

import asyncio
import re
import uuid

from .app_server import AppServerUnavailable, RpcFailure
from .errors import ConflictError, UsageLimitExceeded, ValidationError
from .parallel_runtime import ACTIVE, latest_turn


class TaskPlanning:
    def __init__(self, scheduler):
        self.scheduler = scheduler
        self.db = scheduler.db
        self.lock = asyncio.Lock()
        self.subscribed: set[str] = set()

    @property
    def server(self):
        return self.scheduler.server

    def save(self, op, state, **payload):
        return self.db.save_operation(op["id"], op["task_id"], "task_plan", state, **payload)

    async def action(self, task, action, text=None):
        async with self.lock:
            current = self.db.get_task(task["id"])
            if current["version"] != task["version"]:
                raise ConflictError("任务已变化，请刷新后操作")
            op = self.db.operation(current["plan"].get("operationId") or "")
            if action == "plan_start":
                if current["kind"] != "task" or current["status"] != "todo" or current["queued"] or current["plan"]["hold"]:
                    raise ValidationError("仅未排队的待认领任务可以开始计划")
                if current["parentId"]:
                    self.db.require_group_editable(current["parentId"])
                op = self.db.save_operation(str(uuid.uuid4()), current["id"], "task_plan", "pending",
                                           title=current["title"], description=current["description"])
                self.db.set_parallel(current["id"], planHold=True, planOperation=op["id"])
                self.scheduler.parallel.spawn("task-plan:" + op["id"], self.start(op))
            elif action == "plan_save":
                if not current["plan"]["hold"]:
                    op = self.db.operation(current["parallel"].get("acceptedPlan") or "")
                if current["status"] != "todo" or current["queued"]:
                    raise ValidationError("仅未排队的待认领任务可以编辑计划")
                if current["parentId"]:
                    self.db.require_group_editable(current["parentId"])
                if not op or op["state"] not in {"ready", "confirmed"}:
                    raise ValidationError("请等待最终计划生成后再编辑")
                if not text or not text.strip():
                    raise ValidationError("计划内容不能为空")
                thread = op["payload"].get("threadId")
                if thread:
                    await self.scheduler._ensure_server()
                    latest = latest_turn(await self.server.read_thread(thread, include_turns=True))
                    if latest.get("id") != op["payload"].get("turnId") or latest.get("status") in ACTIVE:
                        raise ConflictError("计划会话已变化，请刷新后编辑")
                if self.pending(op):
                    raise ValidationError("请先回答计划中的待处理问题")
                if self.db.get_task(current["id"])["version"] != current["version"]:
                    raise ConflictError("任务已变化，请刷新后编辑计划")
                # Preserve the native proposal; local edits must survive history replay.
                with self.db.transaction(immediate=True):
                    self.save(op, op["state"], editedText=text.strip())
                    self.db.set_parallel(current["id"])
            elif not op or not current["plan"]["hold"]:
                raise ValidationError("当前任务没有待处理的计划")
            elif action == "plan_continue":
                if not text or not text.strip():
                    raise ValidationError("请填写计划补充要求")
                if op["state"] in {"pending", "starting", "uncertain"}:
                    raise ConflictError("计划会话状态待核对，请稍后重试")
                if current["title"] != op["payload"].get("title") or current["description"] != op["payload"].get("description"):
                    text = f'任务要求已更新：{current["title"]}\n{current["description"]}\n\n用户补充：{text}'
                await self.send(op, text.strip())
                self.save(self.db.operation(op["id"]), self.db.operation(op["id"])["state"],
                          title=current["title"], description=current["description"])
            elif action in {"plan_accept", "plan_cancel"}:
                if action == "plan_accept":
                    thread = op["payload"].get("threadId")
                    if thread:
                        await self.scheduler._ensure_server()
                        latest = latest_turn(await self.server.read_thread(thread, include_turns=True))
                        if latest.get("id") != op["payload"].get("turnId") or latest.get("status") in ACTIVE:
                            raise ConflictError("计划会话已变化，请稍后确认最终计划")
                    if op["state"] != "ready" or not op["payload"].get("text"):
                        raise ValidationError("请先生成完整的最终计划")
                    if current["title"] != op["payload"].get("title") or current["description"] != op["payload"].get("description"):
                        raise ValidationError("任务要求已修改，请发送补充让计划同步后再确认")
                    if self.pending(op):
                        raise ValidationError("请先回答计划中的待处理问题")
                    if self.db.get_task(current["id"])["version"] != current["version"]:
                        raise ConflictError("任务已变化，请刷新后确认计划")
                    self.save(op, "confirmed")
                    self.db.set_parallel(current["id"], acceptedPlan=op["id"])
                else:
                    if op["state"] in {"pending", "starting"}:
                        raise ConflictError("计划正在启动，请稍后取消")
                    thread = op["payload"].get("threadId")
                    if thread:
                        await self.scheduler._ensure_server()
                        latest = latest_turn(await self.server.read_thread(thread, include_turns=True))
                        if latest.get("status") in ACTIVE:
                            await self.server.interrupt_turn(thread, latest["id"])
                            latest = latest_turn(await self.server.read_thread(thread, include_turns=True))
                            if latest.get("status") in ACTIVE:
                                raise ConflictError("正在停止计划，请稍后再次取消")
                    for interaction in self.pending(op):
                        if interaction["kind"] == "async_user_input":
                            self.db.resolve_interaction(interaction["id"], interaction["version"], {}, canceled=True)
                        else:
                            try:
                                await self.scheduler.resolve_interaction(interaction["id"], interaction["version"], {}, canceled=True)
                            except ConflictError:
                                # The stopped native turn may have already invalidated its request.
                                latest = self.db.get_interaction(interaction["id"])
                                if latest["status"] == "pending":
                                    self.db.resolve_interaction(latest["id"], latest["version"], {}, canceled=True)
                    self.save(op, "canceled")
                self.db.set_parallel(current["id"], planHold=False, waitReason=None)
            else:
                raise ValidationError("未知计划操作")
            return await self.scheduler.parallel.publish(current["id"])

    def pending(self, op):
        return [i for i in self.db.list_interactions(op["task_id"], pending_only=True)
                if i["payload"].get("threadId") == op["payload"].get("threadId")]

    async def start(self, op):
        async with self.lock:
            await self._start(op)

    async def _start(self, op):
        try:
            await self.scheduler._ensure_server()
            task = self.db.get_task(op["task_id"])
            workspace = task.get("worktreePath") or self.db.get_project(task["projectId"])["workspacePath"]
            self.save(op, "starting")
            thread = await self.server.start_thread(workspace)
            self.server.register_thread_task(thread, task["id"])
            self.subscribed.add(thread)
            op = self.save(op, "conversation", threadId=thread)
            prompt = (f'为任务 "{task["identifier"]}: {task["title"]}" 制定详细实施计划。\n\n'
                      f'{task["description"]}\n\n'
                      "先检查项目并通过提问澄清需求，可进行多轮问答。仅规划，不实施代码变更。"
                      "信息充分后，将可直接交给后续执行任务的完整最终计划放入 <proposed_plan> 标签。"
                      "计划应包括目标、修改范围、实施步骤、验证方式和已确认的决策。")
            prompt += self.db.attachment_prompt(task["id"]) + self.db.plan_prompt(task["id"])
            if task["parentId"]:
                parent = self.db.get_task(task["parentId"])
                prompt += f'\n父任务要求：\n{parent["description"]}' + self.db.plan_prompt(parent["id"])
            await self.send(op, prompt)
        except Exception as exc:
            current = self.db.operation(op["id"])
            if current["state"] not in {"uncertain", "blocked"}:
                self.save(current, "blocked", error=str(exc))
        await self.scheduler.parallel.publish(op["task_id"])

    async def send(self, op, text):
        await self.scheduler._ensure_server()
        thread = op["payload"].get("threadId")
        if not thread:
            raise ValidationError("计划会话未创建成功，请取消后重新开始")
        latest = latest_turn(await self.server.read_thread(thread, include_turns=True))
        if latest.get("status") in ACTIVE:
            await self.server.steer_turn(thread, latest["id"], text)
            return
        if self.pending(op) and any(i["kind"] != "async_user_input" for i in self.pending(op)):
            raise ConflictError("请先处理计划中的待回答问题或批准请求")
        await self.server.resume_thread(thread)
        task = self.db.get_task(op["task_id"])
        parent = self.db.get_task(task["parentId"]) if task["parentId"] else None
        options = await self.scheduler.execution_options(task["model"] or (parent["model"] if parent else None),
                   task["reasoningEffort"] or (parent["reasoningEffort"] if parent and not task["model"] else None))
        self.save(op, "uncertain", previousTurnId=latest.get("id"), error=None)
        try:
            turn = await self.server.start_turn(thread, text, task_id=task["id"], plan=True, **options)
        except (RpcFailure, UsageLimitExceeded, ValidationError) as exc:
            self.save(op, "blocked", error=str(exc))
            raise
        except Exception as exc:
            self.save(op, "uncertain", error=str(exc))
            raise
        self.save(op, "agent_running", turnId=turn, text=None, editedText=None)
        await self.scheduler.parallel.publish(task["id"])

    async def recover(self):
        async with self.lock:
            await self._recover()

    async def _recover(self):
        for op in self.db.operations(kind="task_plan"):
            if op["state"] in {"confirmed", "canceled"}:
                continue
            if "task-plan:" + op["id"] in self.scheduler.parallel.workers:
                continue
            try:
                thread = op["payload"].get("threadId")
                if not thread:
                    if op["state"] in {"pending", "starting"}:
                        self.save(op, "blocked", error="计划准备被中断，请取消后重新生成")
                        await self.scheduler.parallel.publish(op["task_id"])
                    continue
                self.server.register_thread_task(thread, op["task_id"])
                if thread not in self.subscribed:
                    await self.server.resume_thread(thread)
                    self.subscribed.add(thread)
                before = self.db.list_interactions(op["task_id"])
                activity_before = self.db.list_activity(op["task_id"])
                snapshot = await self.server.read_thread(thread, include_turns=True)
                for turn in snapshot.get("thread", {}).get("turns", []):
                    for item in turn.get("items", []):
                        self.scheduler._record_item(op["task_id"], turn["id"], item, turn.get("status") not in ACTIVE,
                                                    thread_id=thread)
                if before != self.db.list_interactions(op["task_id"]) or activity_before != self.db.list_activity(op["task_id"]):
                    await self.scheduler.parallel.publish(op["task_id"])
                latest = latest_turn(snapshot)
                if op["state"] == "uncertain" and latest.get("id") == op["payload"].get("previousTurnId"):
                    continue
                await self.observe(op, latest)
            except AppServerUnavailable:
                continue
            except Exception as exc:
                self.save(op, "blocked", error=str(exc))
                await self.scheduler.parallel.publish(op["task_id"])

    async def observe(self, op, turn):
        if not turn or op["state"] in {"confirmed", "canceled", "pending", "starting"}:
            return
        status = turn.get("status")
        state = "agent_running" if status in ACTIVE else "conversation"
        payload = {"turnId": turn.get("id"), "error": None}
        if turn.get("id") != op["payload"].get("turnId"):
            payload.update(text=None, editedText=None)
        if status in {"failed", "interrupted"}:
            state = "blocked"
            payload["error"] = str(turn.get("error") or "计划回合已中断，可补充要求后继续")
        elif status in {"completed", "complete", "succeeded"}:
            # A progress plan (turn/plan/updated) and async questions are never final plans.
            items = turn.get("items", [])
            final = [i for i in items if i.get("type") == "plan"]
            messages = [i for i in items if i.get("type") == "agentMessage" and i.get("delivery") != "async"]
            text = final[-1].get("text", "") if final else ""
            if not text and messages:
                message = messages[-1]
                matches = re.findall(r"<proposed_plan>([\s\S]*?)</proposed_plan>", message.get("text", ""))
                text = matches[-1].strip() if matches else ""
            if text.strip():
                state = "ready"
                payload["text"] = text.strip()
        if state == "ready" and not self.pending(op):
            task = self.db.get_task(op["task_id"])
            if task["title"] == op["payload"].get("title") and task["description"] == op["payload"].get("description"):
                # A completed native proposal is the saved execution plan. No second
                # confirmation is needed; drafts remain excluded from dispatch.
                with self.db.transaction(immediate=True):
                    self.save(op, "confirmed", **payload)
                    self.db.set_parallel(task["id"], acceptedPlan=op["id"], planHold=False, waitReason=None)
                await self.scheduler.parallel.publish(task["id"])
                return
            state = "blocked"
            payload["error"] = "任务要求已修改，请发送补充让计划同步"
        if state != op["state"] or any(op["payload"].get(k) != v for k, v in payload.items()):
            self.save(op, state, **payload)
            await self.scheduler.parallel.publish(op["task_id"])
