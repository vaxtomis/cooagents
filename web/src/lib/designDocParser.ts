export const DESIGN_DOC_V2_REQUIRED_SECTIONS = [
  "问题与目标",
  "用户故事",
  "场景案例",
  "范围与非目标",
  "详细操作流程",
  "验收标准",
  "技术约束与集成边界",
  "交付切片",
  "决策记录",
  "打分 rubric",
] as const;

export const DESIGN_DOC_V2_OPTIONAL_SECTIONS = ["页面结构"] as const;

export type DesignDocSectionKey =
  | (typeof DESIGN_DOC_V2_REQUIRED_SECTIONS)[number]
  | (typeof DESIGN_DOC_V2_OPTIONAL_SECTIONS)[number];

export type DesignDocMarkerKind = "tbd" | "assumption";

export interface DesignDocMarker {
  kind: DesignDocMarkerKind;
  text: string;
  section?: DesignDocSectionKey;
}

export interface DesignDocSection {
  key: DesignDocSectionKey;
  heading: string;
  body: string;
  raw: string;
}

export interface ProblemSummary {
  problem?: string;
  evidence?: string;
  keyHypothesis?: string;
  successSignal?: string;
}

export interface ScenarioItem {
  id: string;
  title: string;
  body: string;
  actor?: string;
  trigger?: string;
  preconditions?: string;
  expectedResult?: string;
  markers: DesignDocMarkerKind[];
}

export interface AcceptanceItem {
  id: string;
  text: string;
  checked: boolean;
  markers: DesignDocMarkerKind[];
}

export interface ScopeRow {
  priority: string;
  item: string;
  description: string;
  markers: DesignDocMarkerKind[];
}

export interface DeliverySlice {
  phId: string;
  capability: string;
  dependency: string;
  parallelism: string;
  doneSignal: string;
  markers: DesignDocMarkerKind[];
}

export interface DecisionRow {
  decision: string;
  choice: string;
  alternatives: string;
  rationale: string;
  markers: DesignDocMarkerKind[];
}

export interface RubricRow {
  dimension: string;
  weight: number | null;
  criterion: string;
  markers: DesignDocMarkerKind[];
}

export interface ParsedDesignDocV2 {
  frontMatter: Record<string, string>;
  h1?: string;
  title: string;
  sections: Partial<Record<DesignDocSectionKey, DesignDocSection>>;
  problemSummary: ProblemSummary;
  scenarios: ScenarioItem[];
  acceptanceItems: AcceptanceItem[];
  scopeRows: ScopeRow[];
  deliverySlices: DeliverySlice[];
  decisionRows: DecisionRow[];
  rubricRows: RubricRow[];
  rubricWeightTotal: number;
  markers: DesignDocMarker[];
  warnings: string[];
  counts: {
    scenarios: number;
    acceptance: number;
    deliverySlices: number;
    rubricRows: number;
    warnings: number;
  };
}

interface MarkdownTable {
  headers: string[];
  rows: string[][];
}

const ALL_SECTION_KEYS = new Set<string>([
  ...DESIGN_DOC_V2_REQUIRED_SECTIONS,
  ...DESIGN_DOC_V2_OPTIONAL_SECTIONS,
]);
const PH_ID_RE = /^PH-\d{2,}$/;

export function parseDesignDocV2(markdown: string): ParsedDesignDocV2 {
  const normalized = markdown.replace(/\r\n?/g, "\n");
  const { frontMatter, body } = extractFrontMatter(normalized);
  const h1 = extractH1(body);
  const sections = splitSections(body);
  const warnings = validateRequiredSections(sections);
  const problemSummary = parseProblemSummary(sections["问题与目标"]?.body ?? "");
  const scenarios = parseScenarios(sections["场景案例"]?.body ?? "");
  const acceptanceItems = parseAcceptanceItems(sections["验收标准"]?.body ?? "");
  const scopeRows = parseScopeRows(sections["范围与非目标"]?.body ?? "", warnings);
  const deliverySlices = parseDeliverySlices(sections["交付切片"]?.body ?? "", warnings);
  const decisionRows = parseDecisionRows(sections["决策记录"]?.body ?? "", warnings);
  const rubricRows = parseRubricRows(sections["打分 rubric"]?.body ?? "", warnings);

  if (sections["验收标准"] && acceptanceItems.length === 0) {
    warnings.push("验收标准 未找到 AC-xx checklist 项");
  }

  if (sections["场景案例"] && scenarios.length === 0) {
    warnings.push("场景案例 未找到 SC-xx 小节");
  }

  const rubricWeightTotal = rubricRows.reduce((total, row) => total + (row.weight ?? 0), 0);
  const markers = collectMarkers(sections);

  return {
    frontMatter,
    h1,
    title: frontMatter.title || h1 || "DesignDoc",
    sections,
    problemSummary,
    scenarios,
    acceptanceItems,
    scopeRows,
    deliverySlices,
    decisionRows,
    rubricRows,
    rubricWeightTotal,
    markers,
    warnings,
    counts: {
      scenarios: scenarios.length,
      acceptance: acceptanceItems.length,
      deliverySlices: deliverySlices.length,
      rubricRows: rubricRows.length,
      warnings: warnings.length,
    },
  };
}

