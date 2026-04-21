import { useEffect, useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

export function LoginPage() {
  const { login, status } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const from = (location.state as { from?: string } | null)?.from ?? "/";

  useEffect(() => {
    if (status === "authenticated") {
      navigate(from, { replace: true });
    }
  }, [status, from, navigate]);

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(username, password);
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-void px-4 py-10">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm space-y-6 rounded-[32px] border border-border bg-panel p-10 shadow-whisper"
      >
        <div className="space-y-2">
          <p className="text-[11px] font-medium uppercase tracking-[0.28em] text-accent">
            Anthropic-inspired ops
          </p>
          <h1 className="font-serif text-3xl font-medium leading-tight tracking-tight text-copy">
            Cooagents
          </h1>
          <p className="text-sm leading-relaxed text-muted">
            请先登录以进入运维控制台。
          </p>
        </div>

        <label className="block space-y-1.5 text-xs font-medium uppercase tracking-[0.16em] text-muted-soft">
          <span>用户名</span>
          <input
            autoFocus
            autoComplete="username"
            name="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="w-full rounded-xl border border-border-strong bg-panel px-4 py-3 text-sm font-normal normal-case tracking-normal text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(56,152,236,0.18)]"
            required
          />
        </label>

        <label className="block space-y-1.5 text-xs font-medium uppercase tracking-[0.16em] text-muted-soft">
          <span>密码</span>
          <input
            type="password"
            autoComplete="current-password"
            name="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-xl border border-border-strong bg-panel px-4 py-3 text-sm font-normal normal-case tracking-normal text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(56,152,236,0.18)]"
            required
          />
        </label>

        {error ? <p className="text-sm text-danger">{error}</p> : null}

        <button
          type="submit"
          disabled={submitting}
          className="w-full rounded-xl bg-accent px-4 py-3 text-sm font-medium text-ink-invert transition hover:bg-accent-soft disabled:opacity-60"
        >
          {submitting ? "登录中..." : "登录"}
        </button>
      </form>
    </div>
  );
}

export default LoginPage;
