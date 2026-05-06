import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(join(process.cwd(), "src", "index.css"), "utf8");

function token(name: string) {
  const match = css.match(new RegExp(`--${name}:\\s*([^;]+);`));
  return match?.[1]?.trim();
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
});
