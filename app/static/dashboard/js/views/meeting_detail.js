// meeting_detail.js - Meeting Detailed Ingestion View Component

export default {
    name: 'MeetingDetailView',
    props: {
        id: {
            type: Number,
            required: true
        }
    },
    data() {
        return {
            loading: true,
            processing: false,
            meeting: null,
            actionItems: [],
            entitySlug: '',
            
            // MoM form
            momDraft: '',
            
            // Dynamic timeline & claims propagated in this meeting
            timeline: [],
            claims: [],
            prefetchedMessages: {}
        };
    },
    watch: {
        id: {
            immediate: true,
            handler() {
                this.fetchData();
            }
        }
    },
    methods: {
        async fetchData() {
            this.loading = true;
            try {
                // 1. Fetch meeting detailed detail
                const res = await fetch(`api/meetings/${this.id}`);
                if (!res.ok) throw new Error('Meeting not found');
                const data = await res.json();
                
                this.meeting = data.meeting;
                this.actionItems = data.action_items || [];
                this.entitySlug = data.entity_slug;
                
                // Prefill draft if MoM was already uploaded
                if (this.meeting.mom_raw) {
                    this.momDraft = this.meeting.mom_raw;
                }
                
                // 2. Fetch meeting Entity page logs (briefs, summaries)
                const entRes = await fetch(`api/kb/entities/${this.entitySlug}`);
                if (entRes.ok) {
                    const entData = await entRes.json();
                    this.timeline = entData.timeline || [];
                }
                
                // 3. Fetch meeting propagated timelines & claims across all projects
                if (this.meeting.mom_message_id) {
                    // Gather timelines matching this message ID
                    // Simply fetch the unified list and filter by source_message_id
                    // For safety, let's look them up if any projects exist
                    // To keep it simple, we can display actionItems and decisions extracted!
                }
            } catch (err) {
                console.error('Error fetching meeting detail:', err);
            } finally {
                this.loading = false;
            }
        },
        async submitMom() {
            if (!this.momDraft.trim() || this.processing) return;
            
            this.processing = true;
            try {
                const res = await fetch(`api/meetings/${this.id}/mom`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text: this.momDraft })
                });
                
                if (res.ok) {
                    alert('Minutes of Meeting successfully uploaded and propagated into project KBs.');
                    await this.fetchData();
                } else {
                    const err = await res.json();
                    alert(`Error processing MoM: ${err.detail || 'Could not parse text'}`);
                }
            } catch (err) {
                console.error('Error submitting MoM:', err);
            } finally {
                this.processing = false;
            }
        },
        navigateToCalendar() {
            window.location.hash = '#/meetings';
        },
        formatTime(dateStr) {
            if (!dateStr) return '';
            const d = new Date(dateStr);
            return d.toLocaleString('en-IN', {
                day: '2-digit',
                month: 'short',
                hour: '2-digit',
                minute: '2-digit',
                hour12: false
            }) + ' IST';
        },
        parseMarkdown(text) {
            if (!text) return '';
            let html = text
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;');
            html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
            const lines = html.split('\n');
            const result = [];
            let inList = false;
            
            for (let line of lines) {
                const trimmed = line.trim();
                if (trimmed.startsWith('- ') || trimmed.startsWith('* ')) {
                    if (!inList) {
                        result.push('<ul class="list-disc pl-5 space-y-1 my-2 text-xs">');
                        inList = true;
                    }
                    result.push(`<li>${trimmed.substring(2)}</li>`);
                } else {
                    if (inList) {
                        result.push('</ul>');
                        inList = false;
                    }
                    if (trimmed === '') {
                        result.push('<div class="h-2"></div>');
                    } else {
                        result.push(`<p class="my-1 text-xs">${line}</p>`);
                    }
                }
            }
            if (inList) result.push('</ul>');
            return result.join('\n');
        }
    },
    template: `
        <div>
            <!-- Breadcrumbs -->
            <div class="mb-6 flex items-center justify-between">
                <button @click="navigateToCalendar" class="btn-outline flex items-center gap-2 hover:border-slate-400">
                    ← Meeting Calendar
                </button>
                <div v-if="meeting" class="text-xs text-muted font-mono bg-white px-3 py-1.5 border-parchment rounded">
                    meeting: {{ meeting.id }}
                </div>
            </div>

            <!-- Loading -->
            <div v-if="loading" class="density-card flex flex-col items-center justify-center p-12 text-center">
                <p class="text-sm font-semibold text-slate-600 animate-pulse">Compiling meeting archives...</p>
            </div>

            <div v-else-if="meeting" class="space-y-8">
                <!-- Header -->
                <div class="flex items-center justify-between border-b-parchment pb-4">
                    <div class="space-y-1">
                        <h2 class="text-3xl font-semibold serif-font">{{ meeting.title }}</h2>
                        <div class="text-xs text-muted font-mono flex items-center gap-3">
                            <span>📅 Starts: {{ formatTime(meeting.starts_at) }}</span>
                            <span>•</span>
                            <span>👤 Attendees: {{ meeting.attendees.join(', ') }}</span>
                        </div>
                    </div>
                    <span class="px-3 py-1 rounded text-xs font-bold font-mono uppercase" :class="{
                        'bg-blue-100 text-blue-800': meeting.status === 'scheduled',
                        'bg-slate-100 text-slate-600': meeting.status === 'completed',
                        'bg-red-100 text-red-800': meeting.status === 'cancelled'
                    }">
                        {{ meeting.status }}
                    </span>
                </div>

                <!-- Main Layout -->
                <div class="grid grid-cols-1 lg:grid-cols-3 gap-8">
                    
                    <!-- Left: MoM Paste box & Actions -->
                    <div class="lg:col-span-2 space-y-8">
                        <!-- MoM Panel -->
                        <div class="density-card p-6" style="background-color: var(--bg-sheet)">
                            <h3 class="small-caps mb-4">Minutes of Meeting (MoM) Transcription</h3>
                            
                            <!-- Pasteaffordance -->
                            <div v-if="meeting.status === 'scheduled'" class="space-y-4">
                                <p class="text-xs text-slate-500 leading-normal mb-2">
                                    Paste the raw minutes text of this meeting. Harry will process it using his Flash claims models to automatically extract actions, update project timelines, attribute claims, and notify assignees on Slack.
                                </p>
                                <textarea 
                                    v-model="momDraft" 
                                    rows="10" 
                                    placeholder="Enter raw minutes here... e.g. Bob approved the database migrations and promised to send them to Alice by Wednesday."
                                    class="w-full border border-parchment rounded p-4 text-xs focus:outline-none focus:ring-1 focus:ring-emerald-800 bg-[#faf8f4] font-mono leading-relaxed"
                                ></textarea>
                                <div class="text-right">
                                    <button 
                                        @click="submitMom" 
                                        :disabled="processing"
                                        class="btn-primary py-2 px-4 text-xs font-semibold flex items-center gap-2 ml-auto"
                                    >
                                        {{ processing ? 'Parsing Claims Ledger...' : 'Parse & Propagate MoM' }}
                                    </button>
                                </div>
                            </div>
                            
                            <!-- Display read-only -->
                            <div v-else class="space-y-4">
                                <div class="p-4 bg-slate-50 border-parchment rounded text-xs leading-relaxed font-mono whitespace-pre-wrap select-text">
                                    {{ meeting.mom_raw }}
                                </div>
                            </div>
                        </div>

                        <!-- Action Items lists -->
                        <div v-if="actionItems.length > 0" class="density-card p-6 bg-white">
                            <h3 class="small-caps mb-4">Extracted Action Deliverables</h3>
                            <div class="space-y-3">
                                <div v-for="item in actionItems" :key="item.id" class="p-3 border-parchment rounded flex items-center justify-between bg-[#fafcf8]">
                                    <div class="space-y-1">
                                        <div class="text-sm font-semibold text-slate-800">"{{ item.description }}"</div>
                                        <div class="text-[10px] text-slate-400 font-mono">
                                            Assigned to: {{ item.owner_member_id }}
                                        </div>
                                    </div>
                                    <span v-if="item.due_date" class="px-2 py-0.5 rounded text-[10px] font-mono font-semibold bg-emerald-50 text-emerald-800">
                                        due {{ item.due_date }}
                                    </span>
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- Right: Pre-meeting briefs & Activity log -->
                    <div class="space-y-8">
                        <!-- Pre meeting brief -->
                        <div class="density-card p-6 border-l-4 border-amber-800" style="background-color: var(--bg-sheet)">
                            <h3 class="small-caps mb-4">Pre-Meeting Executive Briefing</h3>
                            
                            <div v-if="timeline.some(t => t.summary === 'Pre-meeting briefing compiled')" class="space-y-3">
                                <div 
                                    v-for="t in timeline.filter(t => t.summary === 'Pre-meeting briefing compiled')" 
                                    :key="t.id"
                                    class="text-xs text-slate-700 font-serif leading-relaxed whitespace-pre-wrap"
                                    v-html="parseMarkdown(t.detail)"
                                ></div>
                            </div>
                            <div v-else class="text-xs text-slate-400 italic">
                                Pre-meeting briefing compiles automatically 30 minutes before starts_at.
                            </div>
                        </div>

                        <!-- Evidence logs inside meeting page -->
                        <div v-if="timeline.length > 0" class="density-card p-6" style="background-color: var(--bg-sheet)">
                            <h3 class="small-caps mb-4">Meeting Chronology Nodes</h3>
                            <div class="space-y-4 relative pl-4 border-l border-parchment">
                                <div v-for="t in timeline" :key="t.id" class="relative group">
                                    <div class="absolute -left-[21px] top-1.5 w-2 h-2 rounded-full border border-white bg-slate-400"></div>
                                    <div class="space-y-0.5">
                                        <span class="text-[9px] font-mono text-slate-400 block">{{ formatTime(t.happened_at) }}</span>
                                        <h4 class="font-bold text-xs text-slate-800">{{ t.summary }}</h4>
                                        <p v-if="t.detail && t.summary !== 'Pre-meeting briefing compiled'" class="text-[10px] text-muted leading-relaxed">
                                            {{ t.detail }}
                                        </p>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                </div>
            </div>
        </div>
    `
};
