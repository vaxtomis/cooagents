import { useState, type FormEvent } from "react";
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

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(username, password);
      navigate(from, { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败");
    } finally {
      setSubmitting(false);
    }
  }

  if (status === "authenticated") {
    navigate(from, { replace: true });
    return null;
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-black/80 px-4">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm space-y-5 rounded-[28px] border border-white/8 bg-panel p-8 shadow-panel"
      >
        <div className="space-y-1">
          <h1 className="text-xl font-semibold text-white">Cooagents</h1>
          <p className="text-sm text-muted">请先登录</p>
        </div>

        <label className="block space-y-1 text-sm text-muted">
          <span>用户名</span>
          <input
            autoFocus
            autoComplete="username"
            name="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="w-full rounded-2xl border border-white/8 bg-black/18 px-4 py-3 text-sm text-white outline-none focus:border-accent/40"
            required
          />
        </label>

        <label className="block space-y-1 text-sm text-muted">
          <span>密码</span>
          <input
            type="password"
            autoComplete="current-password"
            name="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-2xl border border-white/8 bg-black/18 px-4 py-3 text-sm text-white outline-none focus:border-accent/40"
            required
          />
        </label>

        {error ? <p className="text-sm text-danger">{error}</p> : null}

        <button
          type="submit"
          disabled={submitting}
          className="w-full rounded-2xl bg-accent px-4 py-3 text-sm font-medium text-black transition hover:bg-accent/90 disabled:opacity-60"
        >
          {submitting ? "登录中..." : "登录"}
        </button>
      </form>
    </div>
  );
}

export default LoginPage;
