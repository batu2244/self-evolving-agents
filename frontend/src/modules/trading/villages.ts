import type { DecisionMemo, Fill, OutcomeRecord } from "./types";

/** A village is bound to exactly one instrument — one ETF, stock or pair.
 * Its agents debate and trade that instrument and nothing else. Villages are
 * derived from the ratified desk mandates plus any symbol that shows up in
 * the trading record (fills, memos, graded outcomes). */
export interface Village {
  id: string; // url slug of the symbol
  symbol: string; // "NVDA", "BTC/USD", …
  name: string; // asset name when known, else the symbol
  members: string[];
}

export const ANALYSTS = ["sentiment", "realtime", "historical"] as const;
export const CORE_MEMBERS = [...ANALYSTS, "pm", "evaluator"];

/** Agents are shown by TYPE — one villager per archetype. Internal ids map
 * to their type here; unmapped ids are their own type. */
export const AGENT_TYPE_LABELS: Record<string, string> = {
  newsflow: "sentiment",
  newsdesk: "sentiment",
  tape: "realtime",
  trend: "historical",
  openrange: "intraday",
};

export function agentLabel(name: string): string {
  return AGENT_TYPE_LABELS[name] ?? name;
}

const TYPE_ORDER = ["sentiment", "realtime", "historical", "intraday"];

/** The committee the XTB test set established — when several ids share a
 * type, these are the representatives to show. */
const PREFERRED_IDS: Record<string, string> = {
  sentiment: "newsflow",
  realtime: "tape",
  historical: "trend",
  intraday: "openrange",
};

/** Standing villages — always on the board even before their first cycle. */
export const DEFAULT_SYMBOLS = ["GOOGL", "NVDA"];
const DEFAULT_NAMES: Record<string, string> = {
  GOOGL: "Alphabet Inc.",
  NVDA: "NVIDIA Corp.",
  "XTB.WA": "XTB S.A.",
};

export function villageId(symbol: string): string {
  return symbol.toLowerCase().replace(/[^a-z0-9]+/g, "-");
}

export function buildVillages(
  symbols: string[],
  names: Record<string, string> = {},
  data?: { memos?: DecisionMemo[]; outcomes?: OutcomeRecord[] },
): Village[] {
  const unique = [...new Set([...symbols, ...DEFAULT_SYMBOLS])].sort();
  return unique.map((symbol) => {
    // The villagers are whoever actually voted or got graded on this
    // instrument — collapsed to ONE id per agent type. The XTB committee
    // ids are the preferred representatives; otherwise the deepest record
    // wins. The core desk staffs villages with no record yet.
    const seen = new Set<string>();
    for (const memo of data?.memos ?? []) {
      for (const d of memo.decisions) {
        if (d.ticker !== symbol) continue;
        for (const v of d.votes) seen.add(v.analyst);
      }
    }
    for (const o of data?.outcomes ?? []) {
      if (o.ticker === symbol) seen.add(o.agent);
    }

    const byType = new Map<string, { id: string; graded: number }>();
    for (const id of seen) {
      const type = agentLabel(id);
      if (PREFERRED_IDS[type] && seen.has(PREFERRED_IDS[type])) {
        byType.set(type, { id: PREFERRED_IDS[type], graded: Infinity });
        continue;
      }
      const graded = (data?.outcomes ?? []).filter(
        (o) => o.ticker === symbol && o.agent === id,
      ).length;
      const current = byType.get(type);
      if (!current || graded > current.graded) byType.set(type, { id, graded });
    }
    const members = [...byType.entries()]
      .sort(([a], [b]) => {
        const ia = TYPE_ORDER.indexOf(a);
        const ib = TYPE_ORDER.indexOf(b);
        return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib) || a.localeCompare(b);
      })
      .map(([, v]) => v.id);

    return {
      id: villageId(symbol),
      symbol,
      name: names[symbol] ?? DEFAULT_NAMES[symbol] ?? symbol,
      members: members.length > 0 ? members : CORE_MEMBERS,
    };
  });
}

export function findVillage(villages: Village[], id: string): Village | undefined {
  return villages.find((v) => v.id === id);
}

/** Every symbol the desk is mandated for or has actually touched — ratifying
 * a desk in onboarding creates a village for each selected stock. */
export function collectSymbols(sources: {
  mandates?: string[];
  fills?: Fill[];
  memos?: DecisionMemo[];
  outcomes?: OutcomeRecord[];
}): string[] {
  return [
    ...(sources.mandates ?? []),
    ...(sources.fills ?? []).map((f) => f.symbol),
    ...(sources.memos ?? []).flatMap((m) => m.decisions.map((d) => d.ticker)),
    ...(sources.outcomes ?? []).flatMap((o) => (o.ticker ? [o.ticker] : [])),
  ];
}
