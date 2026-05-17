import { AlertTriangle } from "lucide-react";
import { useMemo, useState } from "react";
import {
  DESIGN_DOC_V2_OPTIONAL_SECTIONS,
  DESIGN_DOC_V2_REQUIRED_SECTIONS,
  detectDesignDocMarkers,
  parseDesignDocV2,
  type DesignDocMarkerKind,
  type DesignDocSection,
  type DesignDocSectionKey,
  type ParsedDesignDocV2,
} from "../lib/designDocParser";
import { MarkdownBody, MarkdownPanel } from "./MarkdownPanel";
import { SegmentedControl } from "./SegmentedControl";

type ViewMode = "structured" | "markdown";

interface Props {
  content: string;
}

const VIEW_OPTIONS = [
  { value: "structured", label: "结构化" },
  { value: "markdown", label: "Markdown" },
] as const;

const SECTION_ANCHOR_IDS: Record<DesignDocSectionKey, string> = {
  问题与目标: "problem-goal",
  用户故事: "user-story",
  场景案例: "scenarios",
  范围与非目标: "scope",
  详细操作流程: "operation-flow",
  验收标准: "acceptance",
  技术约束与集成边界: "technical-boundary",
  交付切片: "delivery-slices",
  决策记录: "decisions",
  "打分 rubric": "rubric",
  页面结构: "page-structure",
};

export function DesignDocStructuredViewer({ content }: Props) {
  const [viewMode, setViewMode] = useState<ViewMode>("structured");
  const parsed = useMemo(() => parseDesignDocV2(content), [content]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[11px] font-medium uppercase tracking-[0.24em] text-accent-soft">
            DesignDoc v2
          </p>
          <h3 className="mt-1 break-words text-xl font-semibold leading-snug text-copy">
            {parsed.title}
          </h3>
          {parsed.frontMatter.goal ? (
            <p className="mt-1 max-w-3xl text-sm text-muted">{parsed.frontMatter.goal}</p>
          ) : null}
        </div>
        <SegmentedControl
          ariaLabel="DesignDoc 展示模式"
          onChange={setViewMode}
          options={VIEW_OPTIONS}
          value={viewMode}
        />
      </div>

      {viewMode === "markdown" ? (
        <MarkdownPanel content={content} reader />
      ) : (
        <StructuredDesignDoc parsed={parsed} />
      )}
    </div>
  );
}

function StructuredDesignDoc({ parsed }: { parsed: ParsedDesignDocV2 }) {
  const navItems = [...DESIGN_DOC_V2_REQUIRED_SECTIONS, ...DESIGN_DOC_V2_OPTIONAL_SECTIONS]
    .filter((key) => parsed.sections[key])
    .map((key) => ({
      key,
      href: `#design-doc-${SECTION_ANCHOR_IDS[key]}`,
      count: getSectionCount(parsed, key),
    }));

  return (
    <div className="space-y-4">
      {parsed.warnings.length > 0 ? <WarningList warnings={parsed.warnings} /> : null}

      <SummaryMetrics parsed={parsed} />

      <div className="grid gap-5 lg:grid-cols-[13rem_minmax(0,1fr)]">
        <nav
          aria-label="DesignDoc 章节"
          className="self-start rounded-2xl border border-border bg-panel-strong/42 p-3 lg:sticky lg:top-4"
        >
          <p className="px-2 text-[11px] font-medium uppercase tracking-[0.22em] text-muted-soft">
            章节
          </p>
          <div className="mt-2 flex gap-2 overflow-x-auto lg:block lg:space-y-1 lg:overflow-visible">
            {navItems.map((item) => (
              <a
                className="flex shrink-0 items-center justify-between gap-3 rounded-xl px-2.5 py-2 text-xs text-muted transition hover:bg-panel-deep/70 hover:text-copy"
                href={item.href}
                key={item.key}
              >
                <span>{item.key}</span>
                {item.count !== null ? (
                  <span className="rounded-full border border-border bg-panel-deep px-2 py-0.5 font-mono text-[10px] text-muted-soft">
                    {item.count}
                  </span>
                ) : null}
              </a>
            ))}
          </div>
        </nav>

        <div className="min-w-0 space-y-5">
          <ProblemGoalSection parsed={parsed} />
          <MarkdownSection section={parsed.sections["用户故事"]} />
          <ScenarioSection parsed={parsed} />
          <ScopeSection parsed={parsed} />
          <MarkdownSection section={parsed.sections["详细操作流程"]} />
          <AcceptanceSection parsed={parsed} />
          <MarkdownSection section={parsed.sections["技术约束与集成边界"]} />
          <DeliverySliceSection parsed={parsed} />
          <DecisionSection parsed={parsed} />
          <RubricSection parsed={parsed} />
          <MarkdownSection section={parsed.sections["页面结构"]} />
        </div>
      </div>
    </div>
  );
}

