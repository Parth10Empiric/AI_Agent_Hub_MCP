"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * The agent's answer, rendered as formatted text.
 *
 * WHY THIS EXISTS
 *
 * Language models write Markdown. They always have - it is how they
 * were trained to express structure. So an answer arrives looking like
 * this:
 *
 *     Here are the auth details:
 *
 *     **GitHub:**
 *     - Username: `Parth10Empiric`
 *     - Public Repositories: 3
 *
 * Printed as plain text, the reader sees the asterisks and backticks
 * instead of bold text and code. It looks broken, and worse, a table
 * of GitHub issues turns into an unreadable wall of pipe characters.
 *
 * WHY NOT `dangerouslySetInnerHTML`
 *
 * Because the name is accurate. The answer text is not fully under our
 * control - it can contain anything a tool returned from GitHub, Slack
 * or Drive, including an issue title someone wrote to be hostile.
 * Injecting that as HTML is a cross-site scripting hole with a stranger
 * on the other end of it.
 *
 * react-markdown parses to a syntax tree and renders React elements. It
 * does NOT render raw HTML unless you explicitly add `rehype-raw`,
 * which we do not. So `<script>` in a tool result is displayed as text,
 * which is exactly what should happen.
 *
 * `remark-gfm` adds the GitHub flavour: tables, strikethrough, task
 * lists and bare URLs. Tables matter most - "list the open issues"
 * produces one nearly every time.
 */
export function Markdown({ children }: { children: string }) {
  return (
    // `prose-like` styling is written out by hand rather than pulled in
    // from @tailwindcss/typography, so the message bubble keeps its own
    // sizes instead of inheriting a plugin's article defaults.
    <div className="text-sm leading-relaxed [&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children }) => (
            <p className="mb-3 whitespace-pre-wrap break-words">{children}</p>
          ),

          strong: ({ children }) => (
            <strong className="font-semibold">{children}</strong>
          ),

          em: ({ children }) => <em className="italic">{children}</em>,

          ul: ({ children }) => (
            <ul className="mb-3 ml-5 list-disc space-y-1">{children}</ul>
          ),

          ol: ({ children }) => (
            <ol className="mb-3 ml-5 list-decimal space-y-1">{children}</ol>
          ),

          li: ({ children }) => <li className="break-words">{children}</li>,

          h1: ({ children }) => (
            <h1 className="mb-2 mt-4 text-base font-semibold">{children}</h1>
          ),
          h2: ({ children }) => (
            <h2 className="mb-2 mt-4 text-base font-semibold">{children}</h2>
          ),
          h3: ({ children }) => (
            <h3 className="mb-1 mt-3 text-sm font-semibold">{children}</h3>
          ),

          a: ({ href, children }) => (
            <a
              href={href}
              // A link in an answer points somewhere we do not control.
              //
              //   target="_blank"  keeps the conversation open
              //   noopener         stops the new page reaching back
              //                    through window.opener and navigating
              //                    this one somewhere hostile
              //   noreferrer       does not leak where the click came
              //                    from
              target="_blank"
              rel="noopener noreferrer"
              className="underline underline-offset-2 hover:no-underline"
            >
              {children}
            </a>
          ),

          code: ({ className, children }) => {
            // react-markdown does not pass an `inline` flag any more.
            // A fenced block is given a `language-*` class; inline code
            // has no class at all. That is the reliable way to tell
            // them apart, and they need very different styling.
            const isBlock = Boolean(className);

            if (isBlock) {
              return (
                <code className="block font-mono text-xs">{children}</code>
              );
            }

            return (
              <code className="rounded bg-black/10 px-1 py-0.5 font-mono text-[0.85em] dark:bg-white/15">
                {children}
              </code>
            );
          },

          pre: ({ children }) => (
            // overflow-x-auto on the block itself: a long line of code
            // must scroll INSIDE the bubble, never widen the page.
            <pre className="mb-3 overflow-x-auto rounded-md bg-black/10 p-3 dark:bg-white/10">
              {children}
            </pre>
          ),

          blockquote: ({ children }) => (
            <blockquote className="mb-3 border-l-2 border-current/30 pl-3 opacity-90">
              {children}
            </blockquote>
          ),

          table: ({ children }) => (
            // Same reasoning as <pre>: a wide table of GitHub issues
            // scrolls in its own box.
            <div className="mb-3 overflow-x-auto">
              <table className="w-full border-collapse text-xs">
                {children}
              </table>
            </div>
          ),

          th: ({ children }) => (
            <th className="border-b border-current/20 px-2 py-1 text-left font-semibold">
              {children}
            </th>
          ),

          td: ({ children }) => (
            <td className="border-b border-current/10 px-2 py-1 align-top">
              {children}
            </td>
          ),

          hr: () => <hr className="my-4 border-current/20" />,
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
