import Markdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";

type Props = {
  content: string | null | undefined;
  emptyText?: string;
  className?: string;
};

export function MarkdownPanel({ content, emptyText = "暂无内容。", className = "" }: Props) {
  if (!content) {
    return (
      <p className="rounded-[22px] border border-dashed border-border bg-panel-deep/72 px-4 py-6 text-sm text-muted">
        {emptyText}
      </p>
    );
  }

  return (
    <div
      className={`md-prose max-h-[520px] overflow-y-auto rounded-[24px] border border-border bg-panel-deep/86 p-5 shadow-panel ${className}`.trim()}
    >
      <Markdown rehypePlugins={[rehypeSanitize]} remarkPlugins={[remarkGfm]}>
        {content}
      </Markdown>
    </div>
  );
}
