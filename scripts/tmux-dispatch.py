#!/usr/bin/env python3
import argparse
import subprocess


def run(cmd):
    p = subprocess.run(cmd, text=True, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(f"cmd failed: {' '.join(cmd)}\n{p.stdout}\n{p.stderr}")
    return p


def tmux_has(session):
    return subprocess.run(["tmux", "has-session", "-t", session], capture_output=True).returncode == 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--session", required=True)
    p.add_argument("--workdir", required=True)
    p.add_argument("--agent-cmd", required=True, help="claude or codex")
    p.add_argument("--task-file", required=True)
    p.add_argument("--role", choices=["claude", "codex"], required=True)
    args = p.parse_args()

    if not tmux_has(args.session):
        run(["tmux", "new-session", "-d", "-s", args.session, "-c", args.workdir])
        run(["tmux", "send-keys", "-t", args.session, args.agent_cmd, "Enter"])

    prompt = (
        f"请先阅读任务文件：{args.task_file}。\n"
        f"阅读后先创建 ACK 文件，然后开始执行任务。\n"
        f"如果是 {args.role} 阶段，请严格按任务单中的输入/输出路径执行。"
    )
    run(["tmux", "send-keys", "-t", args.session, "-l", "--", prompt])
    run(["tmux", "send-keys", "-t", args.session, "Enter"])
    print(f"dispatched {args.role} task to session={args.session}")


if __name__ == "__main__":
    main()
