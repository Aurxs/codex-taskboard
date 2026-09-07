import { useRef, useState } from "react";
import type { Interaction } from "../types";
import { t } from "../i18n";
import { LinearIcon } from "./LinearIcon";

export function PlanQuestions({ interaction, onResolve }: {
  interaction: Interaction;
  onResolve: (interaction: Interaction, response: unknown) => Promise<void>;
}) {
  const questions = interaction.questions ?? [];
  const [index, setIndex] = useState(0);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [custom, setCustom] = useState<Record<string, string>>({});
  const [activeOption, setActiveOption] = useState(0);
  const [busy, setBusy] = useState(false);
  const sending = useRef(false);
  const question = questions[index];
  if (!question) return null;
  const id = question.id ?? String(index);
  const options = question.options ?? [];
  async function respond(value?: string) {
    if (sending.current) return;
    const next = value === undefined ? answers : { ...answers, [id]: value };
    if (value !== undefined) {
      setAnswers(next);
      const unanswered = questions.findIndex((q, i) => !next[q.id ?? String(i)]);
      if (unanswered >= 0) { setIndex(unanswered); setActiveOption(0); return; }
    }
    sending.current = true; setBusy(true);
    try {
      await onResolve(interaction, value === undefined ? { decision: "cancel" } : {
        answers: Object.fromEntries(questions.map((q, i) => {
          const key = q.id ?? String(i);
          return [key, { answers: [next[key]] }];
        })),
      });
    } finally { sending.current = false; setBusy(false); }
  }
  return <form className="plan-question-card" aria-label={t("Codex 需要你的回答")} onSubmit={e => { e.preventDefault(); if (custom[id]?.trim()) void respond(custom[id].trim()); }} onKeyDown={e => {
    if (busy || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLInputElement) return;
    const option = options[Number(e.key) - 1];
    if (/^[1-9]$/.test(e.key) && option) { e.preventDefault(); void respond(option.label); }
  }}>
    <div className="plan-question-heading"><h3 id={`plan-question-${interaction.id}`}>{question.question}</h3>
      {questions.length > 1 && <nav aria-label={t("问题导航")}><button type="button" aria-label={t("上一题")} disabled={busy || index === 0} onClick={() => { setIndex(i => i - 1); setActiveOption(0); }}><LinearIcon name="chevronLeft" /></button><span>{index + 1} of {questions.length}</span><button type="button" aria-label={t("下一题")} disabled={busy || index === questions.length - 1} onClick={() => { setIndex(i => i + 1); setActiveOption(0); }}><LinearIcon name="chevronLeft" className="plan-question-next-icon" /></button></nav>}
      <button type="button" className="plan-question-close" aria-label={t("取消")} disabled={busy} onClick={() => void respond()}><LinearIcon name="close" /></button>
    </div>
    {options.length > 0 && <div className="plan-question-options" aria-labelledby={`plan-question-${interaction.id}`}>
      {options.map((option, i) => <button type="button" className={`plan-question-choice${activeOption === i ? " is-active" : ""}`} key={option.label} disabled={busy} onMouseEnter={() => setActiveOption(i)} onFocus={() => setActiveOption(i)} onClick={() => void respond(option.label)}>
        <span className="plan-question-choice-number" aria-hidden="true">{i + 1}</span>
        <span className="plan-question-choice-text"><strong>{option.label}</strong>{option.description && <small>{option.description}</small>}</span>
        <svg className="plan-question-choice-arrow" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M4 12h16m-7-7 7 7-7 7" /></svg>
      </button>)}
    </div>}
    <div className="plan-question-footer"><label className="plan-question-custom-row"><span className="plan-question-choice-number" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="m15 5 4 4M4 20l4-1L20 7a2.8 2.8 0 0 0-4-4L4 15l-1 5z" /></svg></span><input aria-label={question.question} disabled={busy} value={custom[id] ?? ""} onFocus={() => setActiveOption(-1)} onChange={e => setCustom(current => ({ ...current, [id]: e.target.value }))} placeholder={t("否，并告诉 ChatGPT 应该如何做得不同")} /></label>
      {custom[id]?.trim() ? <button type="submit" className="plan-question-footer-action" disabled={busy}>{t("发送")}</button> : <button type="button" className="plan-question-footer-action" disabled={busy} onClick={() => void respond()}>{t("跳过")}</button>}
    </div>
  </form>;
}
