import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { getChatHistory, postChatMessage, type ChatMessage } from "@/lib/api";

export default function Chat() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    getChatHistory(100)
      .then((h) => setMessages(h))
      .catch(() => setError("Couldn't load chat history"))
      .finally(() => setLoaded(true));
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, sending]);

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
        {
          id: -Date.now() - 1,
          role: "assistant",
          content: res.reply,
          created_at: res.created_at,
        },
      ]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Harry couldn't reply -- try again");
    } finally {
      setSending(false);
    }
  }

  return (
    <main className="w-full h-[calc(100vh-3.5rem)] flex flex-col justify-between bg-zinc-50 px-6 md:px-12 py-6">
      {/* Centered Page Header */}
      <div className="max-w-3xl mx-auto w-full border-b border-zinc-200 pb-4 mb-4 shrink-0">
        <h1 className="text-xl font-black tracking-tight text-zinc-900">Ask Harry</h1>
        <p className="text-xs text-zinc-500 mt-0.5">
          Consult your personal workspace assistant on projects, actions, and team state.
        </p>
      </div>

      {/* Centered Scrollable Conversation Area */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto px-2 md:px-6 py-4 space-y-6 min-h-0 flex flex-col scrollbar-thin"
      >
        {!loaded && <p className="text-xs text-zinc-500 text-center mt-12">Loading conversation history…</p>}
        {loaded && messages.length === 0 && (
          <div className="text-center mt-16 max-w-md mx-auto text-zinc-500">
            <svg
              className="w-10 h-10 text-zinc-300 mx-auto mb-3"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth="1.5"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M8.684 13.342C8.886 12.938 9 12.482 9 12c0-.482-.114-.938-.316-1.342m0 2.684a3 3 0 110-2.684m0 2.684l6.632 3.316m-6.632-6l6.632-3.316m0 0a3 3 0 105.367-2.684 3 3 0 00-5.367 2.684zm0 9.316a3 3 0 105.368 2.684 3 3 0 00-5.368-2.684z"
              />
            </svg>
            <p className="text-sm font-semibold text-zinc-700">No messages yet</p>
            <p className="text-xs text-zinc-400 mt-1 leading-relaxed">
              Ask Harry about any project, task, blockers, or meeting status -- e.g., "what projects are currently tracked?" or "any blockers today?"
            </p>
          </div>
        )}

        {messages.map((m) => (
          <div
            key={m.id}
            className={`flex ${m.role === "user" ? "justify-end" : "justify-start"} max-w-3xl mx-auto w-full`}
          >
            <div
              className={`max-w-[85%] rounded-2xl px-5 py-3.5 text-sm leading-relaxed ${
                m.role === "user"
                  ? "bg-zinc-950 text-white rounded-br-sm border border-zinc-950 shadow-sm"
                  : "bg-white text-zinc-900 rounded-bl-sm border border-zinc-200 shadow-sm"
              }`}
            >
              {m.role === "user" ? (
                <span className="whitespace-pre-wrap">{m.content}</span>
              ) : (
                <div className="prose prose-zinc prose-sm max-w-none text-zinc-900">
                  <ReactMarkdown
                    components={{
                      p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
                      ul: ({ children }) => <ul className="list-disc pl-5 mb-2 space-y-1">{children}</ul>,
                      ol: ({ children }) => <ol className="list-decimal pl-5 mb-2 space-y-1">{children}</ol>,
                      li: ({ children }) => <li className="mb-0.5">{children}</li>,
                      strong: ({ children }) => <strong className="font-bold text-zinc-950">{children}</strong>,
                      code: ({ children }) => <code className="bg-zinc-100 rounded px-1.5 py-0.5 font-mono text-[12px]">{children}</code>,
                    }}
                  >
                    {m.content}
                  </ReactMarkdown>
                </div>
              )}
            </div>
          </div>
        ))}

        {sending && (
          <div className="flex justify-start max-w-3xl mx-auto w-full">
            <div className="rounded-2xl rounded-bl-sm bg-white border border-zinc-200 shadow-sm text-zinc-500 px-5 py-3.5 text-sm flex items-center gap-2">
              <span className="h-1.5 w-1.5 bg-zinc-400 rounded-full animate-pulse" />
              <span className="h-1.5 w-1.5 bg-zinc-400 rounded-full animate-pulse delay-75" />
              <span className="h-1.5 w-1.5 bg-zinc-400 rounded-full animate-pulse delay-150" />
              <span className="ml-1 text-xs">Harry is typing…</span>
            </div>
          </div>
        )}
      </div>

      {/* Centered Error Box */}
      {error && (
        <div className="max-w-3xl mx-auto w-full shrink-0">
          <p className="px-4 py-2 mb-2 text-xs text-rose-600 bg-rose-50 border border-rose-100 rounded-lg">
            {error}
          </p>
        </div>
      )}

      {/* Centered Input Box */}
      <div className="max-w-3xl mx-auto w-full shrink-0 mb-4 mt-2">
        <div className="flex items-center gap-2.5 p-1 border border-zinc-200 bg-white rounded-xl shadow-sm">
          <textarea
            autoFocus
            rows={1}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                handleSend();
              }
            }}
            placeholder="Ask Harry about projects, team tasks, blockers..."
            className="flex-1 resize-none rounded-lg border-0 bg-transparent px-4 py-3 text-sm text-zinc-900 focus:outline-none focus:ring-0 max-h-32"
          />
          <button
            onClick={handleSend}
            disabled={sending || !draft.trim()}
            aria-label="Send message"
            className="rounded-lg bg-zinc-950 text-white w-9 h-9 flex items-center justify-center hover:bg-zinc-900 disabled:opacity-50 transition-all shrink-0 mr-1"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M3 11.5 20.5 4l-6.5 17.5-3-7-8-3Z" stroke="white" strokeWidth="1.8" strokeLinejoin="round" fill="none" />
            </svg>
          </button>
        </div>
      </div>
    </main>
  );
}
