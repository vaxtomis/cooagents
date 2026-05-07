interface ParsePolicyOverridesInput {
  maxLabel: string;
  maxRaw: string;
  thresholdLabel: string;
  thresholdRaw: string;
}

interface ParsedPolicyOverrides {
  error: string | null;
  maxValue: number | undefined;
  thresholdValue: number | undefined;
}

interface PolicyOverrideFieldsProps {
  fieldClassName: string;
  maxAriaLabel: string;
  maxValue: string;
  onMaxChange: (value: string) => void;
  thresholdAriaLabel: string;
  thresholdValue: string;
  onThresholdChange: (value: string) => void;
}

function parseOptionalBoundedInt(
  raw: string,
  label: string,
  min: number,
  max: number,
): { error: string | null; value: number | undefined } {
  const trimmed = raw.trim();
  if (!trimmed) return { error: null, value: undefined };
  if (!/^\d+$/.test(trimmed)) {
    return { error: `${label} must be an integer from ${min} to ${max}`, value: undefined };
  }
  const value = Number(trimmed);
  if (value < min || value > max) {
    return { error: `${label} must be an integer from ${min} to ${max}`, value: undefined };
  }
  return { error: null, value };
}

export function parsePolicyOverrides({
  maxLabel,
  maxRaw,
  thresholdLabel,
  thresholdRaw,
}: ParsePolicyOverridesInput): ParsedPolicyOverrides {
  const parsedMax = parseOptionalBoundedInt(maxRaw, maxLabel, 0, 50);
  if (parsedMax.error) {
    return { error: parsedMax.error, maxValue: undefined, thresholdValue: undefined };
  }
  const parsedThreshold = parseOptionalBoundedInt(thresholdRaw, thresholdLabel, 1, 100);
  if (parsedThreshold.error) {
    return { error: parsedThreshold.error, maxValue: undefined, thresholdValue: undefined };
  }
  return {
    error: null,
    maxValue: parsedMax.value,
    thresholdValue: parsedThreshold.value,
  };
}

export function PolicyOverrideFields({
  fieldClassName,
  maxAriaLabel,
  maxValue,
  onMaxChange,
  thresholdAriaLabel,
  thresholdValue,
  onThresholdChange,
}: PolicyOverrideFieldsProps) {
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <label className="space-y-1.5 text-sm text-muted">
        <span>最大循环次数</span>
        <input
          aria-label={maxAriaLabel}
          className={fieldClassName}
          inputMode="numeric"
          min={0}
          max={50}
          placeholder="global default"
          type="number"
          value={maxValue}
          onChange={(event) => onMaxChange(event.target.value)}
        />
      </label>
      <label className="space-y-1.5 text-sm text-muted">
        <span>准出分值</span>
        <input
          aria-label={thresholdAriaLabel}
          className={fieldClassName}
          inputMode="numeric"
          min={1}
          max={100}
          placeholder="global default"
          type="number"
          value={thresholdValue}
          onChange={(event) => onThresholdChange(event.target.value)}
        />
      </label>
    </div>
  );
}
