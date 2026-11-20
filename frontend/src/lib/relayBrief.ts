import type { Briefing, MorningBrief } from '../types';

export interface StructuredBrief {
  kind: 'briefing' | 'morning_brief';
  headline: string;
  rows: { label: string; detail: string }[];
  note: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/**
 * Parse a seeded relay message's JSON text into a structured brief.
 * Returns null when the text is not a recognizable brief, so the caller can
 * fall back to plain text. Never trusts the payload as authority — it only
 * extracts display fields from data the Core produced.
 */
export function parseRelayBrief(kind: string, text: string): StructuredBrief | null {
  if (kind !== 'briefing' && kind !== 'morning_brief') return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return null;
  }
  if (!isRecord(parsed)) return null;

  if (kind === 'morning_brief') {
    const brief = parsed as Partial<MorningBrief>;
    if (typeof brief.headline !== 'string' || !Array.isArray(brief.sections)) return null;
    const rows = brief.sections.filter(isRecord).map((section) => ({
      label: typeof section.title === 'string' ? section.title : String(section.key ?? 'Section'),
      detail: Array.isArray(section.lines)
        ? section.lines.filter((line): line is string => typeof line === 'string').join(' · ')
        : '',
    }));
    return {
      kind, headline: brief.headline, rows,
      note: typeof brief.note === 'string' ? brief.note : '',
    };
  }

  const brief = parsed as Partial<Briefing>;
  if (typeof brief.headline !== 'string' || !Array.isArray(brief.items)) return null;
  const rows = brief.items.filter(isRecord).map((item) => ({
    label: typeof item.title === 'string' ? item.title : String(item.kind ?? 'Item'),
    detail: typeof item.reason === 'string' ? item.reason : '',
  }));
  return {
    kind, headline: brief.headline, rows,
    note: typeof brief.note === 'string' ? brief.note : '',
  };
}
