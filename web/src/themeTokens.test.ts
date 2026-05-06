import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(join(process.cwd(), "src", "index.css"), "utf8");

function token(name: string) {
  const match = css.match(new RegExp(`--${name}:\\s*([^;]+);`));
  return match?.[1]?.trim();
}

function hexToRgb(hex: string) {
  const normalized = hex.replace("#", "");
  return [
    Number.parseInt(normalized.slice(0, 2), 16),
    Number.parseInt(normalized.slice(2, 4), 16),
    Number.parseInt(normalized.slice(4, 6), 16),
  ] as const;
}

function luminance([r, g, b]: readonly [number, number, number]) {
  const [rs, gs, bs] = [r, g, b].map((channel) => {
    const scaled = channel / 255;
    return scaled <= 0.03928 ? scaled / 12.92 : ((scaled + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * rs + 0.7152 * gs + 0.0722 * bs;
}

function contrastRatio(foreground: string, background: string) {
  const fg = luminance(hexToRgb(foreground));
  const bg = luminance(hexToRgb(background));
  const light = Math.max(fg, bg);
  const dark = Math.min(fg, bg);
  return (light + 0.05) / (dark + 0.05);
}

describe("console theme tokens", () => {
  it("anchors the palette to the extracted dark mechanical cutouts", () => {
    expect(token("color-void")).toBe("#050505");
    expect(token("color-panel")).toBe("#10100d");
    expect(token("color-panel-strong")).toBe("#181713");
    expect(token("color-accent")).toBe("#a9702d");
    expect(token("color-focus")).toBe("#d0a05a");
  });

  it("does not keep the earlier parchment and blue-focus palette in global tokens", () => {
    expect(css).not.toContain("#efe1c2");
    expect(css).not.toContain("#c99a54");
    expect(css).not.toContain("#8bbcff");
  });

  it("keeps text readable on the extracted dark surfaces", () => {
    expect(contrastRatio(token("color-copy")!, token("color-panel")!)).toBeGreaterThan(12);
    expect(contrastRatio(token("color-copy-soft")!, token("color-panel")!)).toBeGreaterThan(8);
    expect(contrastRatio(token("color-muted")!, token("color-panel")!)).toBeGreaterThan(6);
  });

  it("keeps brass ornament layers subtle enough for dense information panels", () => {
    expect(token("console-ornament-opacity")).toBe("0.18");
    expect(token("console-gold-sheen")).toBe("rgba(208, 160, 90, 0.06)");
    expect(css).not.toContain("opacity: 0.45");
    expect(css).not.toContain("rgba(208, 160, 90, 0.16)");
  });
});
