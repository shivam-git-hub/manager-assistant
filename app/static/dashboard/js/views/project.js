// project.js - Project Detailed View Component

export default {
    name: 'ProjectView',
    props: {
        slug: {
            type: String,
            required: true
        }
    },
    data() {
        return {
            loading: true,
            entity: null,
            compiledTruth: '',
            timeline: [],
            claims: [],
            conflicts: [],
            tasks: [],
            prefetchedMessages: {},
            
            // Popover State
            popoverVisible: false,
            popoverTimelineId: null,
            popoverMessage: null,
            popoverX: 0,
            popoverY: 0,
            
            // Claims Toggle
            showSuperseded: false
        };
    },
    computed: {
        parsedCompiledTruth() {
            if (!this.compiledTruth) return 'Pending synthesis...';
            // Regex to find [T12] or similar and replace with a span
            return this.compiledTruth.replace(/\[T(\d+)\]/g, (match, id) => {
                return `<span class="citation-chip" data-timeline-id="${id}" style="font-family: var(--font-sans);">${match}</span>`;
            });
        },
        claimsByHolder() {
            const groups = {};
            this.claims.forEach(c => {
                if (!groups[c.holder]) {
                    groups[c.holder] = [];
                }
                groups[c.holder].push(c);
            });
            return groups;
        }
    },
    watch: {
        slug: {
            immediate: true,
            handler() {
                this.fetchProjectData();
            }
        }
    },
    methods: {
        async fetchProjectData() {
            this.loading = true;
            try {
                // 1. Fetch Entity details
                const res = await fetch(`../api/kb/entities/${this.slug}`);
                if (!res.ok) throw new Error('Project details not found');
                const data = await res.json();
                
                this.entity = data.entity;
                this.compiledTruth = data.compiled_truth || '';
                this.timeline = data.timeline || [];
                this.claims = data.claims || [];
                this.conflicts = data.conflicts || [];
                
                // 2. Fetch Project Tasks (using ref_id)
                if (this.entity.ref_id) {
                    const taskRes = await fetch(`../api/tasks?project_id=${this.entity.ref_id}`);
                    if (taskRes.ok) {
                        this.tasks = await taskRes.json();
                    }
                }
                
                // 3. Prefetch all cited messages in the timeline
                const msgIdsToFetch = new Set();
                this.timeline.forEach(t => {
                    if (t.source_message_id) msgIdsToFetch.add(t.source_message_id);
                });
                this.claims.forEach(c => {
                    if (c.source_message_id) msgIdsToFetch.add(c.source_message_id);
                });
                
                // Fetch in parallel
                await Promise.all(Array.from(msgIdsToFetch).map(async (msgId) => {
                    if (!this.prefetchedMessages[msgId]) {
                        try {
                            const msgRes = await fetch(`../api/messages/${msgId}`);
                            if (msgRes.ok) {
                                this.prefetchedMessages[msgId] = await msgRes.json();
                            }
                        } catch (err) {
                            console.error(`Error prefetching message ${msgId}:`, err);
                        }
                    }
                }));
                
            } catch (err) {
                console.error(err);
            } finally {
                this.loading = false;
            }
        },
        
        handleTruthHover(event) {
            const target = event.target;
            const tId = target.getAttribute('data-timeline-id');
            if (tId) {
                const timelineId = parseInt(tId, 10);
                const entry = this.timeline.find(t => t.id === timelineId);
                
                if (entry) {
                    this.popoverTimelineId = timelineId;
                    this.popoverMessage = entry.source_message_id ? this.prefetchedMessages[entry.source_message_id] : null;
                    
                    // Position Popover nicely relative to mouse cursor
                    const rect = target.getBoundingClientRect();
                    this.popoverX = window.scrollX + rect.left;
                    this.popoverY = window.scrollY + rect.bottom + 8;
                    this.popoverVisible = true;
                }
            }
        },
        
        handleTruthLeave() {
            this.popoverVisible = false;
        },
        
        async resolveConflict(conflictId, action) {
            const decision = action === 'resolved' ? 'Resolve' : 'Dismiss';
            const note = prompt(`Enter ${decision} resolution note:`, '');
            if (note === null) return; // user cancelled
            
            try {
                const res = await fetch(`../api/kb/conflicts/${conflictId}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        status: action,
                        resolution_note: note
                    })
                });
                
                if (res.ok) {
                    alert(`Conflict successfully ${action}.`);
                    await this.fetchProjectData();
                } else {
                    const err = await res.json();
                    alert(`Error: ${err.detail || 'Could not resolve conflict'}`);
                }
            } catch (err) {
                console.error('Error resolving conflict:', err);
            }
        },
        
        navigateToPortfolio() {
            window.location.hash = '#/portfolio';
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
            });
        },
        
        getInitials(name) {
            if (!name) return 'U';
            return name.split(' ').map(p => p[0]).join('').toUpperCase().substring(0, 2);
        },
        
        getUserAvatarClass(name) {
            const colors = ['bg-blue-600', 'bg-emerald-600', 'bg-purple-600', 'bg-amber-600', 'bg-rose-600', 'bg-indigo-600'];
            let hash = 0;
            for (let i = 0; i < name.length; i++) {
                hash = name.charCodeAt(i) + ((hash << 5) - hash);
            }
            return colors[Math.abs(hash) % colors.length];
        }
    },
    template: `
        <div>
            <!-- Breadcrumbs -->
            <div class="mb-6 flex items-center justify-between">
                <button @click="navigateToPortfolio" class="btn-outline flex items-center gap-2 hover:border-slate-400">
                    ← Portfolio
                </button>
                <div v-if="entity" class="text-xs text-muted font-mono bg-white px-3 py-1.5 border-parchment rounded">
                    slug: {{ entity.slug }}
                </div>
            </div>

            <!-- Loading State -->
            <div v-if="loading" class="density-card flex flex-col items-center justify-center p-12 text-center">
                <p class="text-sm font-semibold text-slate-600 animate-pulse">Loading project intelligence...</p>
            </div>

            <div v-else-if="entity" class="space-y-8">
                <!-- Project Summary Header -->
                <div class="flex items-center gap-4 border-b-parchment pb-4">
                    <h2 class="text-3xl font-semibold serif-font">{{ entity.name }}</h2>
                    <span v-if="tasks.some(t => t.status === 'blocked')" class="px-2 py-0.5 rounded text-[11px] font-bold bg-amber-100 text-amber-800 uppercase font-mono">
                        Blocked Task Present
                    </span>
                </div>

                <!-- 1. Executive Summary (Compiled Truth) -->
                <div class="density-card p-6 border-l-4 border-emerald-800" style="background-color: var(--bg-sheet)">
                    <h3 class="small-caps mb-3">Executive Summary</h3>
                    <div 
                        @mouseover="handleTruthHover" 
                        @mouseleave="handleTruthLeave"
                        class="text-base text-slate-800 leading-relaxed font-serif prose whitespace-pre-wrap"
                        v-html="parsedCompiledTruth"
                    ></div>
                    <div v-if="entity.truth_updated_at" class="text-[10px] text-muted font-mono mt-3 text-right">
                        Last Synthesized: {{ formatTime(entity.truth_updated_at) }} IST
                    </div>
                </div>

                <!-- Hover Citation Popover -->
                <div 
                    v-if="popoverVisible" 
                    class="popover-container flex flex-col space-y-3 bg-white p-4 border border-parchment shadow-lg rounded"
                    :style="{ left: popoverX + 'px', top: popoverY + 'px' }"
                >
                    <div class="border-b-parchment pb-1.5 flex items-center justify-between">
                        <span class="font-bold font-serif text-sm text-emerald-800">Timeline Link [T{{ popoverTimelineId }}]</span>
                    </div>
                    
                    <div v-if="popoverMessage">
                        <div class="flex items-center gap-2 mb-1.5">
                            <span class="font-bold text-xs text-slate-800">{{ popoverMessage.sender_mapped_name }}</span>
                            <span class="text-[10px] text-slate-400 capitalize bg-slate-100 px-1.5 py-0.5 rounded font-mono">{{ popoverMessage.source }}</span>
                        </div>
                        <p class="text-xs text-slate-700 font-sans leading-relaxed italic bg-slate-50 p-2 border-parchment rounded">
                            "{{ popoverMessage.content }}"
                        </p>
                        <div class="text-[9px] text-slate-400 font-mono mt-1 text-right">
                            Timestamp: {{ formatTime(popoverMessage.timestamp) }}
                        </div>
                    </div>
                    <div v-else class="text-xs text-slate-400 italic">
                        No underlying Slack/Outlook message mapped for this timeline entry.
                    </div>
                </div>

                <!-- 2. Active Claims Deadlock Strip -->
                <div v-if="conflicts.length > 0" class="density-card border-l-4 border-red-800 bg-red-50/50 p-6 space-y-4">
                    <h3 class="small-caps text-red-800 mb-2">🚨 Identified Claim Contradictions</h3>
                    <div v-for="c in conflicts" :key="c.id" class="p-4 bg-white border border-red-100 rounded flex flex-col md:flex-row md:items-center justify-between gap-4">
                        <div class="space-y-1">
                            <div class="flex items-center gap-2">
                                <span class="px-1.5 py-0.5 text-[9px] font-bold font-mono bg-red-100 text-red-800 rounded uppercase">
                                    {{ c.severity }} severity
                                </span>
                                <span class="font-semibold text-xs text-slate-700">Conflict #{{ c.id }}</span>
                            </div>
                            <p class="text-sm font-medium text-slate-800 mt-1 leading-relaxed">
                                {{ c.description }}
                            </p>
                        </div>
                        <div class="flex gap-2 shrink-0">
                            <button @click="resolveConflict(c.id, 'resolved')" class="btn-outline border-red-200 text-red-800 hover:bg-red-50 hover:border-red-400 font-semibold px-3 py-1.5 text-xs">
                                Resolve Conflict
                            </button>
                            <button @click="resolveConflict(c.id, 'dismissed')" class="btn-outline text-slate-600 hover:bg-slate-50 text-xs">
                                Dismiss
                            </button>
                        </div>
                    </div>
                </div>

                <div class="grid grid-cols-1 lg:grid-cols-3 gap-8">
                    <!-- 3. Left Columns: Tasks and Claims -->
                    <div class="lg:col-span-2 space-y-8">
                        <!-- Tasks Board -->
                        <div class="density-card p-6" style="background-color: var(--bg-sheet)">
                            <h3 class="small-caps mb-4">Current Project Deliverables</h3>
                            <div v-if="tasks.length === 0" class="text-xs text-slate-400 italic">
                                No tasks mapped to this project yet.
                            </div>
                            <div v-else class="space-y-3">
                                <div v-for="t in tasks" :key="t.id" class="p-3 border-parchment rounded flex items-center justify-between hover:bg-slate-50/50">
                                    <div class="space-y-1">
                                        <div class="font-medium text-sm text-slate-800">{{ t.title }}</div>
                                        <div v-if="t.description" class="text-xs text-muted leading-relaxed">
                                            {{ t.description }}
                                        </div>
                                    </div>
                                    <div class="flex items-center gap-3">
                                        <span class="px-2 py-0.5 rounded text-[10px] font-bold font-mono uppercase" :class="{
                                            'bg-slate-100 text-slate-700': t.status === 'pending',
                                            'bg-blue-100 text-blue-800': t.status === 'in_progress',
                                            'bg-amber-100 text-amber-800': t.status === 'blocked',
                                            'bg-emerald-100 text-emerald-800': t.status === 'completed'
                                        }">
                                            {{ t.status }}
                                        </span>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <!-- Claims Ledger -->
                        <div class="density-card p-6" style="background-color: var(--bg-sheet)">
                            <h3 class="small-caps mb-4">Attributed Claims Matrix</h3>
                            <div v-if="claims.length === 0" class="text-xs text-slate-400 italic">
                                No claims currently active for this project entity.
                            </div>
                            <div v-else class="space-y-6">
                                <div v-for="(claimsList, holder) in claimsByHolder" :key="holder" class="space-y-3">
                                    <div class="flex items-center gap-2 border-b-parchment pb-1.5">
                                        <div class="w-6 h-6 rounded-lg text-white font-bold text-[10px] flex items-center justify-center shrink-0" :class="getUserAvatarClass(holder)">
                                            {{ getInitials(holder) }}
                                        </div>
                                        <span class="font-bold text-xs text-slate-800 font-mono">{{ holder }} Claims</span>
                                    </div>
                                    <div class="pl-8 space-y-2">
                                        <div v-for="c in claimsList" :key="c.id" class="text-xs text-slate-800 flex items-start gap-3 p-2 bg-slate-50/50 rounded border-parchment">
                                            <div class="flex-1 leading-relaxed">
                                                "{{ c.claim }}"
                                                <div class="text-[9px] text-slate-400 font-mono mt-1">
                                                    Confidence: {{ c.weight.toFixed(2) }} | Claimed: {{ formatTime(c.claimed_at) }}
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- 4. Right Column: Timeline -->
                    <div class="space-y-8">
                        <div class="density-card p-6" style="background-color: var(--bg-sheet)">
                            <h3 class="small-caps mb-4">System Evidence Trail</h3>
                            <div v-if="timeline.length === 0" class="text-xs text-slate-400 italic">
                                No evidence logged on the timeline yet.
                            </div>
                            <div v-else class="space-y-6 relative pl-4 border-l border-parchment">
                                <div v-for="t in timeline" :key="t.id" class="relative group">
                                    <!-- Node marker -->
                                    <div class="absolute -left-[21px] top-1.5 w-2 h-2 rounded-full border border-white bg-slate-400 group-hover:bg-emerald-800 transition"></div>
                                    <div class="space-y-1">
                                        <span class="text-[9px] font-mono text-slate-400 block">{{ formatTime(t.happened_at) }}</span>
                                        <div class="text-xs font-semibold text-slate-800 flex items-center gap-1.5">
                                            <span class="font-bold text-emerald-800 font-serif shrink-0">[T{{ t.id }}]</span>
                                            <span>{{ t.summary }}</span>
                                        </div>
                                        <p v-if="t.detail" class="text-[11px] text-muted leading-relaxed">
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
