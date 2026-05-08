import type { ReactNode } from "react";

export type StatusTone = "success" | "warning" | "danger" | "accent" | "muted";

const SUCCESS_CLASS =
  "border-[rgba(143,164,106,0.34)] bg-[linear-gradient(180deg,rgba(143,164,106,0.18),rgba(143,164,106,0.08))] text-[#c1cb9a]";
const SUCCESS_DOT = "bg-success";
const ACCENT_CLASS =
  "border-[rgba(169,112,45,0.34)] bg-[linear-gradient(180deg,rgba(169,112,45,0.2),rgba(169,112,45,0.08))] text-accent-soft";
const ACCENT_DOT = "bg-accent";
const HIGHLIGHT_ACCENT_CLASS =
  "border-[rgba(215,154,74,0.58)] bg-[linear-gradient(180deg,rgba(215,154,74,0.28),rgba(169,112,45,0.12))] text-[#f5d7a1] shadow-[0_0_0_1px_rgba(215,154,74,0.22),0_0_20px_rgba(215,154,74,0.16)]";
const WARNING_CLASS =
  "border-[rgba(185,130,54,0.34)] bg-[linear-gradient(180deg,rgba(185,130,54,0.18),rgba(185,130,54,0.08))] text-[#d6a461]";
const WARNING_DOT = "bg-warning";
const DANGER_CLASS =
  "border-[rgba(170,80,61,0.34)] bg-[linear-gradient(180deg,rgba(170,80,61,0.18),rgba(170,80,61,0.08))] text-[#d4876f]";
const DANGER_DOT = "bg-danger";
const MUTED_CLASS =
  "border-border bg-panel-strong/55 text-muted shadow-[inset_0_1px_0_rgba(255,255,255,0.03)]";
const MUTED_DOT = "bg-muted";

function success(label: string) {
  return { label, tone: "success" as StatusTone, className: SUCCESS_CLASS, dotClassName: SUCCESS_DOT };
}

function accent(label: string) {
  return { label, tone: "accent" as StatusTone, className: ACCENT_CLASS, dotClassName: ACCENT_DOT };
}

function highlightedAccent(label: string) {
  return { label, tone: "accent" as StatusTone, className: HIGHLIGHT_ACCENT_CLASS, dotClassName: ACCENT_DOT };
}

function warning(label: string) {
  return { label, tone: "warning" as StatusTone, className: WARNING_CLASS, dotClassName: WARNING_DOT };
}

function danger(label: string) {
  return { label, tone: "danger" as StatusTone, className: DANGER_CLASS, dotClassName: DANGER_DOT };
}

function muted(label: string) {
  return { label, tone: "muted" as StatusTone, className: MUTED_CLASS, dotClassName: MUTED_DOT };
}

const STATUS_META: Record<string, { label: string; tone: StatusTone; className: string; dotClassName: string }> = {
  active: success("在线"),
  approved: success("已通过"),
  cancelled: danger("已取消"),
  completed: highlightedAccent("已完成"),
  dispatched: accent("已分派"),
  failed: danger("失败"),
  interrupted: danger("已中断"),
  offline: danger("离线"),
  queued: warning("排队中"),
  rejected: danger("已驳回"),
  review: warning("审核中"),
  running: success("运行中"),
  starting: muted("启动中"),
  timeout: danger("超时"),
  waiting: warning("等待中"),

  archived: muted("已归档"),

  draft: warning("草稿"),
  published: success("已发布"),
  superseded: muted("已被替代"),

  init: muted("待启动"),
  mode_branch: accent("模式分支"),
  pre_validate: warning("预校验"),
  prompt_compose: accent("提示合成"),
  llm_generate: accent("LLM 生成"),
  mockup: accent("原型"),
  post_validate: warning("后校验"),
  persist: success("持久化"),
  escalated: danger("升级"),

  step1_validate: warning("Step1 校验"),
  step2_iteration: accent("Step2 迭代"),
  step3_context: accent("Step3 上下文"),
  step4_develop: accent("Step4 开发"),
  step5_review: warning("Step5 评审"),

  req_gap: warning("需求缺口"),
  impl_gap: warning("实现缺口"),
  design_hollow: warning("设计空洞"),

  unknown: muted("未知"),
  healthy: success("健康"),
  unhealthy: danger("异常"),
  error: danger("失败"),

  pending: warning("待推送"),
  pushed: success("已推送"),
};

export function resolveStatusBadge(status: string | null | undefined) {
  const normalized = String(status ?? "unknown").toLowerCase();
  return STATUS_META[normalized] ?? {
    label: normalized,
    tone: "muted" as StatusTone,
    className: MUTED_CLASS,
    dotClassName: MUTED_DOT,
  };
}

export function StatusBadge({
  status,
  label,
  icon,
  className = "",
}: {
  status: string | null | undefined;
  label?: string;
  icon?: ReactNode;
  className?: string;
}) {
  const meta = resolveStatusBadge(status);
  const resolvedLabel = label ?? meta.label;

  return (
    <span
      aria-label={resolvedLabel}
      className={`inline-flex shrink-0 items-center gap-2 whitespace-nowrap rounded-full border px-3 py-1 text-xs font-medium shadow-[0_10px_24px_rgba(0,0,0,0.16)] ${meta.className} ${className}`.trim()}
      data-ornament="console-pill"
      data-tone={meta.tone}
      role="status"
    >
      {icon ?? (
        <span
          className={`size-1.5 rounded-full ring-2 ring-black/20 ${meta.dotClassName}`}
          data-badge-dot="true"
        />
      )}
      {resolvedLabel}
    </span>
  );
}
