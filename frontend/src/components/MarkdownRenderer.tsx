import { memo } from "react";
import ReactMarkdown from "react-markdown";
import { PrismLight as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";

// Register only the languages Origin actually uses (saves ~600KB)
import typescript from "react-syntax-highlighter/dist/esm/languages/prism/typescript";
import javascript from "react-syntax-highlighter/dist/esm/languages/prism/javascript";
import python from "react-syntax-highlighter/dist/esm/languages/prism/python";
import bash from "react-syntax-highlighter/dist/esm/languages/prism/bash";
import json from "react-syntax-highlighter/dist/esm/languages/prism/json";
import css from "react-syntax-highlighter/dist/esm/languages/prism/css";
import sql from "react-syntax-highlighter/dist/esm/languages/prism/sql";
import yaml from "react-syntax-highlighter/dist/esm/languages/prism/yaml";
import markdown from "react-syntax-highlighter/dist/esm/languages/prism/markdown";
import jsx from "react-syntax-highlighter/dist/esm/languages/prism/jsx";
import tsx from "react-syntax-highlighter/dist/esm/languages/prism/tsx";

SyntaxHighlighter.registerLanguage("typescript", typescript);
SyntaxHighlighter.registerLanguage("ts", typescript);
SyntaxHighlighter.registerLanguage("javascript", javascript);
SyntaxHighlighter.registerLanguage("js", javascript);
SyntaxHighlighter.registerLanguage("python", python);
SyntaxHighlighter.registerLanguage("py", python);
SyntaxHighlighter.registerLanguage("bash", bash);
SyntaxHighlighter.registerLanguage("sh", bash);
SyntaxHighlighter.registerLanguage("shell", bash);
SyntaxHighlighter.registerLanguage("json", json);
SyntaxHighlighter.registerLanguage("css", css);
SyntaxHighlighter.registerLanguage("sql", sql);
SyntaxHighlighter.registerLanguage("yaml", yaml);
SyntaxHighlighter.registerLanguage("yml", yaml);
SyntaxHighlighter.registerLanguage("markdown", markdown);
SyntaxHighlighter.registerLanguage("md", markdown);
SyntaxHighlighter.registerLanguage("jsx", jsx);
SyntaxHighlighter.registerLanguage("tsx", tsx);

interface Props {
  content: string;
}

/**
 * Renders markdown content with syntax-highlighted code blocks.
 * Memoized to avoid re-renders on sibling state changes.
 */
export const MarkdownRenderer = memo(function MarkdownRenderer({ content }: Props) {
  return (
    <ReactMarkdown
      components={{
        // ── Fenced code blocks ──────────────────────────
        code({ node, className, children, ...props }) {
          const match = /language-(\w+)/.exec(className || "");
          const codeStr = String(children).replace(/\n$/, "");

          // Inline code (no language class, single line)
          if (!match) {
            return (
              <code className="inline-code" {...props}>
                {children}
              </code>
            );
          }

          // Block code with syntax highlighting
          return (
            <div className="code-block">
              <div className="code-block__header">
                <span className="code-block__lang">{match[1]}</span>
                <button
                  className="code-block__copy"
                  onClick={() => navigator.clipboard.writeText(codeStr)}
                  title="Copy code"
                >
                  COPY
                </button>
              </div>
              <SyntaxHighlighter
                style={oneDark}
                language={match[1]}
                PreTag="div"
                customStyle={{
                  margin: 0,
                  borderRadius: "0 0 4px 4px",
                  fontSize: "12px",
                  lineHeight: "1.5",
                  background: "#1a1d23",
                }}
              >
                {codeStr}
              </SyntaxHighlighter>
            </div>
          );
        },

        // ── Links open in new tab ───────────────────────
        a({ href, children }) {
          return (
            <a href={href} target="_blank" rel="noopener noreferrer" className="md-link">
              {children}
            </a>
          );
        },

        // ── Tables ──────────────────────────────────────
        table({ children }) {
          return (
            <div className="md-table-wrap">
              <table className="md-table">{children}</table>
            </div>
          );
        },

        // ── Blockquotes ─────────────────────────────────
        blockquote({ children }) {
          return <blockquote className="md-blockquote">{children}</blockquote>;
        },
      }}
    >
      {content}
    </ReactMarkdown>
  );
});
