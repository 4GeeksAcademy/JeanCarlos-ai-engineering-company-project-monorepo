"use client";

import { useCallback, useEffect, useReducer, useRef, useState, type FormEvent } from "react";
import { applyChatEvent, ChatSocket, initialChatState, type ChatEvent, type ChatMessage, type ChatState, type SocketStatus } from "@/lib/chat-socket";

// Compliance assistant (Claire Whitfield's area) for HealthCore staff — Sistemas en Tiempo Real, Parte 2.
// Before: one request (POST), one complete answer. Now: one WebSocket per chat session
// (/agent/chat/ws?session_id=…, backoffice JWT in the first frame): the answer appears token by
// token; while it is answering, sending a new message interrupts it (`interrupt_requested` with
// new_input), the partial answer stays visible marked "Interrumpida" and the redirected answer is a
// new message. On a disconnection it reconnects with backoff and rehydrates the conversation from
// the server's snapshot before accepting new tokens. Internal staff only — never patients.

const EXAMPLES = [
  "¿Qué permite HIPAA sobre compartir información con un proveedor externo?",
  "¿Qué debe incluir un Data Processing Agreement bajo UK GDPR?",
  "¿En cuánto tiempo hay que notificar una brecha de datos en Reino Unido?",
];
const MAX_LENGTH = 1000;
const SESSION_KEY = "healthcore.compliance-chat.session";

type Action = { type: "event"; event: ChatEvent } | { type: "reset"; sessionId: string };

function reducer(state: ChatState, action: Action): ChatState {
  return action.type === "reset" ? initialChatState(action.sessionId) : applyChatEvent(state, action.event);
}

async function credentials(): Promise<{ token: string; ws_url: string }> {
  const response = await fetch("/api/agent/chat/socket", { cache: "no-store", headers: { "X-HealthCore-Chat": "1" } });
  if (response.status === 401) throw new Error("expired");
  if (!response.ok) throw new Error("unavailable");
  return (await response.json()) as { token: string; ws_url: string };
}

