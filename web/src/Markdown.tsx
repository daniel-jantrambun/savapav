import type { ReactNode } from "react";

/**
 * Rendu du markdown simple que produit le LLM : titres, listes (numérotées ou non), paragraphes, gras, italique,
 * code. Construit des éléments React, jamais de HTML injecté : le texte du LLM ne peut rien exécuter.
 */

// `id` : rang d'apparition dans le texte, clé React stable pour un même texte.
type Line = { id: number; text: string };
type Item = Line & { children: Line[] };
type Block = { id: number } & (
  | { kind: "heading"; text: string }
  | { kind: "paragraph"; lines: Line[] }
  | { kind: "ul"; items: Item[] }
  | { kind: "ol"; start: number; items: Item[] }
);

const HEADING = /^#{1,6}\s+(.*)$/;
const ORDERED = /^\s*(\d+)[.)]\s+(.*)$/;
const UNORDERED = /^\s*[-*•+]\s+(.*)$/;

export function parseMarkdown(text: string): Block[] {
  const blocks: Block[] = [];
  let current: Block | null = null;
  let id = 0;
  const toLine = (text: string): Line => ({ id: id++, text });
  const toItem = (text: string): Item => ({ ...toLine(text), children: [] });
  for (const line of text.split("\n")) {
    if (!line.trim()) {
      current = null;
      continue;
    }
    const heading = line.match(HEADING);
    const ordered = line.match(ORDERED);
    const unordered = line.match(UNORDERED);
    if (heading) {
      blocks.push({ id: id++, kind: "heading", text: heading[1] });
      current = null;
    } else if (ordered) {
      const item = toItem(ordered[2]);
      if (current?.kind === "ol") {
        current.items.push(item);
      } else {
        current = { id: id++, kind: "ol", start: Number(ordered[1]), items: [item] };
        blocks.push(current);
      }
    } else if (unordered) {
      if (current?.kind === "ol") {
        // « 1. **Séance** » suivi de puces : les puces détaillent la séance.
        current.items[current.items.length - 1].children.push(toLine(unordered[1]));
      } else if (current?.kind === "ul") {
        current.items.push(toItem(unordered[1]));
      } else {
        current = { id: id++, kind: "ul", items: [toItem(unordered[1])] };
        blocks.push(current);
      }
    } else if (current?.kind === "paragraph") {
      current.lines.push(toLine(line.trim()));
    } else if (current?.kind === "ul" || current?.kind === "ol") {
      // Suite d'un élément de liste sur la ligne suivante.
      const last = current.items[current.items.length - 1];
      if (last.children.length) last.children[last.children.length - 1].text += ` ${line.trim()}`;
      else last.text += ` ${line.trim()}`;
    } else {
      current = { id: id++, kind: "paragraph", lines: [toLine(line.trim())] };
      blocks.push(current);
    }
  }
  return blocks;
}

const INLINE = /(\*\*[^*]+\*\*|__[^_]+__|\*[^*\s][^*]*\*|_[^_\s][^_]*_|`[^`]+`)/g;

function inline(text: string): ReactNode[] {
  return text.split(INLINE).map((part, i) => {
    const key = `${i}-${part}`;
    if (/^(\*\*|__).+\1$/.test(part)) return <strong key={key}>{part.slice(2, -2)}</strong>;
    if (/^`.+`$/.test(part)) return <code key={key}>{part.slice(1, -1)}</code>;
    if (/^([*_]).+\1$/.test(part)) return <em key={key}>{part.slice(1, -1)}</em>;
    return part;
  });
}

function ListItems({ items }: { items: Item[] }) {
  return items.map((item) => (
    <li key={item.id}>
      {inline(item.text)}
      {item.children.length > 0 && (
        <ul className="mt-1 list-disc space-y-0.5 pl-5">
          {item.children.map((child) => (
            <li key={child.id}>{inline(child.text)}</li>
          ))}
        </ul>
      )}
    </li>
  ));
}

export function Markdown({ text, className = "" }: { text: string; className?: string }) {
  return (
    <div className={`space-y-2 [&_strong]:font-semibold [&_strong]:text-ink ${className}`}>
      {parseMarkdown(text).map((block) => {
        const key = block.id;
        switch (block.kind) {
          case "heading":
            return (
              <p key={key} className="font-semibold text-ink">
                {inline(block.text)}
              </p>
            );
          case "paragraph":
            return (
              <p key={key}>
                {block.lines.map((line, j) => (
                  <span key={line.id}>
                    {j > 0 && <br />}
                    {inline(line.text)}
                  </span>
                ))}
              </p>
            );
          case "ul":
            return (
              <ul key={key} className="list-disc space-y-1 pl-5">
                <ListItems items={block.items} />
              </ul>
            );
          case "ol":
            return (
              <ol key={key} start={block.start} className="list-decimal space-y-1 pl-5">
                <ListItems items={block.items} />
              </ol>
            );
          default:
            return null;
        }
      })}
    </div>
  );
}
