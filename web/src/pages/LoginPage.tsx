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
    <div className="flex min-h-screen items-center justify-center px-4 py-10">
      <form
        className="relative w-full max-w-sm overflow-hidden rounded-[34px] border border-border-strong bg-panel/96 p-10 shadow-shell"
        onSubmit={handleSubmit}
      >
        <div className="pointer-events-none absolute inset-[1px] rounded-[33px] border border-white/4" />
        <div className="pointer-events-none absolute inset-x-8 top-0 h-px bg-[linear-gradient(90deg,transparent,rgba(169,112,45,0.8),transparent)]" />

        <div className="relative space-y-6">
          <div className="space-y-2">
            <p className="text-[11px] font-medium uppercase tracking-[0.28em] text-accent-soft">
              Cooagents 控制台
            </p>
            <h1 className="text-3xl font-semibold leading-tight tracking-[-0.05em] text-copy">
              Cooagents
            </h1>
            <p className="text-sm leading-relaxed text-muted">请先登录以进入运维控制台。</p>
          </div>

          <label className="block space-y-1.5 text-xs font-medium uppercase tracking-[0.16em] text-muted-soft">
            <span>用户名</span>
            <input
              autoComplete="username"
              autoFocus
              className="w-full rounded-[16px] border border-border bg-panel-deep px-4 py-3 text-sm font-normal normal-case tracking-normal text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(208,160,90,0.18)]"
              name="username"
              onChange={(e) => setUsername(e.target.value)}
              required
              value={username}
            />
          </label>

          <label className="block space-y-1.5 text-xs font-medium uppercase tracking-[0.16em] text-muted-soft">
            <span>密码</span>
            <input
              autoComplete="current-password"
              className="w-full rounded-[16px] border border-border bg-panel-deep px-4 py-3 text-sm font-normal normal-case tracking-normal text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(208,160,90,0.18)]"
              name="password"
              onChange={(e) => setPassword(e.target.value)}
              required
              type="password"
              value={password}
            />
          </label>

          {error ? <p className="text-sm text-danger">{error}</p> : null}

          <button
            className="w-full rounded-[16px] border border-[rgba(169,112,45,0.34)] bg-[linear-gradient(180deg,rgba(169,112,45,0.28),rgba(169,112,45,0.14))] px-4 py-3 text-sm font-medium text-copy shadow-[0_14px_30px_rgba(0,0,0,0.24)] transition hover:border-[rgba(208,160,90,0.45)] hover:bg-[linear-gradient(180deg,rgba(208,160,90,0.34),rgba(169,112,45,0.16))] disabled:opacity-60"
            disabled={submitting}
            type="submit"
          >
            {submitting ? "登录中..." : "登录"}
          </button>
        </div>
      </form>
    </div>
  );
}

export default LoginPage;
