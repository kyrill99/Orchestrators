import { Fragment, type ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * Small, safe markdown renderer for issue text: headings, paragraphs, lists,
 * block quotes, fenced code, inline code, bold, italics and links. It builds
 * React elements (never raw HTML), so user text cannot inject markup.
 */
export function Markdown({
  source,
  className,
}: {
  source: string;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "space-y-3 text-[14px] leading-relaxed break-words text-foreground/90",
        className,
      )}
    >
      {blocks(source)}
    </div>
  );
}

function blocks(source: string): ReactNode[] {
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const out: ReactNode[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i++;
      continue;
    }
    const fence = line.match(/^\s*```/);
    if (fence) {
      const code: string[] = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i++]);
      i++;
      out.push(
        <pre
          key={out.length}
          className="overflow-x-auto rounded-md bg-muted px-3 py-2 font-mono text-[12px] leading-normal"
        >
          <code>{code.join("\n")}</code>
        </pre>,
      );
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      const level = heading[1].length;
      out.push(
        <p
          key={out.length}
          role="heading"
          aria-level={level}
          className={cn(
            "font-semibold text-foreground",
            level === 1 ? "text-[17px]" : level === 2 ? "text-[15px]" : "text-[14px]",
          )}
        >
          {inline(heading[2])}
        </p>,
      );
      i++;
      continue;
    }
    if (/^\s*>/.test(line)) {
      const quote: string[] = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) {
        quote.push(lines[i++].replace(/^\s*>\s?/, ""));
      }
      out.push(
        <blockquote
          key={out.length}
          className="border-l-2 border-border pl-3 text-muted-foreground"
        >
          {blocks(quote.join("\n"))}
        </blockquote>,
      );
      continue;
    }
    const listItem = /^\s*([-*+]|\d+[.)])\s+/;
    if (listItem.test(line)) {
      const ordered = /^\s*\d/.test(line);
      const items: string[] = [];
      while (i < lines.length && listItem.test(lines[i])) {
        let item = lines[i++].replace(listItem, "");
        // indented continuation lines
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !listItem.test(lines[i])) {
          item += " " + lines[i++].trim();
        }
        items.push(item);
      }
      const List = ordered ? "ol" : "ul";
      out.push(
        <List
          key={out.length}
          className={cn("space-y-1 pl-5", ordered ? "list-decimal" : "list-disc")}
        >
          {items.map((item, n) => (
            <li key={n}>{inline(item.replace(/^\[( |x)\]\s+/i, (m) => (m[1] === " " ? "☐ " : "☑ ")))}</li>
          ))}
        </List>,
      );
      continue;
    }
    const para: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^(\s*```|#{1,6}\s|\s*>|\s*([-*+]|\d+[.)])\s+)/.test(lines[i])
    ) {
      para.push(lines[i++]);
    }
    out.push(
      <p key={out.length}>
        {para.map((text, n) => (
          <Fragment key={n}>
            {n > 0 ? <br /> : null}
            {inline(text)}
          </Fragment>
        ))}
      </p>,
    );
  }
  return out;
}

const INLINE =
  /(`[^`]+`)|(\*\*[^*]+\*\*|__[^_]+__)|(\*[^*\s][^*]*\*|_[^_\s][^_]*_)|(\[[^\]]+\]\([^)\s]+\))|(https?:\/\/[^\s<>()]+[^\s<>().,;:!?'"])/g;

function safeHref(href: string): string | null {
  return /^(https?:\/\/|mailto:|\/|#)/i.test(href) ? href : null;
}

function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(INLINE)) {
    const at = match.index ?? 0;
    if (at > last) out.push(text.slice(last, at));
    const [token, code, bold, italic, link, url] = match;
    const key = out.length;
    if (code) {
      out.push(
        <code key={key} className="rounded bg-muted px-1 py-px font-mono text-[12px]">
          {code.slice(1, -1)}
        </code>,
      );
    } else if (bold) {
      out.push(<strong key={key} className="font-semibold text-foreground">{inline(bold.slice(2, -2))}</strong>);
    } else if (italic) {
      out.push(<em key={key}>{inline(italic.slice(1, -1))}</em>);
    } else if (link) {
      const [, label, href] = link.match(/^\[([^\]]+)\]\(([^)\s]+)\)$/) ?? [];
      const safe = safeHref(href ?? "");
      out.push(
        safe ? (
          <a key={key} href={safe} target="_blank" rel="noreferrer" className="text-foreground underline underline-offset-2 hover:opacity-70">
            {label}
          </a>
        ) : (
          token
        ),
      );
    } else if (url) {
      out.push(
        <a key={key} href={url} target="_blank" rel="noreferrer" className="text-foreground underline underline-offset-2 hover:opacity-70">
          {url}
        </a>,
      );
    }
    last = at + token.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}
