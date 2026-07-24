/**
 * Onboarding data contracts. Mirrors `backend/app/onboarding/schemas.py`
 * and the chat models in `router.py` — change both together.
 */

export type RiskLevel = "conservative" | "balanced" | "aggressive";
export type Market = "us" | "eu" | "pl" | "crypto";
export type VolBand = "low" | "medium" | "high";

export interface RiskEnvelope {
  riskLevel: RiskLevel;
  /** % per quarter vs the tracker, e.g. 2 = "beat the tracker by 2%/quarter" */
  targetReturnPct: number;
  capitalUsd: number;
  market: Market;
}

/** The envelope as the concierge learns it — every slot starts unknown. */
export interface EnvelopeSlots {
  riskLevel: RiskLevel | null;
  targetReturnPct: number | null;
  capitalUsd: number | null;
  market: Market | null;
  /** sector label the user zoomed into, e.g. "Financials" */
  sector: string | null;
  /** stocks picked in chat — pre-checked in the proposal */
  picks: string[];
}

export const EMPTY_SLOTS: EnvelopeSlots = {
  riskLevel: null,
  targetReturnPct: null,
  capitalUsd: null,
  market: null,
  sector: null,
  picks: [],
};

export interface UniverseAsset {
  symbol: string;
  name: string;
  sector: string;
  volBand: VolBand;
  /** indicative paper-desk price (synthetic, deterministic) */
  lastPrice: number;
  change30dPct: number;
  /** 30 daily closes, oldest first */
  history: number[];
}

export interface RiskRules {
  maxPositionPct: number;
  maxDailyDrawdownPct: number;
  stopRule: string;
}

export interface UniverseProposal {
  trackerSymbol: string;
  trackerName: string;
  currency: "USD" | "EUR" | "PLN";
  tradingWindow: string;
  universe: UniverseAsset[];
  rules: RiskRules;
}

export interface ChatTurnMessage {
  role: "user" | "assistant";
  content: string;
}

export interface RadarItem {
  symbol: string;
  name: string;
}

export interface ChatResponse {
  reply: string;
  slots: EnvelopeSlots;
  suggestions: string[];
  proposal: UniverseProposal | null;
  done: boolean;
  candidates: RadarItem[];
  preselect: string[];
}

export const MARKET_LABEL: Record<Market, string> = {
  us: "US equities",
  eu: "EU equities",
  pl: "Warsaw (GPW)",
  crypto: "Crypto",
};