export function detectDesignDocMarkers(value: string): DesignDocMarkerKind[] {
  const markers: DesignDocMarkerKind[] = [];
  if (value.includes("TBD - needs research")) markers.push("tbd");
  if (value.includes("Assumption - needs validation")) markers.push("assumption");
  return markers;
}

function extractFrontMatter(markdown: string) {
  if (!markdown.startsWith("---\n")) {
    return { frontMatter: {}, body: markdown };
  }

  const closingIndex = markdown.indexOf("\n---", 4);
  if (closingIndex === -1) {
    return { frontMatter: {}, body: markdown };
  }

  const rawFrontMatter = markdown.slice(4, closingIndex).trim();
  const frontMatter = rawFrontMatter
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .reduce<Record<string, string>>((acc, line) => {
      const separatorIndex = line.indexOf(":");
      if (separatorIndex === -1) return acc;
      const key = line.slice(0, separatorIndex).trim();
      const value = line.slice(separatorIndex + 1).trim();
      return key ? { ...acc, [key]: value } : acc;
    }, {});

  const bodyStart = markdown.slice(closingIndex).startsWith("\n---\n")
    ? closingIndex + "\n---\n".length
    : closingIndex + "\n---".length;
  return { frontMatter, body: markdown.slice(bodyStart).trimStart() };
}

function extractH1(markdown: string) {
  const match = /^#\s+(.+?)\s*$/m.exec(markdown);
  return match?.[1]?.trim();
}

