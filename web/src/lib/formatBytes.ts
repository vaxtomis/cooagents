const UNITS = ["B", "KiB", "MiB", "GiB"] as const;

export function formatBytes(size: number | null): string {
  if (size === null || size < 0 || !Number.isFinite(size)) return "—";
  let value = size;
  let i = 0;
  while (value >= 1024 && i < UNITS.length - 1) {
    value /= 1024;
    i += 1;
  }
  const fixed = i === 0 ? value.toFixed(0) : value.toFixed(1);
  return `${fixed} ${UNITS[i]}`;
}
