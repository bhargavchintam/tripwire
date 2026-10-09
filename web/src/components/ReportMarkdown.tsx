// Loaded lazily (React.lazy) so react-markdown + remark-gfm stay out of the main chunk.
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

export default function ReportMarkdown({ md }: { md: string }) {
  return (
    <div className="md">
      <Markdown remarkPlugins={[remarkGfm]}>{md}</Markdown>
    </div>
  );
}
