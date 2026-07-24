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

/**
 * Streaming chat turn: `onDelta` fires per text chunk as the concierge talks;
 * resolves with the full structured turn once the stream ends.
 */
export async function streamChat(
  messages: ChatTurnMessage[],
  slots: EnvelopeSlots,
  onDelta: (text: string) => void,
  signal?: AbortSignal,
): Promise<ChatResponse> {
  let res: Response;
  try {
    res = await fetch("/api/onboarding/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages, slots }),
      signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new OnboardingApiError("Could not reach the desk. Check your connection and retry.");
  }
  if (!res.ok || !res.body) {
    throw new OnboardingApiError(
      `The desk returned an error (${res.status}). Retry in a moment.`,
      res.status,
    );
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let turn: ChatResponse | null = null;

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) >= 0) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const event = /^event: (.+)$/m.exec(frame)?.[1];
      const data = /^data: (.+)$/m.exec(frame)?.[1];
      if (!event || !data) continue;
      if (event === "delta") onDelta((JSON.parse(data) as { text: string }).text);
      else if (event === "turn") turn = JSON.parse(data) as ChatResponse;
    }
  }
  if (!turn) {
    throw new OnboardingApiError("The desk went quiet mid-sentence. Retry in a moment.");
  }
  return turn;
}

export function ratifyEnvelope(
  envelope: RiskEnvelope,
  proposal: UniverseProposal,
  selected: string[],
  signal?: AbortSignal,
): Promise<{ status: string; committee_mandates: string[] }> {
  return post("/api/onboarding/envelope", { envelope, proposal, selected }, signal);
}
