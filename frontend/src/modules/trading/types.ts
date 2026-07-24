export type TradeDirection = "buy" | "sell" | "hold";

export interface Vote {
  analyst: string;
  ticker: string;
  direction: TradeDirection;
  signal: number;
  confidence: number;
  size_class: "full" | "half" | "probe";
  rationale: string;
}

export interface TickerDecision {
  ticker: string;
  direction: TradeDirection;
  size_factor: number;
  vote_share: number;
  unanimous: boolean;
  votes: Vote[];
}

export interface DecisionMemo {
  cycle_id: string;
  decisions: TickerDecision[];
  weights: Record<string, number>;
  narrative: string;
}

export interface Fill {
  symbol: string;
  side?: "buy" | "sell";
  qty?: number;
  price?: number;
}

export interface OutcomeRecord {
  agent: string;
  ticker?: string | null;
  score: number;
  credibility?: number;
  ts?: string;
}
