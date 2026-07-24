/**
 * Onboarding module — public API.
 *
 * The rest of the app should import ONLY from this file:
 *
 *   import { OnboardingChat, loadDesk } from "@/modules/onboarding";
 *
 * `loadDesk()` is how the trading dashboard reads the ratified envelope,
 * universe, and the stocks the user staffed to the committee (`selected`) —
 * without reaching into module internals. The backend twin lives in
 * `backend/app/onboarding/` (committee hook: `get_committee_mandates()`).
 */

export { OnboardingChat } from "./components/Chat";
export { loadDesk, clearDesk, type StoredDesk } from "./storage";
export type {
  Market,
  RiskEnvelope,
  RiskLevel,
  RiskRules,
  UniverseAsset,
  UniverseProposal,
} from "./types";
