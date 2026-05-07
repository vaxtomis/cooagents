import { useMemo } from "react";
import useSWR from "swr";
import hljs from "highlight.js/lib/core";
import bash from "highlight.js/lib/languages/bash";
import css from "highlight.js/lib/languages/css";
import go from "highlight.js/lib/languages/go";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import markdown from "highlight.js/lib/languages/markdown";
import python from "highlight.js/lib/languages/python";
import rust from "highlight.js/lib/languages/rust";
import sql from "highlight.js/lib/languages/sql";
import typescript from "highlight.js/lib/languages/typescript";
import xml from "highlight.js/lib/languages/xml";
import yaml from "highlight.js/lib/languages/yaml";
import "highlight.js/styles/atom-one-dark.css";

import { repoBlob } from "../../api/repos";
import { EmptyState } from "../../components/SectionPanel";
import { formatBytes } from "../../lib/formatBytes";
import type { RepoBlob } from "../../types";

hljs.registerLanguage("bash", bash);
hljs.registerLanguage("css", css);
hljs.registerLanguage("go", go);
hljs.registerLanguage("javascript", javascript);
hljs.registerLanguage("json", json);
hljs.registerLanguage("markdown", markdown);
hljs.registerLanguage("python", python);
hljs.registerLanguage("rust", rust);
hljs.registerLanguage("sql", sql);
hljs.registerLanguage("typescript", typescript);
hljs.registerLanguage("xml", xml);
hljs.registerLanguage("yaml", yaml);

const EXT_TO_LANG: Record<string, string> = {
  ts: "typescript",
  tsx: "typescript",
  mts: "typescript",
  cts: "typescript",
  js: "javascript",
  jsx: "javascript",
  mjs: "javascript",
  cjs: "javascript",
  py: "python",
  rs: "rust",
  go: "go",
  sh: "bash",
  bash: "bash",
  zsh: "bash",
  sql: "sql",
  css: "css",
  md: "markdown",
  markdown: "markdown",
  yml: "yaml",
  yaml: "yaml",
  json: "json",
  html: "xml",
  xml: "xml",
  svg: "xml",
};

function languageForPath(path: string): string | null {
  const i = path.lastIndexOf(".");
  if (i < 0) return null;
  const ext = path.slice(i + 1).toLowerCase();
  return EXT_TO_LANG[ext] ?? null;
}

interface Props {
  repoId: string;
  gitRef: string;
  path: string | null;
  refreshToken: number;
}

export function BlobViewer({ repoId, gitRef, path, refreshToken }: Props) {
  const query = useSWR<RepoBlob | null>(
    path && gitRef ? ["repo-blob", repoId, gitRef, path, refreshToken] : null,
    () => (path ? repoBlob(repoId, { ref: gitRef, path }) : null),
  );

  const highlighted = useMemo(() => {
    const blob = query.data;
    if (!blob || blob.binary || blob.content == null || blob.content === "") {
      return null;
    }
    const lang = languageForPath(blob.path);
    try {
      if (lang) {
        return hljs.highlight(blob.content, {
          language: lang,
          ignoreIllegals: true,
        }).value;
      }
      const auto = hljs.highlightAuto(blob.content);
      return auto.relevance >= 10 ? auto.value : null;
    } catch {
      return null;
    }
  }, [query.data]);

  if (!path) {
    return <EmptyState copy="点击左侧文件预览内容。" />;
  }
  if (query.error) {
    return (
      <p className="rounded-2xl border border-danger/15 bg-danger/8 p-3 text-xs text-danger">
        文件加载失败：{String((query.error as Error).message ?? query.error)}
      </p>
    );
  }
  if (!query.data) {
    return <p className="text-xs text-muted">加载中...</p>;
  }
  const blob = query.data;
  return (
    <article className="space-y-2">
      <header className="space-y-1">
        <p className="font-mono text-xs text-copy">{blob.path}</p>
        <p className="text-xs text-muted-soft">
          {formatBytes(blob.size)}
          {blob.binary ? " · 二进制" : ""}
        </p>
      </header>
      {blob.binary ? (
        <p className="rounded-2xl border border-border bg-panel-strong/40 p-3 text-sm text-muted">
          二进制文件，暂不支持预览。
        </p>
      ) : highlighted ? (
        <pre className="overflow-x-auto rounded-2xl bg-panel-deep p-3 text-[12px]">
          {/*
            ``highlighted`` is the return value of ``hljs.highlight()`` /
            ``hljs.highlightAuto()``; highlight.js v11 HTML-escapes the
            input before tokenising, so this string is safe to inject.
            Regression test: RepoDetailPage.test.tsx "escapes script tags
            in highlighted blob". Do not feed any other source of HTML
            into this prop.
          */}
          <code
            className="hljs"
            dangerouslySetInnerHTML={{ __html: highlighted }}
          />
        </pre>
      ) : (
        <pre className="overflow-x-auto whitespace-pre-wrap rounded-2xl bg-panel-deep p-3 font-mono text-[12px] text-copy">
          {blob.content ?? ""}
        </pre>
      )}
    </article>
  );
}
