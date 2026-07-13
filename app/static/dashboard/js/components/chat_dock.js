// chat_dock.js - Harry Conversational Chat Dock Component

export default {
    name: 'ChatDock',
    data() {
        return {
            collapsed: true,
            messages: [],
            draft: '',
            sending: false
        };
    },
    mounted() {
        this.fetchChatHistory();
        // Poll for new messages every 10s if expanded
        setInterval(() => {
            if (!this.collapsed) {
                this.fetchChatHistory();
            }
        }, 10000);
    },
    methods: {
        async fetchChatHistory() {
            try {
                const res = await fetch('api/chat/history');
                if (res.ok) {
                    this.messages = await res.json();
                    this.scrollToBottom();
                }
            } catch (err) {
                console.error('Error fetching chat history:', err);
            }
        },
        async sendMessage() {
            if (!this.draft.trim() || this.sending) return;
            
            const text = this.draft;
            this.draft = '';
            this.sending = true;
            
            // Add user message optimistically
            this.messages.push({
                id: Date.now(),
                role: 'user',
                content: text,
                created_at: new Date().toISOString()
            });
            this.scrollToBottom();
            
            try {
                const res = await fetch('api/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ message: text })
                });
                
                if (res.ok) {
                    await this.fetchChatHistory();
                }
            } catch (err) {
                console.error('Error sending chat message:', err);
            } finally {
                this.sending = false;
                this.scrollToBottom();
            }
        },
        async clearHistory() {
            if (confirm('Are you sure you want to clear Harry\'s chat history?')) {
                try {
                    const res = await fetch('api/chat/history', { method: 'DELETE' });
                    if (res.ok) {
                        this.messages = [];
                    }
                } catch (err) {
                    console.error('Error clearing chat history:', err);
                }
            }
        },
        toggleCollapse() {
            this.collapsed = !this.collapsed;
            if (!this.collapsed) {
                this.fetchChatHistory();
            }
        },
        scrollToBottom() {
            this.$nextTick(() => {
                const feed = this.$refs.feed;
                if (feed) {
                    feed.scrollTop = feed.scrollHeight;
                }
            });
        },
        formatTime(dateStr) {
            if (!dateStr) return '';
            const d = new Date(dateStr);
            return d.toLocaleTimeString('en-IN', {
                hour: '2-digit',
                minute: '2-digit',
                hour12: false
            });
        }
    },
    template: `
        <div class="chat-dock font-sans" :class="{ 'collapsed': collapsed }">
            <!-- Header -->
            <div @click="toggleCollapse" class="chat-dock-header flex items-center justify-between select-none">
                <div class="flex items-center gap-2">
                    <span class="text-sm">💬</span>
                    <span class="font-bold text-xs uppercase letter-wide">Chat with Harry</span>
                </div>
                <div class="flex items-center gap-3">
                    <button v-if="!collapsed" @click.stop="clearHistory" class="text-[10px] text-white/70 hover:text-white underline font-mono">
                        Clear
                    </button>
                    <span class="text-xs font-mono">{{ collapsed ? '▲' : '▼' }}</span>
                </div>
            </div>
            
            <!-- Chat Body (shown only when expanded) -->
            <div v-if="!collapsed" class="chat-dock-body">
                <!-- Message Feed -->
                <div ref="feed" class="flex-1 overflow-y-auto p-4 space-y-3 bg-[#fdfcf9]">
                    <div v-if="messages.length === 0" class="flex flex-col items-center justify-center h-full text-slate-400 text-center p-4">
                        <p class="text-xs font-bold text-slate-600">Ask Harry anything about project status.</p>
                        <p class="text-[10px] text-slate-400 mt-1 leading-normal">"What is the status of Phoenix?"<br>"Ask Bob to confirm the database schema."</p>
                    </div>
                    
                    <div v-for="msg in messages" :key="msg.id" class="flex flex-col">
                        <div class="flex items-baseline justify-between mb-0.5">
                            <span class="font-bold text-[10px] font-mono capitalize" :class="msg.role === 'user' ? 'text-slate-500' : 'text-emerald-800'">
                                {{ msg.role === 'user' ? 'Shivam (You)' : 'Harry (Assistant)' }}
                            </span>
                            <span class="text-[9px] text-slate-400 font-mono">{{ formatTime(msg.created_at) }}</span>
                        </div>
                        <div class="text-xs p-2.5 rounded-lg border leading-relaxed whitespace-pre-wrap font-serif" :class="{
                            'bg-white border-parchment text-slate-800': msg.role === 'user',
                            'bg-emerald-50/50 border-emerald-100 text-slate-900': msg.role === 'assistant'
                        }">
                            {{ msg.content }}
                            
                            <!-- Tool Traces Accordion -->
                            <details v-if="msg.tool_trace && msg.tool_trace.length > 0" class="mt-2 pt-1 border-t border-emerald-100/30">
                                <summary class="text-[9px] font-mono text-emerald-600 cursor-pointer hover:underline select-none">
                                    used {{ msg.tool_trace.length }} tool(s)
                                </summary>
                                <div class="pl-2 mt-1 space-y-1 font-mono text-[8px] text-slate-500 bg-white/70 p-1.5 rounded">
                                    <div v-for="(t, idx) in msg.tool_trace" :key="idx">
                                        <span class="font-bold text-emerald-700">{{ t.name }}()</span>
                                        <span class="text-slate-400 block pr-2 overflow-ellipsis overflow-hidden whitespace-nowrap">args: {{ JSON.stringify(t.args) }}</span>
                                    </div>
                                </div>
                            </details>
                        </div>
                    </div>
                    
                    <!-- Thinking placeholder -->
                    <div v-if="sending" class="flex flex-col">
                        <div class="flex items-baseline justify-between mb-0.5">
                            <span class="font-bold text-[10px] font-mono text-emerald-800">Harry (Assistant)</span>
                        </div>
                        <div class="text-xs p-2.5 rounded-lg bg-emerald-50/50 border border-emerald-100 text-slate-400 italic">
                            Harry is reasoning...
                        </div>
                    </div>
                </div>
                
                <!-- Input Box -->
                <div class="p-3 bg-white border-t border-parchment flex gap-2 items-center">
                    <input 
                        type="text" 
                        v-model="draft" 
                        @keyup.enter="sendMessage"
                        placeholder="Ask Harry about status..."
                        class="flex-1 border border-parchment rounded px-3 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-emerald-800 bg-[#faf8f4]"
                    >
                    <button @click="sendMessage" class="btn-primary py-1.5 px-3 text-xs">
                        Send
                    </button>
                </div>
            </div>
        </div>
    `
};
