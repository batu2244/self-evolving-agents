import type { RiskEnvelope, UniverseProposal } from "./types";

const KEY = "deltadesk.envelope.v2";

export interface StoredDesk {
  envelope: RiskEnvelope;
  proposal: UniverseProposal;
  /** symbols the user staffed to the committee */
  selected: string[];
  ratifiedAt: string;
}

export function saveDesk(desk: StoredDesk): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(desk));
  } catch {
    // storage unavailable (private mode) — the backend copy is authoritative
  }
}

export function loadDesk(): StoredDesk | null {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as StoredDesk;
    if (!parsed?.envelope?.riskLevel || !parsed?.proposal?.trackerSymbol) return null;
    if (!Array.isArray(parsed.selected)) return null;
    return parsed;
  } catch {
    return null;
  }
}

export function clearDesk(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    // ignore
  }
}
