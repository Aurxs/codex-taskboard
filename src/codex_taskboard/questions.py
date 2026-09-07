"""Native Astra async question messages and their structured user replies.

Verified against the bundled Codex app protocol: agentMessage.delivery=async,
questions[{title, options}], and send_user_message_question_reply user messages.
"""
import json

from .errors import ValidationError

REPLY_OPEN = "<send_user_message_question_reply>"
REPLY_CLOSE = "</send_user_message_question_reply>"


def record_questions(db, task_id, thread_id, turn_id, item):
    if item.get("type") == "agentMessage" and item.get("delivery") == "async":
        request_id = f"async:{thread_id}:{item['id']}"
        if any(i["requestId"] == request_id for i in db.list_interactions(task_id)):
            return
        questions = item.get("questions") or []
        normalized = [{"id": json.dumps(["request_user_input_async", item["id"], index], separators=(",", ":")),
                       "question": q["title"],
                       "options": [{"label": label} for label in q.get("options") or []]}
                      for index, q in enumerate(questions)]
        if not normalized:
            normalized = [{"id": item["id"], "question": item.get("text", ""), "options": []}]
        db.create_interaction(task_id=task_id, kind="async_user_input", request_id=request_id,
                              blocking_scope="none", payload={"threadId": thread_id, "turnId": turn_id,
                              "itemId": item["id"], "questions": normalized})
    elif item.get("type") in {"userMessage", "steeringUserMessage"}:
        if item.get("type") == "steeringUserMessage" and item.get("status") != "accepted":
            return
        for part in item.get("content", item.get("input", [])):
            text = part.get("text", "").strip()
            if not text.startswith(REPLY_OPEN) or not text.endswith(REPLY_CLOSE):
                continue
            try:
                replies = json.loads(text[len(REPLY_OPEN):-len(REPLY_CLOSE)])
                replies = replies if isinstance(replies, list) else [replies]
                answers = {r["questionItemId"]: {"answers": [r["answer"]]} for r in replies}
            except (ValueError, KeyError, TypeError):
                continue
            for interaction in db.list_interactions(task_id, pending_only=True):
                if interaction["kind"] != "async_user_input" or interaction["payload"].get("threadId") != thread_id:
                    continue
                ids = {q["id"] for q in interaction["questions"]}
                previous = interaction.get("response") or {}
                combined = {**previous.get("answers", {}), **{k: v for k, v in answers.items() if k in ids}}
                if ids <= combined.keys():
                    db.resolve_interaction(interaction["id"], interaction["version"], {"answers": combined})
                elif combined:
                    # Native UI can answer individual questions in separate messages.
                    db.save_partial_answers(interaction["id"], combined)


def validate_answers(interaction, response):
    if not isinstance(response, dict) or not isinstance(response.get("answers"), dict):
        raise ValidationError("User input response must contain answers")
    questions = interaction.get("questions", interaction["payload"].get("questions", []))
    ids = {str(q.get("id", index)) for index, q in enumerate(questions)}
    if set(response["answers"]) != ids:
        raise ValidationError("请回答全部问题，且不要提交未知问题标识")
    for answer in response["answers"].values():
        if (not isinstance(answer, dict) or not isinstance(answer.get("answers"), list)
                or not answer["answers"] or not all(isinstance(a, str) and a.strip() for a in answer["answers"])):
            raise ValidationError("每个问题需要至少一个非空回答")
    return response


def reply_text(interaction, response):
    replies = [{"questionItemId": q["id"], "question": q["question"],
                "answer": "\n".join(response["answers"][q["id"]]["answers"])} for q in interaction["questions"]]
    return REPLY_OPEN + "\n" + json.dumps(replies, ensure_ascii=False) + "\n" + REPLY_CLOSE


def reply_summary(text):
    stripped = text.strip()
    if stripped.startswith(REPLY_OPEN) and stripped.endswith(REPLY_CLOSE):
        try:
            replies = json.loads(stripped[len(REPLY_OPEN):-len(REPLY_CLOSE)])
            replies = replies if isinstance(replies, list) else [replies]
            return "\n\n".join(f"{reply['question']}\n{reply['answer']}" for reply in replies)
        except (ValueError, KeyError, TypeError):
            pass
    return text
