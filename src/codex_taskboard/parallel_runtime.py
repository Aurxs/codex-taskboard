"""Fixed task-group lifecycle and isolated, recoverable Git integration."""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

from . import git_workspace as gw
from .constants import FAILED_RETRY_PROMPT
from .task_skills import EXECUTE_SKILL, MERGE_SKILL, PLAN_SKILL
from .errors import ConflictError, TaskboardError, UsageLimitExceeded, ValidationError
from .parallel_db import rank


ACTIVE = {"inProgress", "in_progress", "inprogress", "running", "pending"}


def latest_turn(snapshot: dict) -> dict:
    turns = snapshot.get("thread", {}).get("turns", [])
    return turns[-1] if turns else {}


def output_text(value: Any) -> str:
    if isinstance(value, dict):
        if value.get("type") == "agentMessage" and isinstance(value.get("text"), str):
            return value["text"]
        return "\n".join(filter(None, (output_text(v) for v in value.values())))
    if isinstance(value, list):
        return "\n".join(filter(None, (output_text(v) for v in value)))
    return ""


class ParallelRuntime:
    def __init__(self, scheduler):
        self.scheduler = scheduler
        self.db = scheduler.db
        self.workers: dict[str, asyncio.Task] = {}
        self.dispatch_lock = asyncio.Lock()

    @property
    def server(self):
        return self.scheduler.server

    async def publish(self, task_id: str) -> dict:
        task = self.db.get_task(task_id)
        await self.scheduler._publish_task(task)
        if task["parentId"]:
            parent = self.db.set_parallel(task["parentId"], childUpdated=task["updatedAt"])
            await self.scheduler._publish_task(parent)
        self.scheduler.kick()
        return task

    def spawn(self, key: str, coroutine) -> None:
        if key in self.workers:
            coroutine.close()
            return
        worker = asyncio.create_task(coroutine, name="taskboard-" + key)
        self.workers[key] = worker
        def finished(done):
            self.workers.pop(key, None)
            if not done.cancelled():
                done.exception()  # Workers persist their errors before returning.
            self.scheduler.kick()
        worker.add_done_callback(finished)

    async def stop(self):
        workers = list(self.workers.values())
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        self.workers.clear()

    async def queue(self, task: dict, prompt=None, request_id=None) -> dict:
        if task["threadId"] and task["runState"] in {"failed", "waiting_quota"}:
            await self.scheduler._ensure_server()
            latest = latest_turn(await self.server.read_thread(task["threadId"], include_turns=True))
            if latest.get("status") in ACTIVE:
                await self.scheduler._adopt_turn(task["id"], latest)
                await self.scheduler._set_task(task["id"], status="in_progress", run_state="running", last_error=None)
                self.scheduler._spawn_existing_watch(task["id"], latest["id"])
                return await self.publish(task["id"])
        self.db.enqueue(task["id"], task["version"], prompt, request_id)
        claimed = self.db.claim_candidate(task["id"], queued=True)
        if claimed:
            self.scheduler._spawn_execution(task["id"], prompt)
        return await self.publish(task["id"])

    async def execution_thread(self, task: dict, cwd: str) -> str:
        if not task["parallel"].get("managed"):
            return await self.server.start_thread(cwd)
        op_id = "execution-thread:" + task["id"]
        operation = self.db.operation(op_id)
        if operation and operation["payload"].get("threadId"):
            return operation["payload"]["threadId"]
        if operation and operation["state"] == "uncertain":
            self.db.set_parallel(task["id"], uncertainThread=True, waitReason="会话创建结果待核对")
            raise ConflictError("会话创建结果待核对，请关联 Codex 中已有的执行会话")
        self.db.save_operation(op_id, task["id"], "execution_thread", "uncertain", cwd=cwd)
        try:
            thread = await self.server.start_thread(cwd)
        except Exception:
            self.db.set_parallel(task["id"], uncertainThread=True, waitReason="会话创建结果待核对")
            raise
        self.db.save_operation(op_id, task["id"], "execution_thread", "ready", threadId=thread)
        return thread

    async def execution_turn(self, task: dict, thread_id: str, prompt: str, options: dict) -> str:
        for previous_op in self.db.operations(task["id"], "execution"):
            if previous_op["state"] == "uncertain":
                self.db.set_parallel(task["id"], uncertainExecution=previous_op["id"], nativeConflict=True,
                                     waitReason="回合启动结果待核对，请先确认原生会话状态")
                raise ConflictError("已有回合启动结果待核对，不能重复派发")
        op_id = "execution:" + str(uuid.uuid4())
        previous = self.db.latest_run(task["id"])
        self.db.save_operation(op_id, task["id"], "execution", "uncertain", threadId=thread_id,
                               previousTurnId=previous["turnId"] if previous else None)
        try:
            turn = await self.server.start_turn(thread_id, prompt, task_id=task["id"], skill=EXECUTE_SKILL, **options)
        except Exception as exc:
            from .app_server import RpcFailure
            definitive = isinstance(exc, (RpcFailure, UsageLimitExceeded, ValidationError))
            self.db.save_operation(op_id, task["id"], "execution", "blocked" if definitive else "uncertain", error=str(exc))
            if not definitive:
                self.db.set_parallel(task["id"], uncertainExecution=op_id, nativeConflict=True, waitReason="回合启动结果待核对，请先确认原生会话状态")
            raise
        self.db.save_operation(op_id, task["id"], "execution", "agent_running", turnId=turn)
        return turn

    async def dispatch(self):
        async with self.dispatch_lock:
            # Queued continuations are explicit user actions, independent of automation.
            for task in self.db.queue_candidates():
                claimed = self.db.claim_candidate(task["id"], queued=True)
                if claimed:
                    self.scheduler._spawn_execution(task["id"], self.db.queued_prompt(task["id"]))
                    await self.publish(task["id"])
            for task in sorted(self.db.list_tasks(), key=rank):
                project = self.db.get_project(task["projectId"])
                if task["kind"] == "parallel_group":
                    if task["groupPhase"] == "submitted" and task["status"] == "todo" and (project["automationEnabled"] or task["parallel"].get("runRequested")):
                        await self.start_group(task)
                    if task["groupPhase"] == "submitted" and task["status"] == "in_progress" and task["parallel"].get("integrationBranch"):
                        for child in sorted(self.db.children(task["id"]), key=rank):
                            if child["parallel"].get("resumeRequested"):
                                child = self.db.set_parallel(child["id"], resumeRequested=False)
                                try:
                                    await self.queue(child, FAILED_RETRY_PROMPT)
                                except Exception as exc:
                                    self.db.set_parallel(child["id"], waitReason=str(exc))
                                    await self.publish(child["id"])
                                continue
                            if child["status"] == "todo" and not child["queued"]:
                                claimed = self.db.claim_candidate(child["id"])
                                if claimed:
                                    self.scheduler._spawn_execution(child["id"])
                                    await self.publish(child["id"])
                        await self.settle_group(task["id"])
                    elif task["groupPhase"] == "submitted" and task["status"] == "in_progress":
                        self.spawn("group:" + task["id"], self.prepare_group(task["id"]))
                elif not task["parentId"] and project["automationEnabled"] and not task["queued"]:
                    claimed = self.db.claim_candidate(task["id"])
                    if claimed:
                        self.scheduler._spawn_execution(task["id"])
                        await self.publish(task["id"])
            for task in sorted(self.db.list_tasks(), key=rank):
                if task["mergeState"] != "queued" or task["parallel"].get("paused"):
                    continue
                if task["parentId"] and self.db.get_task(task["parentId"])["groupPhase"] != "submitted":
                    continue
                key = self.db.repo_info(task)[1]
                # One integration per repository, irrespective of number of execution workers.
                self.spawn("merge:" + key, self.merge_task(task["id"]))

    async def start_group(self, task: dict) -> dict:
        with self.db.transaction(immediate=True):
            current = self.db.get_task(task["id"])
            if current["status"] != "todo" or current["groupPhase"] != "submitted":
                return current
            reason = self.db.eligibility(current)
            if reason:
                if current["waitReason"] == reason and current["parallel"].get("runRequested"):
                    return current
                self.db.set_parallel(task["id"], waitReason=reason, runRequested=True)
            else:
                self.db.update_task(task["id"], current["version"], status="in_progress", parallel={"waitReason": None, "runRequested": False})
        if not reason:
            self.spawn("group:" + task["id"], self.prepare_group(task["id"]))
        return await self.publish(task["id"])

    async def prepare_group(self, task_id: str):
        try:
            task = await self.prepare_task(task_id)
            if self.db.get_task(task_id)["groupPhase"] != "submitted":
                return
            root = task["worktreeGitRoot"]
            branch = "codex/taskboard/group-" + task_id
            if gw.current_branch(root) != branch:
                existing = gw.git(root, "rev-parse", "--verify", "refs/heads/" + branch, check=False)
                gw.git(root, "switch", branch) if existing else gw.git(root, "switch", "-c", branch)
            self.db.set_parallel(task_id, integrationBranch=branch)
        except Exception as exc:
            self.db.set_parallel(task_id, groupPhase="paused", paused=True, waitReason=str(exc))
            await self.scheduler._set_task(task_id, run_state="failed", last_error=str(exc))
            self.db.release_execution(task_id, scopes=True)
        await self.publish(task_id)

    async def managed_worktree(self, task: dict, op_id: str, base: str, *, purpose: str) -> dict:
        operation = self.db.operation(op_id)
        if operation and operation["payload"].get("worktreeGitRoot"):
            return operation["payload"]
        if operation and operation["state"] in {"creating", "uncertain"}:
            self.db.set_parallel(task["id"], uncertainWorktree=op_id)
            raise ConflictError("工作树创建结果待核对；请在 Codex 中检查，并关联已有工作树")
        workspace = self.db.get_project(task["projectId"])["workspacePath"]
        root, _ = gw.repository(workspace)
        branch = "codex/taskboard/" + purpose + "-" + uuid.uuid5(uuid.NAMESPACE_URL, op_id).hex
        gw.pin_branch(root, branch, base)
        self.db.save_operation(op_id, task["id"], "worktree", "creating", baseCommit=base, baseBranch=branch, purpose=purpose)
        try:
            worktree = await self.server.create_worktree(workspace, branch)
            if gw.repository(worktree["worktreeGitRoot"])[1] != gw.repository(root)[1] or gw.commit(worktree["worktreeGitRoot"]) != base:
                raise ValidationError("Codex 返回的工作树与记录的仓库或起始版本不一致")
        except Exception as exc:
            self.db.save_operation(op_id, task["id"], "worktree", "uncertain", error=str(exc))
            self.db.set_parallel(task["id"], uncertainWorktree=op_id)
            raise
        self.db.save_operation(op_id, task["id"], "worktree", "ready", **worktree)
        return worktree

    async def prepare_task(self, task_id: str) -> dict:
        task = self.db.get_task(task_id)
        if not task["parallel"].get("managed"):
            return task
        workspace = self.db.get_project(task["projectId"])["workspacePath"]
        root, _ = gw.repository(workspace)
        if not task["targetBranch"]:
            raise ValidationError("请选择合入目标分支")
        gw.local_branch(root, task["targetBranch"])
        if task["worktreePath"]:
            if gw.repository(task["worktreeGitRoot"])[1] != gw.repository(root)[1]:
                raise ValidationError("保存的工作树不属于当前仓库")
            if task["parentId"] and task["parallel"].get("validationRequested"):
                parent = self.db.get_task(task["parentId"])
                base = task["parallel"].get("validationBase") or gw.commit(root, parent["parallel"]["integrationBranch"])
                task = self.db.set_parallel(task_id, validationBase=base)
                execution_root = task["worktreeGitRoot"]
                if gw.clean(execution_root) and not gw.is_ancestor(execution_root, base, gw.commit(execution_root)) and not gw.git(execution_root, "rev-parse", "--verify", "MERGE_HEAD", check=False):
                    try:
                        gw.git(execution_root, "merge", "--no-ff", "--no-edit", base)
                    except gw.GitError:
                        if not gw.git(execution_root, "diff", "--name-only", "--diff-filter=U"):
                            raise
            return task
        base = task["parallel"].get("baseCommit")
        if not base:
            if task["parentId"]:
                parent = self.db.get_task(task["parentId"])
                if not parent["parallel"].get("integrationBranch"):
                    raise ValidationError("任务组集成工作树尚未就绪")
                base = gw.commit(root, parent["parallel"]["integrationBranch"])
            else:
                base = gw.commit(root, task["branch"] or task["targetBranch"])
            task = self.db.set_parallel(task_id, baseCommit=base)
        await self.scheduler._ensure_server()
        worktree = await self.managed_worktree(task, "execute-worktree:" + task_id, base, purpose="base")
        task = await self.scheduler._set_task(task_id, worktree_path=worktree["worktreeWorkspaceRoot"], worktree_git_root=worktree["worktreeGitRoot"])
        if task["kind"] == "parallel_group":
            op_id = "group-owner:" + task_id
            operation = self.db.operation(op_id)
            thread = operation["payload"].get("threadId") if operation else None
            if not thread:
                thread = await self.server.start_thread(task["worktreePath"])
                self.db.save_operation(op_id, task_id, "owner", "ready", threadId=thread)
            await self.server.set_worktree_owner(task["worktreeGitRoot"], thread)
        return task

    async def completed(self, task_id: str, result: Any, summary: str | None) -> bool:
        task = self.db.get_task(task_id)
        if not task["parallel"].get("managed"):
            return False
        self.db.release_execution(task_id)
        self.db.finish_queue(task_id)
        try:
            root = task["worktreeGitRoot"]
            if task["parallel"].get("needsValidation"):
                raise ValidationError("前置成果在本回合执行期间变化，请重新验证")
            if not root or not gw.clean(root):
                raise ValidationError("工作树仍有未提交修改，请继续原会话提交后再合入")
            source = gw.commit(root)
            if not gw.is_ancestor(root, task["parallel"]["baseCommit"], source):
                raise ValidationError("任务结果不再包含记录的起始版本，请核对 Git 历史")
            delivery_base = task["parallel"].get("validationBase") or task["parallel"].get("mergedSource") or task["parallel"]["baseCommit"]
            if not gw.is_ancestor(root, delivery_base, source):
                raise ValidationError("成果尚未包含需要重新验证的前置版本")
            outside = gw.outside_scopes(root, delivery_base, source, task["writeScopes"])
            if outside:
                self.db.set_parallel(task_id, resultCommit=source, deliveryBase=delivery_base, mergeState="blocked", outsideScopes=outside)
                raise ValidationError("修改超出声明范围：" + ", ".join(outside))
            project = self.db.get_project(task["projectId"])
            review = project["reviewRequired"] and not task["parentId"]
            updated = await self.scheduler._set_task(task_id, status="in_review" if review else "in_progress", run_state=None,
                        last_message=summary, last_error=None,
                        parallel={"resultCommit": source, "deliveryBase": delivery_base, "validationBase": None, "validationRequested": False, "needsValidation": False, "mergeState": "pending_review" if review else "queued", "outsideScopes": [], "waitReason": None})
            self.db.update_latest_run(task_id, run_state="completed", last_output_summary=summary)
            await self.publish(updated["id"])
        except Exception as exc:
            await self.scheduler._fail_task(task_id, {"code": "RESULT_NEEDS_ATTENTION", "message": str(exc)})
        return True

    async def settle_group(self, group_id: str):
        group = self.db.get_task(group_id)
        children = self.db.children(group_id)
        if (group["groupPhase"] != "submitted" or group["mergeState"] != "none" or not children
            or not all(c["status"] == "done" and c["mergeState"] == "merged" and not c["parallel"].get("needsValidation") for c in children)):
            return
        review = self.db.get_project(group["projectId"])["reviewRequired"]
        source = gw.commit(group["worktreeGitRoot"])
        await self.scheduler._set_task(group_id, status="in_review", run_state=None,
                    parallel={"resultCommit": source, "mergeState": "pending_review" if review else "queued"})
        await self.publish(group_id)

    def scopes_for(self, task: dict) -> list[str]:
        if task["kind"] != "parallel_group":
            return task["writeScopes"]
        children = self.db.children(task["id"])
        # An unrestricted child makes the group's aggregate output unrestricted.
        return sorted({s for c in children for s in c["writeScopes"]}) if all(c["writeScopes"] for c in children) else []

    async def merge_task(self, task_id: str):
        operation = None
        try:
            task = self.db.get_task(task_id)
            if task["mergeState"] not in {"queued", "merging", "merged"}:
                return
            if task["parallel"].get("nativeConflict") or task["parallel"].get("uncertainExecution"):
                raise ConflictError("回合启动结果待核对，请先确认原生会话状态")
            source = task["parallel"].get("resultCommit")
            if not source:
                raise ValidationError("没有可合入的已提交成果")
            root, repo_key = gw.repository(self.db.get_project(task["projectId"])["workspacePath"])
            target = self.db.get_task(task["parentId"])["parallel"]["integrationBranch"] if task["parentId"] else task["targetBranch"]
            # A detached conflict worker may still be alive after an uncertain response.
            for op in self.db.operations(kind="merge"):
                if op["state"] in {"agent_running", "uncertain", "waiting_quota"} and op["payload"].get("repoKey") == repo_key:
                    raise ConflictError("等待已有合并会话停止或恢复")
            if gw.commit(task["worktreeGitRoot"]) != source or not gw.clean(task["worktreeGitRoot"]):
                raise ValidationError("成果在审阅后发生变化，请继续执行并重新审阅")
            outside = gw.outside_scopes(task["worktreeGitRoot"], task["parallel"].get("deliveryBase") or task["parallel"]["baseCommit"], source, self.scopes_for(task))
            if outside:
                self.db.set_parallel(task_id, outsideScopes=outside)
                raise ValidationError("修改超出声明范围：" + ", ".join(outside))
            with self.db.transaction(immediate=True):
                reason = self.db.reserve_scopes(task)
                if reason:
                    raise ConflictError(reason)
            # A main-checkout execution must not race publication into its directory.
            for other in self.db.list_tasks():
                if other["id"] != task_id and other["executionMode"] == "local" and other["status"] == "in_progress" and other["runState"] in {"starting", "running", "waiting_input", "waiting_approval"} and self.db.repo_info(other)[1] == repo_key:
                    raise ConflictError("等待目标工作区中的执行结束")
            target_sha = gw.local_branch(root, target)
            if gw.is_ancestor(root, source, target_sha):
                await self.merged(task_id, source, target_sha)
                return
            op_id = f"merge:{task_id}:{source}:{target_sha}"
            operation = self.db.operation(op_id)
            if operation and operation["state"] in {"blocked", "uncertain"}:
                raise ConflictError("已有合并需要处理，请使用重试合并")
            operation = self.db.save_operation(op_id, task_id, "merge", "preparing", sourceCommit=source,
                            targetCommit=target_sha, targetBranch=target, repoKey=repo_key)
            self.db.set_parallel(task_id, mergeState="merging", mergeOperation=op_id, waitReason=None)
            await self.publish(task_id)
            worktree = await self.managed_worktree(task, "worktree:" + op_id, target_sha, purpose="merge")
            merge_root = worktree["worktreeGitRoot"]
            self.db.save_operation(op_id, task_id, "merge", "preparing", **worktree)
            if not gw.is_ancestor(merge_root, source, gw.commit(merge_root)):
                if not gw.git(merge_root, "rev-parse", "--verify", "MERGE_HEAD", check=False):
                    try:
                        gw.git(merge_root, "merge", "--no-ff", "--no-edit", source)
                    except gw.GitError:
                        if not gw.git(merge_root, "diff", "--name-only", "--diff-filter=U"):
                            raise
                await self.resolve_merge(op_id)
            await self.publish_merge(op_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if operation:
                current = self.db.operation(operation["id"])
                state = current["state"] if current["state"] in {"uncertain", "agent_running", "waiting_quota"} else "blocked"
                self.db.save_operation(operation["id"], task_id, "merge", state, error=str(exc))
            self.db.set_parallel(task_id, mergeState="blocked", waitReason=str(exc))
            await self.publish(task_id)

    async def resolve_merge(self, op_id: str):
        operation = self.db.operation(op_id)
        data = operation["payload"]
        task = self.db.get_task(operation["task_id"])
        root = data["worktreeGitRoot"]
        # No Agent is needed when Git already produced a clean merge commit.
        if gw.is_ancestor(root, data["sourceCommit"], gw.commit(root)) and gw.clean(root):
            return
        thread = data.get("threadId")
        if not thread:
            thread = await self.server.start_thread(data["worktreeWorkspaceRoot"])
            self.db.save_operation(op_id, task["id"], "merge", "preparing", threadId=thread)
            await self.server.set_worktree_owner(root, thread)
        else:
            await self.server.resume_thread(thread)
        self.server.register_thread_task(thread, task["id"])
        prompt = (f"Merge conflicts for task {task['identifier']}: {task['title']}.\n"
                  f"Task requirements:\n{task['description']}\nSource commit: {data['sourceCommit']}\n"
                  f"Target commit: {data['targetCommit']}\n")
        self.db.save_operation(op_id, task["id"], "merge", "uncertain", startingTurn=True)
        try:
            turn = await self.server.start_turn(thread, prompt, task_id=task["id"], skill=MERGE_SKILL, **await self.scheduler.execution_options(task["model"], task["reasoningEffort"]))
            self.db.save_operation(op_id, task["id"], "merge", "agent_running", turnId=turn, startingTurn=False)
            result = await self.server.wait_for_turn(turn)
            await self.check_aux_result(op_id, result)
        except UsageLimitExceeded as exc:
            self.db.save_operation(op_id, task["id"], "merge", "waiting_quota", resumeAt=exc.resets_at, error=str(exc))
            raise

    async def check_aux_result(self, op_id: str, result: dict):
        from .app_server import usage_error_info, _extract_reset_at
        from .scheduler import _turn_status, _structured_error
        op = self.db.operation(op_id)
        info = usage_error_info(result)
        if info:
            reset = _extract_reset_at(result)
            if reset is None:
                try:
                    reset = _extract_reset_at(await self.server.read_rate_limits())
                except Exception:
                    pass
            self.db.save_operation(op_id, op["task_id"], op["kind"], "waiting_quota", error=info,
                                   resumeAt=reset)
            raise ValidationError("合并会话等待额度恢复")
        if _turn_status(result) not in {"completed", "complete", "succeeded"} or _structured_error(result):
            self.db.save_operation(op_id, op["task_id"], op["kind"], "blocked", result=result)
            raise ValidationError("Codex 会话未成功完成，请检查原会话后重试")
        self.db.save_operation(op_id, op["task_id"], op["kind"], "verified_turn", result=result)

    async def publish_merge(self, op_id: str):
        op = self.db.operation(op_id)
        task = self.db.get_task(op["task_id"])
        data = op["payload"]
        root = data["worktreeGitRoot"]
        if task["mergeState"] != "merging" or task["queued"] or task["parallel"].get("nativeConflict") or task["parallel"].get("uncertainExecution") or task["parallel"].get("paused") or task["status"] == "canceled" or (task["parentId"] and self.db.get_task(task["parentId"])["groupPhase"] != "submitted"):
            raise ConflictError("任务已暂停，合并成果保留待继续")
        result = gw.commit(root)
        if not gw.clean(root) or gw.git(root, "rev-parse", "--verify", "MERGE_HEAD", check=False):
            raise ValidationError("合并工作树仍有冲突或未提交修改")
        if not all(gw.is_ancestor(root, sha, result) for sha in (data["sourceCommit"], data["targetCommit"])):
            raise ValidationError("合并结果没有同时包含源提交与目标提交")
        if task["parallel"].get("resultCommit") != data["sourceCommit"] or gw.commit(task["worktreeGitRoot"]) != data["sourceCommit"] or not gw.clean(task["worktreeGitRoot"]):
            raise ValidationError("待合入成果已变化，请重新审阅")
        outside = gw.outside_scopes(root, data["targetCommit"], result, self.scopes_for(task))
        if outside:
            self.db.set_parallel(task["id"], outsideScopes=outside)
            raise ValidationError("合并修复超出声明范围：" + ", ".join(outside))
        for other in self.db.list_tasks():
            if other["id"] != task["id"] and other["executionMode"] == "local" and other["status"] == "in_progress" and other["runState"] in {"starting", "running", "waiting_input", "waiting_approval"} and self.db.repo_info(other)[1] == data["repoKey"]:
                raise ConflictError("等待目标工作区中的执行结束")
        if gw.local_branch(root, data["targetBranch"]) != data["targetCommit"]:
            self.db.save_operation(op_id, task["id"], "merge", "superseded")
            self.db.set_parallel(task["id"], mergeState="queued", waitReason="目标分支已前进，重新准备合并")
            await self.publish(task["id"])
            return
        self.db.save_operation(op_id, task["id"], "merge", "publishing", resultCommit=result)
        gw.publish(root, data["targetBranch"], data["targetCommit"], result)
        self.db.save_operation(op_id, task["id"], "merge", "completed", resultCommit=result)
        await self.merged(task["id"], data["sourceCommit"], result)

    async def merged(self, task_id: str, source: str, result: str):
        task = self.db.get_task(task_id)
        await self.scheduler._set_task(task_id, status="done", run_state=None, last_error=None,
            parallel={"mergeState": "merged", "mergedSource": source, "mergedCommit": result, "waitReason": None})
        self.db.release_execution(task_id, scopes=True)
        if task["parentId"]:
            await self.settle_group(task["parentId"])
        await self.publish(task_id)

    async def action(self, task: dict, action: str, feedback: str | None = None) -> dict | None:
        task_id = task["id"]
        if action == "attach_thread":
            if not task["parallel"].get("uncertainThread") or not feedback or task["threadId"]:
                raise ValidationError("当前没有待关联的执行会话")
            await self.scheduler._ensure_server()
            snapshot = await self.server.read_thread(feedback.strip(), include_turns=True)
            cwd = snapshot.get("thread", {}).get("cwd")
            if not cwd or Path(cwd).resolve() != Path(task["worktreePath"]).resolve():
                raise ValidationError("该会话的执行目录与任务工作树不一致")
            if any(t["threadId"] == feedback.strip() for t in self.db.list_tasks()):
                raise ConflictError("该会话已关联其他任务")
            self.db.save_operation("execution-thread:" + task_id, task_id, "execution_thread", "ready", threadId=feedback.strip())
            await self.scheduler._set_task(task_id, thread_id=feedback.strip(), parallel={"uncertainThread": False, "waitReason": None})
            self.server.register_thread_task(feedback.strip(), task_id)
            return await self.publish(task_id)
        if action == "attach_worktree":
            op_id = task["parallel"].get("uncertainWorktree")
            op = self.db.operation(op_id) if op_id else None
            if not op or not feedback:
                raise ValidationError("请输入需要关联的原工作树路径")
            root, key = gw.repository(str(Path(feedback).expanduser()))
            if key != self.db.repo_info(task)[1] or not gw.is_ancestor(root, op["payload"]["baseCommit"], gw.commit(root)):
                raise ValidationError("工作树不属于原仓库或不包含记录的起始提交")
            self.db.save_operation(op_id, task_id, "worktree", "ready", worktreeGitRoot=root, worktreeWorkspaceRoot=str(Path(feedback).resolve()))
            self.db.set_parallel(task_id, uncertainWorktree=None, waitReason=None)
            return await self.publish(task_id)
        if action in {"pause", "group_pause"}:
            return await self.pause(task)
        if action == "cancel" and (task["kind"] == "parallel_group" or task["parallel"].get("managed")):
            await self.pause(task)
            for child in self.db.children(task_id):
                if child["status"] != "done":
                    await self.scheduler._set_task(child["id"], status="canceled", run_state=None)
            await self.scheduler._set_task(task_id, status="canceled", run_state=None)
            return await self.publish(task_id)
        if action in {"group_submit", "group_resume"}:
            self.db.require_group_editable(task_id)
            children = self.db.children(task_id)
            if not children or any(c["priority"] == "draft" or c["status"] == "canceled" for c in children):
                raise ValidationError("请至少添加一个有效子任务，并处理草稿或取消的子任务")
            gw.repository(self.db.get_project(task["projectId"])["workspacePath"])
            if not task["targetBranch"]:
                raise ValidationError("请选择合入目标分支")
            with self.db.transaction(immediate=True):
                self.db.update_task(task_id, task["version"], status="todo", run_state=None,
                                    parallel={"groupPhase": "submitted", "paused": False, "mergeState": "none", "waitReason": None})
                for child in children:
                    if child["parallel"].get("paused"):
                        self.db.set_parallel(child["id"], paused=False)
                    if child["threadId"] and child["status"] == "in_progress" and child["runState"] == "failed":
                        current = self.db.get_task(child["id"])
                        # Enqueue after parent activation below; mark intent durably now.
                        self.db.set_parallel(current["id"], resumeRequested=True)
            if action == "group_resume" or self.db.get_project(task["projectId"])["automationEnabled"]:
                await self.start_group(self.db.get_task(task_id))
            return await self.publish(task_id)
        if action == "run" and task["kind"] == "parallel_group":
            if task["groupPhase"] != "submitted":
                raise ValidationError("请先添加子任务并提交整个任务组")
            return await self.start_group(task)
        if action in {"complete", "merge_retry"} and task["parallel"].get("managed"):
            if not task["parallel"].get("resultCommit") or task["mergeState"] not in {"pending_review", "blocked", "queued"}:
                raise ValidationError("任务成果尚未准备好，不能跳过合入标记完成")
            if task["parallel"].get("paused"):
                raise ValidationError("请先继续任务，再合入")
            if task["parentId"] and self.db.get_task(task["parentId"])["groupPhase"] != "submitted":
                raise ValidationError("请先继续父任务组")
            for op in self.db.operations(task_id, "merge"):
                if op["state"] in {"uncertain", "waiting_quota", "agent_running"}:
                    if not op["payload"].get("threadId"):
                        raise ConflictError("合并会话创建结果不确定，请先在 Codex 中核对")
                    await self.scheduler._ensure_server()
                    latest = latest_turn(await self.server.read_thread(op["payload"]["threadId"], include_turns=True))
                    if latest.get("status") in ACTIVE:
                        raise ConflictError("合并会话仍在运行，请在原会话中处理或先暂停")
                    self.db.save_operation(op["id"], task_id, "merge", "retry")
                if op["state"] == "blocked":
                    self.db.save_operation(op["id"], task_id, "merge", "retry")
            self.db.set_parallel(task_id, mergeState="queued", approvedCommit=task["parallel"]["resultCommit"], waitReason=None)
            return await self.publish(task_id)
        if action == "resume" and task["parallel"].get("paused"):
            self.db.set_parallel(task_id, paused=False, nativeConflict=False, waitReason=None)
            task = self.db.get_task(task_id)
            if task["parallel"].get("resultCommit") and task["mergeState"] in {"queued", "blocked", "pending_review"}:
                return await self.publish(task_id)
            return await self.queue(task, FAILED_RETRY_PROMPT if task["threadId"] else None)
        return None

    async def pause(self, task: dict) -> dict:
        task_id = task["id"]
        members = self.db.children(task_id) if task["kind"] == "parallel_group" else [task]
        self.db.set_parallel(task_id, **({"groupPhase": "pausing"} if task["kind"] == "parallel_group" else {}), paused=True, waitReason="暂停待确认")
        await self.publish(task_id)
        ids = {task_id, *(c["id"] for c in members)}
        self.scheduler._manual_overrides.update(ids)
        try:
            for member in members:
                self.db.finish_queue(member["id"])
                await self.scheduler._interrupt(member)
                if member["threadId"]:
                    for _ in range(3):
                        latest = latest_turn(await self.server.read_thread(member["threadId"], include_turns=True))
                        if latest.get("status") not in ACTIVE:
                            break
                        await asyncio.sleep(.1)
                    else:
                        raise ConflictError("暂停待确认：Codex 回合仍在运行")
            for op in self.db.operations():
                if op["task_id"] in ids and op["payload"].get("threadId") and op["kind"] in {"merge", "plan"}:
                    thread = op["payload"]["threadId"]
                    latest = latest_turn(await self.server.read_thread(thread, include_turns=True))
                    if latest.get("status") in ACTIVE:
                        await self.server.interrupt_turn(thread, latest["id"])
                        latest = latest_turn(await self.server.read_thread(thread, include_turns=True))
                        if latest.get("status") in ACTIVE:
                            raise ConflictError("暂停待确认：辅助会话仍在运行")
                    if op["state"] not in {"completed", "confirmed"}:
                        self.db.save_operation(op["id"], op["task_id"], op["kind"], "blocked", error="已暂停")
            if any("group:" + i in self.workers for i in ids):
                raise ConflictError("暂停待确认：工作树仍在创建，请稍后再次确认暂停")
            for member in members:
                self.scheduler._cancel_execution(member["id"])
                self.db.release_execution(member["id"], scopes=True)
                changes = {"parallel": {"paused": True, "nativeConflict": False, "waitReason": None}}
                if member["mergeState"] == "merging":
                    changes["parallel"]["mergeState"] = "blocked"
                if member["runState"] in {"starting", "running", "waiting_approval", "waiting_input", "waiting_quota"}:
                    changes["run_state"] = "failed"
                await self.scheduler._set_task(member["id"], **changes)
                await self.publish(member["id"])
            self.db.release_execution(task_id, scopes=True)
            self.db.set_parallel(task_id, **({"groupPhase": "paused"} if task["kind"] == "parallel_group" else {}), paused=True, nativeConflict=False, uncertainExecution=None, waitReason=None)
            for op in self.db.operations():
                if op["task_id"] in ids and op["kind"] == "execution" and op["state"] == "uncertain":
                    self.db.save_operation(op["id"], op["task_id"], "execution", "blocked", error="用户已确认暂停")
        except Exception:
            # Keep all leases until stopped turns have actually been observed.
            await self.publish(task_id)
            raise
        finally:
            self.scheduler._manual_overrides.difference_update(ids)
        return await self.publish(task_id)

    def auxiliary(self, params: dict) -> dict | None:
        from .app_server import extract_identifier
        thread = extract_identifier(params, "threadId")
        turn = extract_identifier(params, "turnId")
        return next((o for o in self.db.operations() if o["kind"] in {"plan", "merge"}
                     and ((thread and o["payload"].get("threadId") == thread) or (turn and o["payload"].get("turnId") == turn))), None)

    async def generate_plan(self, group: dict, request_id: str) -> dict:
        self.db.require_group_editable(group["id"])
        existing = self.db.operation(request_id)
        if existing:
            if existing["task_id"] != group["id"] or existing["kind"] != "plan":
                raise ConflictError("操作标识已被其他操作使用")
            return existing
        self.db.save_operation(request_id, group["id"], "plan", "pending", groupVersion=group["version"])
        self.spawn("plan:" + request_id, self.run_plan(request_id))
        return self.db.operation(request_id)

    async def run_plan(self, op_id: str):
        op = self.db.operation(op_id)
        group = self.db.get_task(op["task_id"])
        try:
            await self.scheduler._ensure_server()
            workspace = self.db.get_project(group["projectId"])["workspacePath"]
            thread = await self.server.start_thread(workspace)
            self.server.register_thread_task(thread, group["id"])
            self.db.save_operation(op_id, group["id"], "plan", "uncertain", threadId=thread)
            prompt = json.dumps({
                "title": group["title"], "description": group["description"],
                "existingTasks": [{"title": c["title"], "description": c["description"]}
                                  for c in self.db.children(group["id"])],
            }, ensure_ascii=False)
            prompt += self.db.attachment_prompt(group["id"])
            turn = await self.server.start_turn(thread, prompt, task_id=group["id"], skill=PLAN_SKILL, **await self.scheduler.execution_options(group["model"], group["reasoningEffort"]))
            self.db.save_operation(op_id, group["id"], "plan", "agent_running", turnId=turn)
            result = await self.server.wait_for_turn(turn)
            await self.check_aux_result(op_id, result)
            await self.finish_plan(op_id, result)
        except Exception as exc:
            current = self.db.operation(op_id)
            self.db.save_operation(op_id, group["id"], "plan", "uncertain" if current["state"] == "uncertain" else "blocked", error=str(exc))
        await self.publish(group["id"])

    async def finish_plan(self, op_id: str, result: dict):
        op = self.db.operation(op_id)
        text = output_text(result).strip()
        if not text:
            text = output_text(await self.server.read_thread(op["payload"]["threadId"], include_turns=True)).strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        proposal = json.loads(text)
        tasks = self.validate_proposal(proposal)
        self.db.save_operation(op_id, op["task_id"], "plan", "ready", proposal={"tasks": tasks})

    @staticmethod
    def validate_proposal(proposal: dict) -> list[dict]:
        if not isinstance(proposal, dict) or not isinstance(proposal.get("tasks"), list) or not proposal["tasks"]:
            raise ValidationError("拆分草案需要包含子任务列表")
        tasks = proposal["tasks"]
        keys = set()
        for task in tasks:
            if not isinstance(task, dict) or not isinstance(task.get("key"), str) or not task["key"] or task["key"] in keys or not isinstance(task.get("title"), str) or not task["title"].strip():
                raise ValidationError("草案子任务需要唯一标识和标题")
            if not isinstance(task.get("description", ""), str) or not isinstance(task.get("blockedByKeys", []), list) or not all(isinstance(k, str) for k in task.get("blockedByKeys", [])):
                raise ValidationError("草案描述或依赖格式不正确")
            keys.add(task["key"])
            task["writeScopes"] = gw.normalize_scopes(task.get("writeScopes", []))
        remaining = {t["key"]: set(t.get("blockedByKeys", [])) for t in tasks}
        if any(not deps <= keys for deps in remaining.values()):
            raise ValidationError("草案依赖引用了不存在的子任务")
        while remaining:
            ready = {k for k, deps in remaining.items() if not deps}
            if not ready:
                raise ValidationError("草案依赖不能包含环")
            remaining = {k: deps - ready for k, deps in remaining.items() if k not in ready}
        return tasks

    async def confirm_plan(self, group_id: str, version: int, op_id: str, proposal: dict) -> dict:
        with self.db.transaction(immediate=True):
            group = self.db.require_group_editable(group_id)
            op = self.db.operation(op_id)
            if op and op["task_id"] == group_id and op["state"] == "confirmed":
                return group
            if group["version"] != version or not op or op["task_id"] != group_id or op["kind"] != "plan" or op["state"] != "ready":
                raise ConflictError("任务组或拆分草案已变化，请刷新后确认")
            tasks = self.validate_proposal(proposal)
            created = {}
            for task in tasks:
                created[task["key"]] = self.db.create_task(project_id=group["projectId"], parent_id=group_id,
                    title=task["title"], description=task.get("description", ""), write_scopes=task["writeScopes"])
            for task in tasks:
                child = created[task["key"]]
                self.db.replace_dependencies(child["id"], child["version"], [created[k]["id"] for k in task.get("blockedByKeys", [])])
            self.db.save_operation(op_id, group_id, "plan", "confirmed", confirmedTaskIds=[c["id"] for c in created.values()])
        return await self.publish(group_id)

    async def recover(self):
        # Called after native synchronization. Uncertain starts are never replayed blindly.
        for task in self.db.list_tasks():
            if task["kind"] != "task" or task["parallel"].get("paused"):
                continue
            if task["status"] == "in_progress" and task["runState"] == "starting" and not task["threadId"] and task["id"] not in self.scheduler._execution_tasks:
                self.scheduler._spawn_execution(task["id"], self.db.queued_prompt(task["id"]))
            uncertain = task["parallel"].get("uncertainExecution") or next((op["id"] for op in self.db.operations(task["id"], "execution") if op["state"] == "uncertain"), None)
            operation = self.db.operation(uncertain) if uncertain else None
            if operation and task["threadId"]:
                try:
                    latest = latest_turn(await self.server.read_thread(task["threadId"], include_turns=True))
                    if latest.get("id") and latest["id"] != operation["payload"].get("previousTurnId"):
                        self.db.save_operation(uncertain, task["id"], "execution", "agent_running", turnId=latest["id"])
                        await self.scheduler._set_task(task["id"], status="in_progress", run_state="running", parallel={"uncertainExecution": None, "nativeConflict": False, "waitReason": None})
                        self.scheduler._register_turn(task["id"], latest["id"])
                        if latest.get("status") in ACTIVE:
                            self.scheduler._spawn_existing_watch(task["id"], latest["id"])
                        else:
                            await self.scheduler._handle_turn_result(task["id"], {"turn": latest})
                except TaskboardError:
                    pass
        for op in self.db.operations():
            if op["kind"] == "plan" and op["state"] == "pending" and "plan:" + op["id"] not in self.workers:
                self.db.save_operation(op["id"], op["task_id"], "plan", "blocked", error="草案准备被中断，可保留现有子任务后重新生成")
                await self.publish(op["task_id"])
            if op["kind"] not in {"plan", "merge"} or op["state"] not in {"agent_running", "uncertain", "publishing", "preparing", "verified_turn", "waiting_quota"}:
                continue
            if op["kind"] == "merge" and "merge:" + op["payload"].get("repoKey", "") in self.workers:
                continue
            if "plan:" + op["id"] in self.workers or "recover:" + op["id"] in self.workers:
                continue
            key = "merge:" + op["payload"].get("repoKey", op["id"]) if op["kind"] == "merge" else "recover:" + op["id"]
            self.spawn(key, self.recover_operation(op["id"]))

    async def recover_operation(self, op_id: str):
        op = self.db.operation(op_id)
        task_id = op["task_id"]
        try:
            data = op["payload"]
            if data.get("threadId"):
                self.server.register_thread_task(data["threadId"], task_id)
                latest = latest_turn(await self.server.read_thread(data["threadId"], include_turns=True))
                if latest.get("status") in ACTIVE:
                    self.db.save_operation(op_id, task_id, op["kind"], "agent_running", turnId=latest["id"])
                    return
                if latest:
                    from .scheduler import _reset_due
                    if op["state"] == "waiting_quota":
                        if op["kind"] != "merge" or not self.db.get_project(self.db.get_task(task_id)["projectId"])["quotaAutoResumeEnabled"] or not _reset_due(data.get("resumeAt")):
                            return
                        if self.db.get_task(task_id)["parallel"].get("paused"):
                            return
                        await self.resolve_merge(op_id)
                    else:
                        await self.check_aux_result(op_id, {"turn": latest})
                    if op["kind"] == "plan":
                        await self.finish_plan(op_id, {"turn": latest})
                        await self.publish(task_id)
                        return
                elif op["state"] == "uncertain":
                    raise ConflictError("辅助会话启动结果不确定，请在 Codex 中检查后处理")
            if op["kind"] == "merge":
                root = data.get("worktreeGitRoot")
                if root and data.get("resultCommit") and gw.is_ancestor(root, data["resultCommit"], gw.local_branch(root, data["targetBranch"])):
                    self.db.save_operation(op_id, task_id, "merge", "completed")
                    await self.merged(task_id, data["sourceCommit"], data["resultCommit"])
                elif root:
                    current_task = self.db.get_task(task_id)
                    if current_task["mergeState"] == "blocked" and current_task["parallel"].get("mergeOperation") == op_id and not current_task["parallel"].get("paused"):
                        self.db.set_parallel(task_id, mergeState="merging")
                    await self.publish_merge(op_id)
                else:
                    self.db.save_operation(op_id, task_id, "merge", "blocked", error="合并准备被中断，请核对工作树后重试")
                    self.db.set_parallel(task_id, mergeState="blocked", waitReason="合并准备被中断，请重试合并")
        except Exception as exc:
            # A transport outage isn't proof that an auxiliary turn stopped.
            from .app_server import AppServerUnavailable
            current = self.db.operation(op_id)
            if isinstance(exc, AppServerUnavailable):
                return
            if current["state"] != "waiting_quota":
                self.db.save_operation(op_id, task_id, op["kind"], "blocked", error=str(exc))
            if op["kind"] == "merge":
                self.db.set_parallel(task_id, mergeState="blocked", waitReason=str(exc))
        await self.publish(task_id)
