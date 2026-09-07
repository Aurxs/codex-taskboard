import { useRef, useState } from "react";
import type { Interaction } from "../types";
import { t } from "../i18n";
import { LinearIcon } from "./LinearIcon";

/** The planning question occupies the same slot as the native-style composer. */
export function PlanQuestions({ interaction, onResolve }: {
  interaction: Interaction;
  onResolve: (interaction: Interaction, response: unknown) => Promise<void>;
}) {
  const questions = interaction.questions ?? [];
  const [index, setIndex] = useState(0);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [custom, setCustom] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const sending = useRef(false);
  const question = questions[index];
  if (!question) return null;
  const id = question.id ?? String(index);
  const options = question.options ?? [];
  const value = (key: string) => custom[key]?.trim() || answers[key] || "";
  const complete = questions.every((q, i) => value(q.id ?? String(i)));
  const last = index === questions.length - 1;
  function select(label: string) {
    setAnswers(current => ({ ...current, [id]: label }));
    setCustom(current => ({ ...current, [id]: "" }));
  }
  async function resolve() {
    if (sending.current || !complete) return;
    sending.current = true; setBusy(true);
    try {
      await onResolve(interaction, {
        answers: Object.fromEntries(questions.map((q, i) => {
          const key = q.id ?? String(i);
          return [key, { answers: [value(key)] }];
        })),
      });
    } finally { sending.current = false; setBusy(false); }
  }
  return <form className="plan-question-card" aria-label={t("Codex 需要你的回答")} onSubmit={e => { e.preventDefault(); if (!value(id) || busy) return; if (last) void resolve(); else setIndex(i => i + 1); }} onKeyDown={e => {
    if (busy || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLInputElement) return;
    const option = options[Number(e.key) - 1];
    if (/^[1-9]$/.test(e.key) && option) { e.preventDefault(); select(option.label); }
  }}>
    <div className="plan-question-topline"><span>{question.header}</span>{questions.length > 1 && <nav aria-label={t("问题导航")}><button type="button" aria-label={t("上一题")} disabled={busy || index === 0} onClick={() => setIndex(i => i - 1)}><LinearIcon name="chevronLeft" /></button><span>{index + 1} / {questions.length}</span><button type="button" aria-label={t("查看下一题")} disabled={busy || last} onClick={() => setIndex(i => i + 1)}><LinearIcon name="chevronLeft" className="plan-question-next-icon" /></button></nav>}</div>
    <h3 id={`plan-question-${interaction.id}`}>{question.question}</h3>
    {options.length > 0 && <div className="plan-question-options" role="radiogroup" aria-labelledby={`plan-question-${interaction.id}`}>
      {options.map((option, i) => {
        const selected = !custom[id]?.trim() && answers[id] === option.label;
        return <button type="button" role="radio" aria-checked={selected} className={`plan-question-choice${selected ? " is-selected" : ""}`} key={option.label} disabled={busy} onClick={() => select(option.label)}>
          <span className="plan-question-choice-number" aria-hidden="true">{selected ? <LinearIcon name="check" /> : i + 1}</span>
          <span className="plan-question-choice-text"><strong>{option.label}</strong>{option.description && <small>{option.description}</small>}</span>
        </button>;
      })}
    </div>}
    <textarea className="plan-question-custom" aria-label={question.question} rows={options.length ? 1 : 2} disabled={busy} value={custom[id] ?? ""} onChange={e => setCustom(current => ({ ...current, [id]: e.target.value }))} placeholder={options.length ? t("或者，输入其他答案…") : t("输入你的回答…")} onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); e.currentTarget.form?.requestSubmit(); } }} />
    <div className="plan-question-footer"><button type="submit" className="plan-question-submit" disabled={busy || !value(id) || last && !complete}>{last ? t("提交回答") : t("下一题")}<span aria-hidden="true">↗</span></button></div>
  </form>;
}
