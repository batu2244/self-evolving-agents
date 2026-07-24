import type {
  ChatResponse,
  ChatTurnMessage,
  EnvelopeSlots,
  RiskEnvelope,
  UniverseProposal,
} from "./types";

export class OnboardingApiError extends Error {
  constructor(
    message: string,
    public readonly status?: number,
  ) {
    super(message);
    this.name = "OnboardingApiError";
  }
}

async function post<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new OnboardingApiError("Could not reach the desk. Check your connection and retry.");
  }
  if (!res.ok) {
    throw new OnboardingApiError(
      res.status === 422
        ? "The desk rejected that — adjust and retry."
        : `The desk returned an error (${res.status}). Retry in a moment.`,
      res.status,
    );
  }
  return (await res.json()) as T;
}

export function sendChat(
  messages: ChatTurnMessage[],
  slots: EnvelopeSlots,
  signal?: AbortSignal,
): Promise<ChatResponse> {
  return post<ChatResponse>("/api/onboarding/chat", { messages, slots }, signal);
}

export function ratifyEnvelope(
  envelope: RiskEnvelope,
  proposal: UniverseProposal,
  selected: string[],
  signal?: AbortSignal,
): Promise<{ status: string; committee_mandates: string[] }> {
  return post("/api/onboarding/envelope", { envelope, proposal, selected }, signal);
}