function SummaryMetrics({ parsed }: { parsed: ParsedDesignDocV2 }) {
  const items = [
    { label: "版本", value: parsed.frontMatter.version || "-" },
    { label: "Rubric 阈值", value: parsed.frontMatter.rubric_threshold || "-" },
    { label: "前端 mockup", value: formatMockupFlag(parsed.frontMatter.needs_frontend_mockup) },
    { label: "场景", value: String(parsed.counts.scenarios) },
    { label: "验收项", value: String(parsed.counts.acceptance) },
    { label: "交付切片", value: String(parsed.counts.deliverySlices) },
    { label: "Rubric 权重", value: String(parsed.rubricWeightTotal) },
    { label: "解析告警", value: String(parsed.counts.warnings) },
  ];

  return (
    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
      {items.map((item) => (
        <div className="border-t border-border/70 px-1 py-2" key={item.label}>
          <p className="text-[11px] uppercase tracking-[0.18em] text-muted-soft">{item.label}</p>
          <p className="mt-1 break-words font-mono text-sm text-copy">{item.value}</p>
        </div>
      ))}
    </div>
  );
}

function ProblemGoalSection({ parsed }: { parsed: ParsedDesignDocV2 }) {
  if (!parsed.sections["问题与目标"]) return null;

  const rows = [
    { label: "问题", value: parsed.problemSummary.problem },
    { label: "证据", value: parsed.problemSummary.evidence },
    { label: "关键假设", value: parsed.problemSummary.keyHypothesis },
    { label: "成功信号", value: parsed.problemSummary.successSignal },
  ];

  return (
    <SectionBlock id={SECTION_ANCHOR_IDS["问题与目标"]} title="问题与目标">
      <div className="grid gap-3 md:grid-cols-2">
        {rows.map((row) => (
          <div className="border-l border-border/80 pl-3" key={row.label}>
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-muted-soft">
                {row.label}
              </p>
              <MarkerBadges markers={detectDesignDocMarkers(row.value ?? "")} />
            </div>
            <p className="mt-1 text-sm leading-6 text-copy-soft">{row.value || "-"}</p>
          </div>
        ))}
      </div>
    </SectionBlock>
  );
}

function ScenarioSection({ parsed }: { parsed: ParsedDesignDocV2 }) {
  if (!parsed.sections["场景案例"]) return null;

  return (
    <SectionBlock id={SECTION_ANCHOR_IDS["场景案例"]} title="场景案例">
      {parsed.scenarios.length === 0 ? (
        <EmptyStructuredLine copy="未解析到 SC-xx 场景。" />
      ) : (
        <div className="space-y-3">
          {parsed.scenarios.map((scenario) => (
            <article className="border-l border-border/80 pl-3" key={scenario.id}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-xs text-accent-soft">{scenario.id}</span>
                <h4 className="text-sm font-semibold text-copy">{scenario.title || "未命名场景"}</h4>
                <MarkerBadges markers={scenario.markers} />
              </div>
              <dl className="mt-2 grid gap-2 text-xs text-muted md:grid-cols-2">
                <KeyValue label="Actor" value={scenario.actor} />
                <KeyValue label="Trigger" value={scenario.trigger} />
                <KeyValue label="Preconditions" value={scenario.preconditions} />
                <KeyValue label="Expected" value={scenario.expectedResult} />
              </dl>
            </article>
          ))}
        </div>
      )}
    </SectionBlock>
  );
}

function ScopeSection({ parsed }: { parsed: ParsedDesignDocV2 }) {
  if (!parsed.sections["范围与非目标"]) return null;

  return (
    <SectionBlock id={SECTION_ANCHOR_IDS["范围与非目标"]} title="范围与非目标">
      {parsed.scopeRows.length === 0 ? (
        <MarkdownSectionBody section={parsed.sections["范围与非目标"]} />
      ) : (
        <TableWrap>
          <table className="min-w-full text-left text-sm">
            <thead>
              <tr>
                <TableHead>优先级</TableHead>
                <TableHead>范围项</TableHead>
                <TableHead>说明</TableHead>
              </tr>
            </thead>
            <tbody>
              {parsed.scopeRows.map((row, index) => (
                <TableRow key={`${row.priority}-${row.item}-${index}`}>
                  <TableCell mono>{row.priority}</TableCell>
                  <TableCell>{row.item}</TableCell>
                  <TableCell>
                    <InlineWithMarkers markers={row.markers} value={row.description} />
                  </TableCell>
                </TableRow>
              ))}
            </tbody>
          </table>
        </TableWrap>
      )}
    </SectionBlock>
  );
}

