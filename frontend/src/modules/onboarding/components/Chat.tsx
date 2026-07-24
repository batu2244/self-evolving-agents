import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { OnboardingApiError, sendChat } from "../api";
import { loadDesk, type StoredDesk } from "../storage";
import {
  EMPTY_SLOTS,
  MARKET_LABEL,
  type ChatTurnMessage,
  type EnvelopeSlots,
  type RadarItem,
  type RiskEnvelope,
  type UniverseProposal,
} from "../types";
import { ProposalCard } from "./ProposalCard";

const GREETING =
  "Welcome to the floor. I staff a desk of trading agents around your risk envelope — " +
  "four questions max, then you pick the stock the committee trades. " +
  "Most-watched right now: NVDA, TSLA, XTB, BTC, KO. " +
  "If you had to buy one today, which would it be?";

const GREETING_CHIPS = [
  "NVDA and TSLA",
  "Something safe like KO",
  "XTB on the Warsaw exchange",
  "Just pick for me",
];

const GREETING_RADAR: RadarItem[] = [
  { symbol: "NVDA", name: "NVIDIA" },
  { symbol: "TSLA", name: "Tesla" },
  { symbol: "XTB", name: "X-Trade Brokers" },
  { symbol: "BTC", name: "Bitcoin" },
  { symbol: "KO", name: "Coca-Cola" },
];

interface Msg {
  id: number;
  role: "user" | "assistant";
  content: string;
  proposal?: UniverseProposal | null;
  envelope?: RiskEnvelope | null;
  candidates?: RadarItem[];
  error?: boolean;
}

let nextId = 1;

