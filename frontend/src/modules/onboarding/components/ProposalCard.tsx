import { useState } from "react";
import { OnboardingApiError, ratifyEnvelope } from "../api";
import { saveDesk, type StoredDesk } from "../storage";
import type { RiskEnvelope, UniverseProposal, VolBand } from "../types";

function VolBadge({ band }: { band: VolBand }) {
  const tone = band === "low" ? "text-phosphor" : band === "medium" ? "text-amber" : "text-loss";
  return <span className={`text-data text-xs uppercase ${tone}`}>{band}</span>;
}

function Sparkline({ history, up }: { history: number[]; up: boolean }) {
  if (history.length < 2) return null;
  const w = 72;
  const h = 20;
  const min = Math.min(...history);
  const max = Math.max(...history);
  const span = max - min || 1;
  const points = history
    .map(
      (v, i) =>
        `${((i / (history.length - 1)) * w).toFixed(1)},${(h - ((v - min) / span) * h).toFixed(1)}`,
    )
    .join(" ");
  return (
    <svg
      width={w}
      height={h}
      viewBox={`0 0 ${w} ${h}`}
      className="overflow-visible"
      role="img"
      aria-label={`30-day price trend, ${up ? "up" : "down"}`}
    >
      <polyline
        points={points}
        fill="none"
        stroke={up ? "var(--color-phosphor)" : "var(--color-loss)"}
        strokeWidth="1.25"
        strokeLinejoin="round"
        opacity="0.85"
      />
    </svg>
  );
}

function fmtPrice(v: number): string {
  return v.toLocaleString("en-US", {
    minimumFractionDigits: v < 1 ? 4 : 2,
    maximumFractionDigits: v < 1 ? 4 : 2,
  });
}

export function ProposalCard(props: {
  envelope: RiskEnvelope;
  proposal: UniverseProposal;
  onRatified: (desk: StoredDesk) => void;
}) {
  const { proposal } = props;
  // starts empty on purpose: the user actively picks the stock(s) the
  // committee will trade
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ratified, setRatified] = useState(false);

  const toggle = (symbol: string) => {
    if (ratified) return;
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(symbol)) next.delete(symbol);
      else next.add(symbol);
      return next;
    });
  };

  const ratify = () => {
    const picks = proposal.universe.map((a) => a.symbol).filter((s) => selected.has(s));
    setBusy(true);
    setError(null);
    ratifyEnvelope(props.envelope, proposal, picks)
      .then(() => {
        const desk: StoredDesk = {
          envelope: props.envelope,
          proposal,
          selected: picks,
          ratifiedAt: new Date().toISOString(),
        };
        saveDesk(desk);
        setRatified(true);
        props.onRatified(desk);
      })
      .catch((err) => {
        setError(
          err instanceof OnboardingApiError
            ? err.message
            : "Could not ratify the desk. Retry in a moment.",
        );
      })
      .finally(() => setBusy(false));
  };

  return (
    <div className="mt-3 border border-line bg-surface">
      <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-line px-4 py-3">
        <div>
          <span className="text-data text-sm text-phosphor">{proposal.trackerSymbol}</span>
          <span className="ml-2 text-xs text-muted">{proposal.trackerName} · benchmark</span>
        </div>
        <span className="text-data text-xs text-faint">
          {proposal.currency} · {proposal.tradingWindow}
        </span>
      </div>

      <table className="w-full">
        <thead className="sr-only">
          <tr>
            <th>Selected</th>
            <th>Symbol</th>
            <th>Name</th>
            <th>30-day trend</th>
            <th>Price and 30-day change</th>
            <th>Volatility</th>
          </tr>
        </thead>
        <tbody>
          {proposal.universe.map((asset, i) => {
            const on = selected.has(asset.symbol);
            const up = asset.change30dPct >= 0;
            return (
              <tr
                key={asset.symbol}
                className="animate-fade-up cursor-pointer border-b border-line transition-colors hover:bg-raised"
                style={{ animationDelay: `${i * 40}ms` }}
                onClick={() => toggle(asset.symbol)}
              >
                <td className="w-10 py-2.5 pl-4">
                  <input
                    type="checkbox"
                    checked={on}
                    disabled={ratified}
                    onChange={() => toggle(asset.symbol)}
                    onClick={(e) => e.stopPropagation()}
                    aria-label={`Staff ${asset.symbol} to the committee`}
                    className="accent-[#3df08c]"
                  />
                </td>
                <td className={`text-data px-2 py-2.5 text-sm ${on ? "text-fg" : "text-faint line-through"}`}>
                  {asset.symbol}
                  <span className={`mt-0.5 block text-xs ${on ? "text-faint" : "text-faint/60"}`}>
                    {asset.name}
                  </span>
                </td>
                <td className="hidden px-2 py-2.5 sm:table-cell">
                  <span className={on ? "" : "opacity-30"}>
                    <Sparkline history={asset.history} up={up} />
                  </span>
                </td>
                <td className={`text-data px-2 py-2.5 text-right text-sm ${on ? "text-fg" : "text-faint"}`}>
                  ${fmtPrice(asset.lastPrice)}
                  <span
                    className={`mt-0.5 block text-xs ${
                      !on ? "text-faint/60" : up ? "text-phosphor" : "text-loss"
                    }`}
                  >
                    {up ? "▲" : "▼"} {Math.abs(asset.change30dPct).toFixed(1)}% / 30d
                  </span>
                </td>
                <td className="px-4 py-2.5 text-right">
                  <VolBadge band={asset.volBand} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <div className="text-data flex flex-wrap justify-between gap-2 border-b border-line px-4 py-2 text-xs text-faint">
        <span>
          Max position {proposal.rules.maxPositionPct}% · drawdown ≤{" "}
          {proposal.rules.maxDailyDrawdownPct}% · {proposal.rules.stopRule.toLowerCase()}
        </span>
        <span className={selected.size === 0 ? "text-amber" : "text-muted"}>
          {selected.size === 0
            ? "pick the stocks to staff"
            : `${selected.size}/${proposal.universe.length} staffed to the committee`}
        </span>
      </div>

      {error && (
        <p role="alert" className="text-data border-b border-line px-4 py-2 text-xs text-loss">
          ✕ {error}
        </p>
      )}

      <div className="px-4 py-3">
        {ratified ? (
          <p className="text-data text-sm text-phosphor">
            ✓ Ratified — {selected.size} mandate{selected.size === 1 ? "" : "s"} forwarded to the
            committee.
          </p>
        ) : (
          <div className="flex items-center gap-3">
            <button
              type="button"
              disabled={busy || selected.size === 0}
              onClick={ratify}
              className="text-data border border-phosphor bg-phosphor px-5 py-2.5 text-sm font-semibold text-ink transition-opacity hover:opacity-85 disabled:cursor-not-allowed disabled:border-line disabled:bg-transparent disabled:text-faint"
            >
              {busy
                ? "Ratifying…"
                : `Ratify ${selected.size} name${selected.size === 1 ? "" : "s"} → staff the committee`}
            </button>
            {selected.size === 0 && (
              <span className="text-data text-xs text-amber">
                Click a row to pick your stock — at least one.
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