function AcceptanceSection({ parsed }: { parsed: ParsedDesignDocV2 }) {
  if (!parsed.sections["验收标准"]) return null;

  return (
    <SectionBlock id={SECTION_ANCHOR_IDS["验收标准"]} title="验收标准">
      {parsed.acceptanceItems.length === 0 ? (
        <EmptyStructuredLine copy="未解析到 AC-xx checklist 项。" />
      ) : (
        <TableWrap>
          <table className="min-w-full text-left text-sm">
            <thead>
              <tr>
                <TableHead>AC</TableHead>
                <TableHead>状态</TableHead>
                <TableHead>标准</TableHead>
              </tr>
            </thead>
            <tbody>
              {parsed.acceptanceItems.map((item) => (
                <TableRow key={item.id}>
                  <TableCell mono>{item.id}</TableCell>
                  <TableCell>{item.checked ? "已勾选" : "未勾选"}</TableCell>
                  <TableCell>
                    <InlineWithMarkers markers={item.markers} value={item.text} />
                  </TableCell>
                </TableRow>
              ))}
            </tbody>
          </table>
        </TableWrap>
      )}
    </SectionBlock>
  );
}

function DeliverySliceSection({ parsed }: { parsed: ParsedDesignDocV2 }) {
  if (!parsed.sections["交付切片"]) return null;

  return (
    <SectionBlock id={SECTION_ANCHOR_IDS["交付切片"]} title="交付切片">
      {parsed.deliverySlices.length === 0 ? (
        <EmptyStructuredLine copy="未解析到 PH-xx 交付切片。" />
      ) : (
        <TableWrap>
          <table className="min-w-full text-left text-sm">
            <thead>
              <tr>
                <TableHead>PH</TableHead>
                <TableHead>能力</TableHead>
                <TableHead>依赖</TableHead>
                <TableHead>可并行性</TableHead>
                <TableHead>完成信号</TableHead>
              </tr>
            </thead>
            <tbody>
              {parsed.deliverySlices.map((slice, index) => (
                <TableRow key={`${slice.phId}-${index}`}>
                  <TableCell mono>{slice.phId}</TableCell>
                  <TableCell>{slice.capability}</TableCell>
                  <TableCell>{slice.dependency}</TableCell>
                  <TableCell>{slice.parallelism}</TableCell>
                  <TableCell>
                    <InlineWithMarkers markers={slice.markers} value={slice.doneSignal} />
                  </TableCell>
                </TableRow>
              ))}
            </tbody>
          </table>
        </TableWrap>
      )}
    </SectionBlock>
  );
}

function DecisionSection({ parsed }: { parsed: ParsedDesignDocV2 }) {
  if (!parsed.sections["决策记录"]) return null;

  return (
    <SectionBlock id={SECTION_ANCHOR_IDS["决策记录"]} title="决策记录">
      {parsed.decisionRows.length === 0 ? (
        <MarkdownSectionBody section={parsed.sections["决策记录"]} />
      ) : (
        <TableWrap>
          <table className="min-w-full text-left text-sm">
            <thead>
              <tr>
                <TableHead>决策</TableHead>
                <TableHead>选择</TableHead>
                <TableHead>备选</TableHead>
                <TableHead>理由</TableHead>
              </tr>
            </thead>
            <tbody>
              {parsed.decisionRows.map((row, index) => (
                <TableRow key={`${row.decision}-${index}`}>
                  <TableCell>{row.decision}</TableCell>
                  <TableCell>{row.choice}</TableCell>
                  <TableCell>{row.alternatives}</TableCell>
                  <TableCell>
                    <InlineWithMarkers markers={row.markers} value={row.rationale} />
                  </TableCell>
                </TableRow>
              ))}
            </tbody>
          </table>
        </TableWrap>
      )}
    </SectionBlock>
  );
}

function RubricSection({ parsed }: { parsed: ParsedDesignDocV2 }) {
  if (!parsed.sections["打分 rubric"]) return null;

  return (
    <SectionBlock
      id={SECTION_ANCHOR_IDS["打分 rubric"]}
      title="打分 rubric"
      accessory={`权重合计 ${parsed.rubricWeightTotal}`}
    >
      {parsed.rubricRows.length === 0 ? (
        <MarkdownSectionBody section={parsed.sections["打分 rubric"]} />
      ) : (
        <TableWrap>
          <table className="min-w-full text-left text-sm">
            <thead>
              <tr>
                <TableHead>维度</TableHead>
                <TableHead>权重</TableHead>
                <TableHead>判定标准</TableHead>
              </tr>
            </thead>
            <tbody>
              {parsed.rubricRows.map((row, index) => (
                <TableRow key={`${row.dimension}-${index}`}>
                  <TableCell>{row.dimension}</TableCell>
                  <TableCell mono>{row.weight === null ? "-" : String(row.weight)}</TableCell>
                  <TableCell>
                    <InlineWithMarkers markers={row.markers} value={row.criterion} />
                  </TableCell>
                </TableRow>
              ))}
            </tbody>
          </table>
        </TableWrap>
      )}
    </SectionBlock>
  );
}