export function OnboardingChat() {
  const [msgs, setMsgs] = useState<Msg[]>([
    { id: 0, role: "assistant", content: GREETING, candidates: GREETING_RADAR },
  ]);
  const [slots, setSlots] = useState<EnvelopeSlots>(EMPTY_SLOTS);
  const [chips, setChips] = useState<string[]>(GREETING_CHIPS);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState(false);
  const [existing, setExisting] = useState<StoredDesk | null>(null);
  const [ratifiedDesk, setRatifiedDesk] = useState<StoredDesk | null>(null);
  const failedHistory = useRef<ChatTurnMessage[] | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => setExisting(loadDesk()), []);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [msgs, pending]);

  const post = (history: ChatTurnMessage[]) => {
    setPending(true);
    setChips([]);
    sendChat(history, slots)
      .then((res) => {
        failedHistory.current = null;
        const envelope: RiskEnvelope | null =
          res.done &&
          res.slots.riskLevel &&
          res.slots.capitalUsd !== null &&
          res.slots.targetReturnPct !== null &&
          res.slots.market
            ? {
                riskLevel: res.slots.riskLevel,
                targetReturnPct: res.slots.targetReturnPct,
                capitalUsd: res.slots.capitalUsd,
                market: res.slots.market,
              }
            : null;
        setSlots(res.slots);
        setChips(res.suggestions);
        setMsgs((m) => [
          ...m,
          {
            id: nextId++,
            role: "assistant",
            content: res.reply,
            proposal: res.proposal,
            envelope,
            candidates: res.candidates,
          },
        ]);
      })
      .catch((err) => {
        failedHistory.current = history;
        setMsgs((m) => [
          ...m,
          {
            id: nextId++,
            role: "assistant",
            error: true,
            content:
              err instanceof OnboardingApiError
                ? err.message
                : "Unexpected error. Retry in a moment.",
          },
        ]);
      })
      .finally(() => {
        setPending(false);
        inputRef.current?.focus();
      });
  };

  const send = (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || pending) return;
    setInput("");
    // side effects must stay out of the setMsgs updater — StrictMode
    // double-invokes updaters, which duplicated the API call
    const withUser = [...msgs, { id: nextId++, role: "user" as const, content: trimmed }];
    setMsgs(withUser);
    post(withUser.filter((x) => !x.error).map((x) => ({ role: x.role, content: x.content })));
  };

  const retry = () => {
    if (!failedHistory.current || pending) return;
    setMsgs((m) => m.filter((x) => !x.error));
    post(failedHistory.current);
  };

  const onRatified = (desk: StoredDesk) => {
    setRatifiedDesk(desk);
    setChips([]);
    setMsgs((m) => [
      ...m,
      {
        id: nextId++,
        role: "assistant",
        content:
          `The desk is live. ${desk.selected.join(", ")} — each goes to the committee: ` +
          "the analysts vote daily, the PM executes inside your envelope, and the evaluator " +
          "grades every decision against the tracker. Every red day rewrites a playbook.",
      },
    ]);
  };

  return (
    <div className="mx-auto flex h-[calc(100dvh-14rem)] min-h-[28rem] max-w-3xl flex-col">
      {existing && !ratifiedDesk && (
        <p className="text-data mb-4 border border-amber/40 bg-surface px-4 py-2.5 text-xs text-amber">
          ⚠ A desk is already configured ({existing.envelope.riskLevel} ·{" "}
          {existing.proposal.trackerSymbol} · {existing.selected.length} mandates). Ratifying a new
          one replaces it.
        </p>
      )}

      <SlotTracker slots={slots} />

      <div
        className="mt-4 flex-1 space-y-4 overflow-y-auto border border-line bg-surface/50 p-4"
        role="log"
        aria-live="polite"
        aria-label="Conversation with the desk concierge"
      >
        {msgs.map((msg) =>
          msg.error ? (
            <div key={msg.id} role="alert" className="animate-fade-up border border-loss/40 p-3">
              <p className="text-data text-xs text-loss">✕ {msg.content}</p>
              <button
                type="button"
                onClick={retry}
                className="text-data mt-2 border border-line-strong px-4 py-1.5 text-xs text-fg transition-colors hover:border-phosphor hover:text-phosphor"
              >
                ↻ Retry
              </button>
            </div>
          ) : (
            <div key={msg.id} className="animate-fade-up">
              <p
                className={`text-data text-xs uppercase ${
                  msg.role === "assistant" ? "text-phosphor" : "text-right text-faint"
                }`}
              >
                {msg.role === "assistant" ? "▮ Desk" : "You"}
              </p>
              <div
                className={`mt-1 text-sm leading-relaxed ${
                  msg.role === "assistant" ? "text-fg" : "text-right text-muted"
                }`}
              >
                {msg.content}
              </div>
              {msg.candidates && msg.candidates.length > 0 && !msg.proposal && (
                <div className="mt-2" aria-label="Stocks on the radar">
                  <span className="text-data mr-2 text-xs uppercase text-faint">On the radar</span>
                  {msg.candidates.map((c) => (
                    <button
                      key={c.symbol}
                      type="button"
                      title={c.name}
                      onClick={() => send(`I'd buy ${c.symbol}`)}
                      className="text-data mb-1 mr-1.5 inline-block border border-line bg-surface px-2 py-1 text-xs text-muted transition-colors hover:border-phosphor hover:text-phosphor"
                    >
                      {c.symbol}
                    </button>
                  ))}
                </div>
              )}
              {msg.proposal && msg.envelope && (
                <ProposalCard
                  envelope={msg.envelope}
                  proposal={msg.proposal}
                  onRatified={onRatified}
                />
              )}
            </div>
          ),
        )}

        {pending && (
          <p className="text-data text-xs text-phosphor" aria-label="The desk is thinking">
            <span className="animate-blink">●</span> DESK THINKING…
          </p>
        )}

        {ratifiedDesk && (
          <div className="animate-fade-up border-t border-line pt-4">
            <Link
              to="/"
              className="text-data inline-block border border-phosphor bg-phosphor px-5 py-2.5 text-sm font-semibold text-ink transition-opacity hover:opacity-85"
            >
              To the trading floor →
            </Link>
          </div>
        )}
        <div ref={endRef} />
      </div>

      {chips.length > 0 && !pending && (
        <div className="mt-3" role="group" aria-label="Multiple choice answers">
          <p className="text-data text-xs uppercase text-faint">Pick one — or type your own below</p>
          <div className="mt-1.5 flex flex-wrap gap-2">
            {chips.map((chip, i) => (
              <button
                key={chip}
                type="button"
                onClick={() => send(chip)}
                className="text-data group border border-line bg-surface px-3 py-2 text-xs text-muted transition-colors hover:border-phosphor hover:text-phosphor"
              >
                <span className="mr-1.5 text-faint group-hover:text-phosphor">{i + 1}</span>
                {chip}
              </button>
            ))}
          </div>
        </div>
      )}

      <form
        className="mt-3 flex items-stretch border border-line bg-surface focus-within:border-phosphor"
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
      >
        <span className="text-data self-center pl-4 text-phosphor" aria-hidden>
          ›
        </span>
        <input
          ref={inputRef}
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={ratifiedDesk ? "Desk is live — tell me what to change…" : "Tell the desk what you're after…"}
          aria-label="Message the desk concierge"
          disabled={pending}
          autoFocus
          className="text-data w-full bg-transparent px-3 py-3.5 text-sm outline-none placeholder:text-faint disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={pending}
          aria-disabled={pending || !input.trim()}
          className={`text-data border-l border-line px-5 text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
            input.trim() ? "text-muted hover:text-phosphor" : "cursor-default text-faint opacity-50"
          }`}
        >
          Send ↵
        </button>
      </form>
    </div>
  );
}

function SlotTracker({ slots }: { slots: EnvelopeSlots }) {
  const cells: [string, string | null][] = [
    ["Risk", slots.riskLevel],
    ["Market", slots.market ? MARKET_LABEL[slots.market] : null],
    ["Capital", slots.capitalUsd !== null ? `$${slots.capitalUsd.toLocaleString("en-US")}` : null],
    ["Target", slots.targetReturnPct !== null ? `+${slots.targetReturnPct}%/qtr` : null],
  ];
  return (
    <dl className="grid grid-cols-4 gap-px border border-line bg-line" aria-label="Risk envelope so far">
      {cells.map(([label, value]) => (
        <div key={label} className="bg-surface px-3 py-2">
          <dt className="text-data text-xs uppercase text-faint">{label}</dt>
          <dd
            className={`text-data mt-0.5 truncate text-xs capitalize ${
              value ? "text-phosphor" : "text-faint"
            }`}
          >
            {value ?? "—"}
          </dd>
        </div>
      ))}
    </dl>
  );
}
