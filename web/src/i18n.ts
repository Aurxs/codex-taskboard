import { useSyncExternalStore } from "react";
import { en } from "./locales/en";

export type Locale = "zh-CN" | "en";
export type MessageKey = keyof typeof en;

export function normalizeLocale(language?: string): Locale {
  return /^zh(?:-(?:hans(?:-[a-z]{2})?|cn|sg))?$/i.test(language?.trim().replaceAll("_", "-") ?? "") ? "zh-CN" : "en";
}

let locale: Locale = "en";
const listeners = new Set<() => void>();
export const getLocale = () => locale;

/** The trusted parent supplies Codex's language, including live changes. No
 * storage is required: production runs in an opaque, sandboxed iframe. */
export function setHostLanguage(language?: string) {
  const next = normalizeLocale(language);
  if (next === locale) return;
  locale = next;
  listeners.forEach(listener => listener());
}

export function useLocale() {
  return useSyncExternalStore(listener => {
    listeners.add(listener);
    return () => { listeners.delete(listener); };
  }, getLocale);
}

export function t(key: MessageKey, ...values: Array<string | number>): string {
  const message = locale === "zh-CN" ? key : en[key];
  return message.replace(/\{(\d+)\}/g, (placeholder, index: string) => String(values[Number(index)] ?? placeholder));
}

/** Translate only known application messages, never task content or Codex output. */
export function localizeError(message: string): string {
  const patterns: [RegExp, MessageKey][] = [
    [/^等待前序独占任务 (.+)$/, "等待前序独占任务 {0}"],
    [/^等待任务 (.+) 的修改范围释放或合入$/, "等待任务 {0} 的修改范围释放或合入"],
    [/^修改超出声明范围：(.*)$/, "修改超出声明范围：{0}"],
    [/^合并修复超出声明范围：(.*)$/, "合并修复超出声明范围：{0}"],
  ];
  for (const [pattern, key] of patterns) { const match = message.match(pattern); if (match) return t(key, match[1]); }
  if (Object.hasOwn(en, message)) return t(message as MessageKey);
  if (locale === "zh-CN") {
    const entry = Object.entries(en).find(([, english]) => english === message);
    if (entry) return entry[0];
  }
  return message;
}
