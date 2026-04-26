import { describe, expect, it } from "vitest";
import { formatBytes } from "./formatBytes";

describe("formatBytes", () => {
  it("returns em dash for null", () => {
    expect(formatBytes(null)).toBe("—");
  });

  it("returns em dash for negative or non-finite", () => {
    expect(formatBytes(-1)).toBe("—");
    expect(formatBytes(Number.POSITIVE_INFINITY)).toBe("—");
    expect(formatBytes(Number.NaN)).toBe("—");
  });

  it("formats zero as bytes without decimal", () => {
    expect(formatBytes(0)).toBe("0 B");
  });

  it("formats kibibytes with one decimal", () => {
    expect(formatBytes(1024)).toBe("1.0 KiB");
  });

  it("formats mebibytes", () => {
    expect(formatBytes(1_500_000)).toBe("1.4 MiB");
  });

  it("caps at gibibytes", () => {
    expect(formatBytes(5 * 1024 * 1024 * 1024)).toBe("5.0 GiB");
  });
});
