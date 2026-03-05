#!/usr/bin/env python3
import argparse
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def render(tpl: str, kv: dict) -> str:
    out = tpl
    for k, v in kv.items():
        out = out.replace("{{" + k + "}}", str(v))
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--run-id", required=True)
    p.add_argument("--ticket", required=True)
    p.add_argument("--stage", choices=["design", "dev"], required=True)
    p.add_argument("--repo", required=True)
    p.add_argument("--worktree", required=True)
    p.add_argument("--req-path", default="")
    p.add_argument("--design-path", default="")
    args = p.parse_args()

    template = ROOT / "templates" / ("TASK-claude.md" if args.stage == "design" else "TASK-codex.md")
    task_dir = ROOT / "tasks" / args.run_id
    task_dir.mkdir(parents=True, exist_ok=True)
    task_file = task_dir / ("design.md" if args.stage == "design" else "dev.md")

    kv = {
        "run_id": args.run_id,
        "ticket": args.ticket,
        "repo_path": args.repo,
        "worktree": args.worktree,
        "req_path": args.req_path or f"docs/req/REQ-{args.ticket}.md",
        "design_path": args.design_path or f"docs/design/DES-{args.ticket}.md",
    }

    content = render(template.read_text(encoding="utf-8"), kv)
    task_file.write_text(content, encoding="utf-8")
    print(task_file)


if __name__ == "__main__":
    main()
