import "@testing-library/jest-dom/vitest";

const NativeRequest = globalThis.Request;

class CompatibleRequest extends NativeRequest {
  constructor(input: RequestInfo | URL, init?: RequestInit) {
    if (init && "signal" in init) {
      const { signal: _signal, ...rest } = init;
      super(input, rest);
      return;
    }

    super(input, init);
  }
}

Object.defineProperty(globalThis, "Request", {
  configurable: true,
  value: CompatibleRequest,
  writable: true,
});

if (typeof window !== "undefined") {
  Object.defineProperty(window, "Request", {
    configurable: true,
    value: CompatibleRequest,
    writable: true,
  });
}
