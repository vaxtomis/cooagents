import Markdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";

type Props = {
  content: string | null | undefined;
  emptyText?: string;
  className?: string;
  reader?: boolean;
};

export function MarkdownBody({ content }: { content: string }) {
  return (
    <Markdown rehypePlugins={[rehypeSanitize]} remarkPlugins={[remarkGfm]}>
      {content}
    </Markdown>
  );
}

export function MarkdownPanel({
  content,
  emptyText = "暂无内容。",
  className = "",
  reader = false,
}: Props) {
  if (!content) {
    return (
      <p className="rounded-[22px] border border-dashed border-border bg-panel-deep/72 px-4 py-6 text-sm text-muted">
        {emptyText}
      </p>
    );
  }

  const readingAreaClass = reader
    ? "max-h-[calc(100vh-18rem)] min-h-[540px]"
    : "max-h-[520px]";

  return (
    <div
      className={`md-prose ${readingAreaClass} overflow-y-auto rounded-[24px] border border-border bg-panel-deep/86 p-5 shadow-panel ${className}`.trim()}
    >
      <MarkdownBody content={content} />
    </div>
  );
}