async function createSession(): Promise<string> {
  const response = await fetch("/api/agent/chat/sessions", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  if (!response.ok) throw new Error(response.status === 401 ? "Tu sesión expiró. Vuelve a iniciar sesión." : "No se pudo abrir el chat.");
  return ((await response.json()) as { session_id: string }).session_id;
}

export default function ComplianceChatPage() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [state, dispatch] = useReducer(reducer, initialChatState(""));
  const [link, setLink] = useState<SocketStatus>({ state: "connecting", attempt: 0 });
  const [text, setText] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const socket = useRef<ChatSocket | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  // the session: ?session=chat_… (e.g. a supervisor observing), the last one of this tab, or a new one
  useEffect(() => {
    const fromUrl = new URLSearchParams(window.location.search).get("session");
    let stored: string | null = null;
    try {
      stored = sessionStorage.getItem(SESSION_KEY);
    } catch {
      /* storage unavailable */
    }
    const known = fromUrl ?? stored;
    const pending = known && /^chat_[0-9a-f]{12}$/.test(known) ? Promise.resolve(known) : createSession();
    pending.then(setSessionId).catch((error: Error) => setProblem(error.message));
  }, []);

  useEffect(() => {
    if (!sessionId) return;
    try {
      if (!new URLSearchParams(window.location.search).get("session")) sessionStorage.setItem(SESSION_KEY, sessionId);
    } catch {
      /* storage unavailable */
    }
    dispatch({ type: "reset", sessionId });
    const chat = new ChatSocket({ sessionId, credentials, onStatus: setLink, onEvent: (event) => dispatch({ type: "event", event }) });
    socket.current = chat;
    chat.start();
    const online = () => chat.reconnectNow();
    window.addEventListener("online", online);
    return () => {
      window.removeEventListener("online", online);
      chat.close();
      socket.current = null;
    };
  }, [sessionId]);

  useEffect(() => {
    if (state.needsResync) socket.current?.resync(); // a gap in the token sequence: get a fresh snapshot
  }, [state.needsResync]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "nearest" });
  }, [state.messages]);

  const send = useCallback((value: string) => {
    const message = value.trim();
    if (message.length < 2 || !socket.current) return;
    const ok = state.generating
      ? socket.current.send("interrupt_requested", { new_input: message }) // redirect the answer in progress
      : socket.current.send("user_message", { text: message });
    if (ok) setText("");
    else setProblem("Sin conexión: el mensaje no se envió. Se está reconectando.");
  }, [state.generating]);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setProblem(null);
    send(text);
  }

  async function newConversation() {
    setProblem(null);
    try {
      setSessionId(await createSession());
    } catch (error) {
      setProblem(error instanceof Error ? error.message : "No se pudo abrir el chat.");
    }
  }

  const observer = state.role === "observer";
  const canSend = link.state === "open" && state.ready && !observer && state.status !== "closed";

  return (
    <main className="knowledge-page chat-page">
      <h1>Compliance assistant</h1>
      <p>
        Pregunta qué está permitido bajo HIPAA (EE. UU.) y UK GDPR (Reino Unido). La respuesta llega mientras se escribe; si
        va por el camino equivocado, escribe y envía otra vez para interrumpirla y redirigirla. Uso interno del personal: no
        escribas datos de pacientes (nombres, diagnósticos ni números de historia clínica).
      </p>

      <section className="panel chat-panel" aria-label="Conversación">
        <header className="chat-header">
          <LinkBadge status={link} />
          {sessionId && <small className="knowledge-muted">Sesión {sessionId}{observer ? " · observando (solo lectura)" : ""}</small>}
          <button type="button" className="chat-secondary" onClick={() => void newConversation()}>Nueva conversación</button>
        </header>

        <div className="chat-log" aria-live="polite" aria-busy={Boolean(state.generating)}>
          {state.ready && state.messages.length === 0 && <p className="knowledge-muted">Empieza con una pregunta o un ejemplo.</p>}
          {!state.ready && <p className="knowledge-muted" role="status">Restaurando la conversación…</p>}
          {state.messages.map((m) => <Bubble key={m.message_id} message={m} />)}
          <div ref={bottom} />
        </div>

        {problem && <p className="error" role="alert">{problem}</p>}
        {state.error && <p className="error" role="alert">{state.error}</p>}

        <form className="knowledge-form" onSubmit={submit}>
          <label htmlFor="chat-input">{state.generating ? "Interrumpe y redirige la respuesta" : "Tu pregunta"}</label>
          <textarea
            id="chat-input"
            rows={2}
            maxLength={MAX_LENGTH}
            value={text}
            disabled={!canSend}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                event.currentTarget.form?.requestSubmit();
              }
            }}
            placeholder={state.generating ? "Ej.: espera, mi pregunta era sobre UK GDPR, no HIPAA" : "Ej.: ¿Qué permite HIPAA sobre compartir PHI con un proveedor?"}
          />
          <div className="knowledge-actions">
            <button disabled={!canSend || text.trim().length < 2} className={state.generating ? "chat-interrupt" : undefined}>
              {state.generating ? "Interrumpir y enviar" : "Enviar"}
            </button>
            <small>Enter para enviar · Shift + Enter para nueva línea</small>
          </div>
        </form>
        {!state.generating && canSend && (
          <div className="knowledge-examples" aria-label="Preguntas de ejemplo">
            {EXAMPLES.map((example) => (
              <button key={example} type="button" className="knowledge-chip" onClick={() => send(example)}>{example}</button>
            ))}
          </div>
        )}
      </section>
    </main>
  );
}

function LinkBadge({ status }: { status: SocketStatus }) {
  if (status.state === "open") return <span className="rfp-live-badge rfp-live-on" role="status">● En vivo</span>;
  if (status.state === "expired") return <span className="rfp-live-badge rfp-live-off" role="alert">Sesión expirada: vuelve a iniciar sesión</span>;
  if (status.state === "forbidden") return <span className="rfp-live-badge rfp-live-off" role="alert">No tienes acceso a esta conversación</span>;
  if (status.state === "reconnecting") {
    const wait = status.retryInMs !== undefined ? ` en ${Math.ceil(status.retryInMs / 1000)} s` : "…";
    return <span className="rfp-live-badge rfp-live-retry" role="status">Sin conexión · reintento {Math.max(1, status.attempt)}{wait}</span>;
  }
  return <span className="rfp-live-badge" role="status">Conectando…</span>;
}

function Bubble({ message }: { message: ChatMessage }) {
  const label = message.status === "interrupted" ? "Interrumpida" : message.status === "failed" ? "Sin respuesta" : null;
  return (
    <article className={`chat-bubble chat-${message.role} chat-${message.status}`} data-message-id={message.message_id}>
      <div className="chat-text">
        {message.text}
        {message.status === "streaming" && <span className="chat-caret" aria-hidden="true">▍</span>}
      </div>
      {label && <span className="chat-tag">{label}</span>}
    </article>
  );
}