function splitSections(markdown: string): Partial<Record<DesignDocSectionKey, DesignDocSection>> {
  const sections: Partial<Record<DesignDocSectionKey, DesignDocSection>> = {};
  const matches = [...markdown.matchAll(/^##\s+(.+?)\s*$/gm)];

  matches.forEach((match, index) => {
    const heading = match[1].trim();
    if (!ALL_SECTION_KEYS.has(heading)) return;

    const key = heading as DesignDocSectionKey;
    const start = (match.index ?? 0) + match[0].length;
    const end = index + 1 < matches.length ? matches[index + 1].index ?? markdown.length : markdown.length;
    const body = markdown.slice(start, end).trim();
    sections[key] = {
      key,
      heading,
      body,
      raw: markdown.slice(match.index ?? 0, end).trim(),
    };
  });

  return sections;
}

function validateRequiredSections(sections: Partial<Record<DesignDocSectionKey, DesignDocSection>>) {
  return DESIGN_DOC_V2_REQUIRED_SECTIONS.flatMap((section) =>
    sections[section] ? [] : [`缺少章节: ${section}`],
  );
}

function parseProblemSummary(body: string): ProblemSummary {
  return {
    problem: parseLabeledLine(body, "问题"),
    evidence: parseLabeledLine(body, "证据"),
    keyHypothesis: parseLabeledLine(body, "关键假设"),
    successSignal: parseLabeledLine(body, "成功信号"),
  };
}

function parseLabeledLine(body: string, label: string) {
  const escaped = escapeRegExp(label);
  const match = new RegExp(`^\\s*[-*]\\s*${escaped}\\s*[:：]\\s*(.+)$`, "m").exec(body);
  return match?.[1]?.trim();
}

function parseScenarios(body: string): ScenarioItem[] {
  const matches = [...body.matchAll(/^###\s+(SC-\d{2,})\s*(.*?)\s*$/gm)];

  return matches.map((match, index) => {
    const start = (match.index ?? 0) + match[0].length;
    const end = index + 1 < matches.length ? matches[index + 1].index ?? body.length : body.length;
    const scenarioBody = body.slice(start, end).trim();
    return {
      id: match[1],
      title: match[2].trim(),
      body: scenarioBody,
      actor: parseAsciiLabeledLine(scenarioBody, "Actor"),
      trigger: parseAsciiLabeledLine(scenarioBody, "Trigger"),
      preconditions: parseAsciiLabeledLine(scenarioBody, "Preconditions"),
      expectedResult: parseAsciiLabeledLine(scenarioBody, "Expected Result"),
      markers: detectDesignDocMarkers(`${match[0]}\n${scenarioBody}`),
    };
  });
}

function parseAsciiLabeledLine(body: string, label: string) {
  const escaped = escapeRegExp(label);
  const match = new RegExp(`^\\s*[-*]\\s*${escaped}\\s*:\\s*(.+)$`, "m").exec(body);
  return match?.[1]?.trim();
}

function parseAcceptanceItems(body: string): AcceptanceItem[] {
  return [...body.matchAll(/^\s*[-*]\s*\[([ xX])\]\s*(AC-\d{2,})\s*[:：]\s*(.+)$/gm)].map(
    (match) => ({
      id: match[2],
      text: match[3].trim(),
      checked: match[1].toLowerCase() === "x",
      markers: detectDesignDocMarkers(match[3]),
    }),
  );
}

function parseScopeRows(body: string, warnings: string[]): ScopeRow[] {
  const table = findFirstMarkdownTable(body);
  if (!table) return [];

  const missing = missingHeaders(table.headers, ["优先级", "范围项", "说明"]);
  if (missing.length > 0) {
    warnings.push(`范围与非目标 表格缺少列: ${missing.join(", ")}`);
    return [];
  }

  return table.rows.map((row) => {
    const priority = getCell(table, row, "优先级");
    const item = getCell(table, row, "范围项");
    const description = getCell(table, row, "说明");
    return {
      priority,
      item,
      description,
      markers: detectDesignDocMarkers(`${priority} ${item} ${description}`),
    };
  });
}

function parseDeliverySlices(body: string, warnings: string[]): DeliverySlice[] {
  const table = findFirstMarkdownTable(body);
  if (!table) return [];

  const missing = missingHeaders(table.headers, ["PH ID", "能力", "依赖", "可并行性", "完成信号"]);
  if (missing.length > 0) {
    warnings.push(`交付切片 表格缺少列: ${missing.join(", ")}`);
    return [];
  }

  return table.rows.map((row) => {
    const phId = getCell(table, row, "PH ID");
    if (!PH_ID_RE.test(phId)) {
      warnings.push(`交付切片 PH ID 必须匹配 PH-xx: ${phId || "(empty)"}`);
    }

    const capability = getCell(table, row, "能力");
    const dependency = getCell(table, row, "依赖");
    const parallelism = getCell(table, row, "可并行性");
    const doneSignal = getCell(table, row, "完成信号");
    return {
      phId,
      capability,
      dependency,
      parallelism,
      doneSignal,
      markers: detectDesignDocMarkers(`${phId} ${capability} ${dependency} ${parallelism} ${doneSignal}`),
    };
  });
}

function parseDecisionRows(body: string, warnings: string[]): DecisionRow[] {
  const table = findFirstMarkdownTable(body);
  if (!table) return [];

  const missing = missingHeaders(table.headers, ["决策", "选择", "备选", "理由"]);
  if (missing.length > 0) {
    warnings.push(`决策记录 表格缺少列: ${missing.join(", ")}`);
    return [];
  }

  return table.rows.map((row) => {
    const decision = getCell(table, row, "决策");
    const choice = getCell(table, row, "选择");
    const alternatives = getCell(table, row, "备选");
    const rationale = getCell(table, row, "理由");
    return {
      decision,
      choice,
      alternatives,
      rationale,
      markers: detectDesignDocMarkers(`${decision} ${choice} ${alternatives} ${rationale}`),
    };
  });
}

function parseRubricRows(body: string, warnings: string[]): RubricRow[] {
  const table = findFirstMarkdownTable(body);
  if (!table) return [];

  const missing = missingHeaders(table.headers, ["维度", "权重", "判定标准"]);
  if (missing.length > 0) {
    warnings.push(`打分 rubric 表格缺少列: ${missing.join(", ")}`);
    return [];
  }

  return table.rows.map((row) => {
    const dimension = getCell(table, row, "维度");
    const rawWeight = getCell(table, row, "权重");
    const criterion = getCell(table, row, "判定标准");
    const weight = /^\d+$/.test(rawWeight) ? Number(rawWeight) : null;
    if (weight === null) {
      warnings.push(`打分 rubric 权重必须是整数: ${rawWeight || "(empty)"}`);
    }

    return {
      dimension,
      weight,
      criterion,
      markers: detectDesignDocMarkers(`${dimension} ${rawWeight} ${criterion}`),
    };
  });
}

function findFirstMarkdownTable(body: string): MarkdownTable | null {
  const lines = body.split("\n");

  for (let index = 0; index < lines.length - 1; index += 1) {
    const header = lines[index].trim();
    const separator = lines[index + 1].trim();
    if (!isPipeRow(header) || !isSeparatorRow(separator)) continue;

    const rows: string[][] = [];
    for (let rowIndex = index + 2; rowIndex < lines.length; rowIndex += 1) {
      const line = lines[rowIndex].trim();
      if (!isPipeRow(line)) break;
      rows.push(splitPipeRow(line));
    }

    return {
      headers: splitPipeRow(header),
      rows,
    };
  }

  return null;
}

function isPipeRow(line: string) {
  return line.startsWith("|") && line.endsWith("|");
}

function isSeparatorRow(line: string) {
  if (!isPipeRow(line)) return false;
  return splitPipeRow(line).every((cell) => /^:?-{3,}:?$/.test(cell));
}

function splitPipeRow(line: string) {
  return line
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function missingHeaders(headers: string[], required: string[]) {
  return required.filter((header) => !headers.includes(header));
}

function getCell(table: MarkdownTable, row: string[], header: string) {
  const index = table.headers.indexOf(header);
  return index === -1 ? "" : row[index]?.trim() ?? "";
}

function collectMarkers(sections: Partial<Record<DesignDocSectionKey, DesignDocSection>>) {
  return Object.values(sections).flatMap((section) =>
    detectDesignDocMarkers(section.body).map((kind) => ({
      kind,
      text: kind === "tbd" ? "TBD - needs research" : "Assumption - needs validation",
      section: section.key,
    })),
  );
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
