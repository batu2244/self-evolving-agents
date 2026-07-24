import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { loadDesk, type StoredDesk } from "@/modules/onboarding";

export default function Landing() {
  const [desk, setDesk] = useState<StoredDesk | null>(null);
  useEffect(() => setDesk(loadDesk()), []);

  return (
    <div className="animate-fade-up mx-auto max-w-3xl py-16 text-center">
      <p className="text-data text-xs uppercase tracking-widest text-phosphor">
        <span className="animate-blink">●</span> a desk that grades itself
      </p>
      <h1 className="text-display mt-4 text-5xl leading-tight sm:text-6xl">
        Every red day makes
        <br />
        the desk <em className="text-phosphor">different</em>.
      </h1>
      <p className="mx-auto mt-6 max-w-xl text-sm leading-relaxed text-muted">
        DeltaDesk is a committee of trading agents graded against doing nothing. Set the risk
        envelope once; the desk trades daily, computes its decision delta, and rewrites its own
        playbooks when it loses.
      </p>

      <div className="mt-10">
        <Link
          to="/onboarding"
          className="text-data inline-block border border-phosphor bg-phosphor px-8 py-4 text-sm font-semibold text-ink transition-opacity hover:opacity-85"
        >
          {desk ? "Reconfigure the desk →" : "Configure your desk →"}
        </Link>
        {desk && (
          <p className="text-data mt-4 text-xs text-muted">
            Desk configured · {desk.envelope.riskLevel} · tracking {desk.proposal.trackerSymbol} ·{" "}
            {desk.selected.length} committee mandates
          </p>
        )}
      </div>

      <p className="text-data mt-16 text-xs text-faint">
        one conversation · pick your stocks · the committee argues daily · paper only
      </p>
    </div>
  );
}
