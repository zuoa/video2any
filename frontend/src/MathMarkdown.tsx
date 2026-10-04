import ReactMarkdown from "react-markdown";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";

const remarkPlugins = [remarkMath];
const rehypePlugins = [[rehypeKatex, { strict: "ignore", trust: false, throwOnError: false }]] as NonNullable<Parameters<typeof ReactMarkdown>[0]["rehypePlugins"]>;

/** Raw HTML stays disabled; LaTeX cannot load remote resources or trusted HTML. */
export function MathMarkdown({ text }: { text: string }) {
  return <div className="math-markdown"><ReactMarkdown remarkPlugins={remarkPlugins} rehypePlugins={rehypePlugins} components={{ img: () => null }}>{text}</ReactMarkdown></div>;
}
