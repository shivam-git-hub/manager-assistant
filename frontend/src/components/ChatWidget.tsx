import { useEffect, useRef, useState } from "react";
import { getChatHistory, postChatMessage, type ChatMessage } from "@/lib/api";

const ORB_SIZE = 56; // px, matches w-14/h-14
const EDGE_MARGIN = 20; // px, matches the old bottom-5/right-5 (1.25rem)
const DRAG_THRESHOLD = 6; // px of movement before a pointerdown counts as a drag, not a click

function defaultPosition() {
  return {
    x: window.innerWidth - ORB_SIZE - EDGE_MARGIN,
    y: window.innerHeight - ORB_SIZE - EDGE_MARGIN,
  };
}

function clamp(pos: { x: number; y: number }) {
  return {
    x: Math.min(Math.max(pos.x, EDGE_MARGIN), window.innerWidth - ORB_SIZE - EDGE_MARGIN),
    y: Math.min(Math.max(pos.y, EDGE_MARGIN), window.innerHeight - ORB_SIZE - EDGE_MARGIN),
  };
}

// Floating "ask Harry anything" widget, reachable from every page (lifted
// into App.tsx, same pattern as QuickAddTodoModal) -- replaces the
// disabled TopNav "Chat!" pill. Position is plain component state (not
// persisted -- reopening the app resets it to the bottom-right default,
// which is intentional: a dragged-out-of-the-way orb shouldn't stay
// stuck somewhere awkward forever). Note the position is inline-styled
// (not Tailwind's fixed/bottom/right utilities) deliberately -- see the
// bug this replaced: the orb's own gradient <style> block set
// `position: relative` on the same class for the ::after highlight
// trick, which silently clobbered Tailwind's `position: fixed` (same
// specificity, later in the DOM wins) and pinned the button to normal
// document flow at the top of the page. Inline styles always win the
// cascade, so drag position can never be fought by that again.
export default function ChatWidget() {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const dragState = useRef<{ startX: number; startY: number; origX: number; origY: number; moved: boolean } | null>(null);

  useEffect(() => {
    setPos(defaultPosition());
    const onResize = () => setPos((p) => (p ? clamp(p) : p));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    if (open && !loaded) {
      getChatHistory(50)
        .then((h) => setMessages(h))
        .catch(() => setError("Couldn't load chat history"))
        .finally(() => setLoaded(true));
    }
  }, [open, loaded]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, sending, open]);

  function handlePointerDown(e: React.PointerEvent<HTMLButtonElement>) {
    if (!pos) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    dragState.current = { startX: e.clientX, startY: e.clientY, origX: pos.x, origY: pos.y, moved: false };
    setDragging(true);
  }

  function handlePointerMove(e: React.PointerEvent<HTMLButtonElement>) {
    if (!dragState.current) return;
    const dx = e.clientX - dragState.current.startX;
    const dy = e.clientY - dragState.current.startY;
    if (!dragState.current.moved && Math.hypot(dx, dy) > DRAG_THRESHOLD) {
      dragState.current.moved = true;
    }
    if (dragState.current.moved) {
      setPos(clamp({ x: dragState.current.origX + dx, y: dragState.current.origY + dy }));
    }
  }

  function handlePointerUp(e: React.PointerEvent<HTMLButtonElement>) {
    const wasDrag = dragState.current?.moved ?? false;
    dragState.current = null;
    setDragging(false);
    if (!wasDrag) {
      setOpen((o) => !o);
    }
    e.currentTarget.releasePointerCapture(e.pointerId);
  }

  // Keyboard activation (Tab + Enter/Space) never goes through the
  // pointer handlers above, so it needs its own path -- deliberately NOT
  // an onClick, since mouse interactions already toggle via
  // handlePointerUp and the browser's synthesized click after pointerup
  // would immediately toggle it back off.
  function handleKeyDown(e: React.KeyboardEvent<HTMLButtonElement>) {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      setOpen((o) => !o);
    }
  }

  async function handleSend() {
    const text = draft.trim();
    if (!text || sending) return;
    setDraft("");
    setError(null);
    const optimistic: ChatMessage = {
      id: -Date.now(),
      role: "user",
      content: text,
      created_at: new Date().toISOString(),
    };
    setMessages((m) => [...m, optimistic]);
    setSending(true);
    try {
      const res = await postChatMessage(text);
      setMessages((m) => [
        ...m,
        { id: -Date.now() - 1, role: "assistant", content: res.reply, tool_trace: res.tool_trace, created_at: res.created_at },
      ]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Harry couldn't reply -- try again");
    } finally {
      setSending(false);
    }
  }

  if (!pos) return null;

  // Panel opens anchored to whichever screen half the orb is currently
  // on, so it never gets pushed off-screen by a dragged orb position.
  const openLeft = pos.x < window.innerWidth / 2;
  const openTop = pos.y < window.innerHeight / 2;

  return (
    <>
      {open && (
        <div
          className="fixed z-50 w-[360px] max-w-[calc(100vw-2.5rem)] h-[520px] max-h-[calc(100vh-9rem)] rounded-2xl bg-white shadow-2xl border border-cardline flex flex-col overflow-hidden"
          style={{
            left: openLeft ? Math.min(pos.x, window.innerWidth - 360 - EDGE_MARGIN) : undefined,
            right: openLeft ? undefined : window.innerWidth - pos.x - ORB_SIZE,
            top: openTop ? pos.y + ORB_SIZE + 12 : undefined,
            bottom: openTop ? undefined : window.innerHeight - pos.y + 12,
          }}
          role="dialog"
          aria-modal="true"
          aria-label="Chat with Harry"
        >
          <div className="flex items-center justify-between px-4 py-3 bg-nav text-white shrink-0">
            <div className="flex items-center gap-2">
              <span className="orb-mini" aria-hidden="true" />
              <span className="font-bold text-sm">Harry</span>
            </div>
            <button
              onClick={() => setOpen(false)}
              aria-label="Close chat"
              className="rounded p-1 hover:bg-navdeep leading-none text-lg"
            >
              &times;
            </button>
          </div>

          <div ref={scrollRef} className="flex-1 overflow-y-auto px-3 py-3 space-y-2 bg-surface">
            {!loaded && <p className="text-xs text-inksoft text-center mt-6">Loading…</p>}
            {loaded && messages.length === 0 && (
              <p className="text-xs text-inksoft text-center mt-6">
                Ask Harry about your projects, tasks, or team -- e.g. "what meetings do I have today?"
              </p>
            )}
            {messages.map((m) => (
              <div key={m.id} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                <div
                  className={`max-w-[80%] rounded-xl px-3 py-2 text-sm whitespace-pre-wrap ${
                    m.role === "user" ? "bg-nav text-white rounded-br-sm" : "bg-card text-ink rounded-bl-sm"
                  }`}
                >
                  {m.content}
                </div>
              </div>
            ))}
            {sending && (
              <div className="flex justify-start">
                <div className="rounded-xl rounded-bl-sm bg-card text-inksoft px-3 py-2 text-sm">Harry is thinking…</div>
              </div>
            )}
          </div>

          {error && <p className="px-3 py-1 text-xs text-[#DD5454] bg-white shrink-0">{error}</p>}

          <div className="flex items-center gap-2 p-3 border-t border-cardline bg-white shrink-0">
            <input
              autoFocus
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSend()}
              placeholder="Message Harry…"
              className="flex-1 rounded-full border border-cardline px-4 py-2 text-sm text-ink focus:outline-none focus:border-nav"
            />
            <button
              onClick={handleSend}
              disabled={sending || !draft.trim()}
              aria-label="Send message"
              className="rounded-full bg-nav text-white w-9 h-9 flex items-center justify-center hover:bg-navdeep disabled:opacity-50 shrink-0"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M3 11.5 20.5 4l-6.5 17.5-3-7-8-3Z" stroke="white" strokeWidth="1.8" strokeLinejoin="round" fill="none" />
              </svg>
            </button>
          </div>
        </div>
      )}

      <button
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onKeyDown={handleKeyDown}
        aria-label={open ? "Close chat with Harry" : "Chat with Harry (draggable)"}
        aria-expanded={open}
        className={`fixed z-50 w-14 h-14 rounded-full shadow-lg touch-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-nav ${
          dragging ? "cursor-grabbing" : "cursor-grab"
        }`}
        style={{ position: "fixed", left: pos.x, top: pos.y }}
      >
        {/* Highlight is a real child, not a ::after on the button itself
            -- keeps the button's own `position` free for the inline
            fixed/left/top above, never fought by a class rule again. */}
        <span className="orb-fill" aria-hidden="true">
          <span className="orb-highlight" />
        </span>
        <span className="sr-only">Chat with Harry</span>
      </button>

      <style>{`
        .orb-fill {
          position: absolute;
          inset: 0;
          border-radius: 9999px;
          background: conic-gradient(from 0deg, #09090b, #3f3f46, #71717a, #d4d4d8, #71717a, #3f3f46, #09090b);
          animation: orb-spin 8s linear infinite;
        }
        .orb-highlight {
          position: absolute;
          inset: 3px;
          border-radius: 9999px;
          background: radial-gradient(circle at 35% 30%, rgba(255,255,255,0.15), rgba(255,255,255,0) 60%), #18181b;
        }
        .orb-mini {
          display: inline-block;
          width: 14px;
          height: 14px;
          border-radius: 9999px;
          background: conic-gradient(from 0deg, #09090b, #3f3f46, #71717a, #d4d4d8, #71717a, #3f3f46, #09090b);
          animation: orb-spin 8s linear infinite;
        }
        @keyframes orb-spin {
          to { transform: rotate(360deg); }
        }
        @media (prefers-reduced-motion: reduce) {
          .orb-fill, .orb-mini { animation: none; }
        }
      `}</style>
    </>
  );
}