function MarkdownSection({ section }: { section?: DesignDocSection }) {
  if (!section) return null;

  return (
    <SectionBlock id={SECTION_ANCHOR_IDS[section.key]} title={section.heading}>
      <MarkdownSectionBody section={section} />
    </SectionBlock>
  );
}

function MarkdownSectionBody({ section }: { section: DesignDocSection }) {
  return (
    <div className="md-prose max-w-none text-sm">
      <MarkdownBody content={section.body} />
    </div>
  );
}

function SectionBlock({
  id,
  title,
  accessory,
  children,
}: {
  id: string;
  title: string;
  accessory?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="scroll-mt-5 border-t border-border/70 pt-4 first:border-t-0 first:pt-0" id={`design-doc-${id}`}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-base font-semibold text-copy">{title}</h3>
        {accessory ? (
          <span className="rounded-full border border-border bg-panel-strong px-3 py-1 font-mono text-[11px] text-muted">
            {accessory}
          </span>
        ) : null}
      </div>
      {children}
    </section>
  );
}

function WarningList({ warnings }: { warnings: string[] }) {
  return (
    <div className="rounded-2xl border border-warning/25 bg-warning/10 p-4 text-warning">
      <div className="flex items-center gap-2">
        <AlertTriangle aria-hidden="true" className="h-4 w-4" />
        <p className="text-sm font-semibold">解析告警</p>
      </div>
      <ul className="mt-2 list-disc space-y-1 pl-5 text-xs">
        {warnings.map((warning) => (
          <li key={warning}>{warning}</li>
        ))}
      </ul>
    </div>
  );
}

function MarkerBadges({ markers }: { markers: DesignDocMarkerKind[] }) {
  if (markers.length === 0) return null;

  return (
    <>
      {markers.map((marker) => (
        <span
          className="rounded-full border border-warning/25 bg-warning/10 px-2 py-0.5 text-[10px] font-medium text-warning"
          key={marker}
        >
          {marker === "tbd" ? "TBD" : "Assumption"}
        </span>
      ))}
    </>
  );
}

function InlineWithMarkers({ value, markers }: { value: string; markers: DesignDocMarkerKind[] }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <span>{value || "-"}</span>
      <MarkerBadges markers={markers} />
    </span>
  );
}

function KeyValue({ label, value }: { label: string; value?: string }) {
  return (
    <div>
      <dt className="font-mono text-[10px] uppercase tracking-[0.16em] text-muted-soft">{label}</dt>
      <dd className="mt-0.5 text-copy-soft">{value || "-"}</dd>
    </div>
  );
}

function TableWrap({ children }: { children: React.ReactNode }) {
  return <div className="overflow-x-auto rounded-2xl border border-border/80">{children}</div>;
}

function TableHead({ children }: { children: React.ReactNode }) {
  return (
    <th className="border-b border-border/80 bg-panel-strong/70 px-3 py-2 text-xs font-semibold text-copy">
      {children}
    </th>
  );
}

function TableRow({ children }: { children: React.ReactNode }) {
  return <tr className="border-b border-border/60 last:border-b-0">{children}</tr>;
}

function TableCell({ children, mono = false }: { children: React.ReactNode; mono?: boolean }) {
  return (
    <td className={["px-3 py-2 align-top text-copy-soft", mono ? "font-mono text-xs text-copy" : ""].join(" ")}>
      {children}
    </td>
  );
}

function EmptyStructuredLine({ copy }: { copy: string }) {
  return <p className="rounded-2xl border border-dashed border-border px-4 py-4 text-sm text-muted">{copy}</p>;
}

function getSectionCount(parsed: ParsedDesignDocV2, key: DesignDocSectionKey) {
  if (key === "场景案例") return parsed.counts.scenarios;
  if (key === "验收标准") return parsed.counts.acceptance;
  if (key === "交付切片") return parsed.counts.deliverySlices;
  if (key === "打分 rubric") return parsed.counts.rubricRows;
  return null;
}

function formatMockupFlag(value: string | undefined) {
  if (value === "true") return "需要";
  if (value === "false") return "不需要";
  return "-";
}
