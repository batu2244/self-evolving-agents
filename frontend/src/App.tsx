import { Link, Route, Routes } from "react-router-dom";
import Landing from "./pages/Landing";
import OnboardingPage from "./pages/OnboardingPage";

export default function App() {
  return (
    <div className="relative z-10 mx-auto flex min-h-dvh max-w-6xl flex-col px-5 sm:px-8">
      <header className="flex items-baseline justify-between border-b border-line py-5">
        <Link to="/" className="flex items-baseline gap-2">
          <span className="text-data text-lg font-semibold text-phosphor">Δ</span>
          <span className="text-display text-xl">DeltaDesk</span>
        </Link>
        <span className="text-data text-xs text-faint">
          paper trading · not investment advice
        </span>
      </header>

      <main className="flex-1 py-10">
        <Routes>
          <Route path="/" element={<Landing />} />
          <Route path="/onboarding" element={<OnboardingPage />} />
          <Route
            path="*"
            element={
              <div className="py-24 text-center">
                <p className="text-data text-sm text-loss">404 — NO SUCH TICKER</p>
                <Link to="/" className="mt-4 inline-block text-sm text-muted underline hover:text-fg">
                  Back to the desk
                </Link>
              </div>
            }
          />
        </Routes>
      </main>

      <footer className="border-t border-line py-4">
        <p className="text-data text-xs text-faint">
          DeltaDesk · SwarmHack SF 2026 · every red day makes the desk different
        </p>
      </footer>
    </div>
  );
}
