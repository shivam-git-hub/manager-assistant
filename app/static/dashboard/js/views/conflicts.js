// conflicts.js - Claims Conflicts View Component

export default {
    name: 'ConflictsView',
    data() {
        return {
            loading: true,
            conflicts: []
        };
    },
    mounted() {
        this.fetchConflicts();
    },
    methods: {
        async fetchConflicts() {
            this.loading = true;
            try {
                const res = await fetch('../api/kb/conflicts?status=open');
                if (res.ok) {
                    this.conflicts = await res.json();
                }
            } catch (err) {
                console.error('Error fetching conflicts:', err);
            } finally {
                this.loading = false;
            }
        },
        async resolveConflict(conflictId, action) {
            const decision = action === 'resolved' ? 'Resolve' : 'Dismiss';
            const note = prompt(`Enter ${decision} resolution note:`, '');
            if (note === null) return;
            
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
                    await this.fetchConflicts();
                } else {
                    const err = await res.json();
                    alert(`Error: ${err.detail || 'Could not resolve conflict'}`);
                }
            } catch (err) {
                console.error('Error resolving conflict:', err);
            }
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
        }
    },
    template: `
        <div>
            <div class="flex items-center justify-between mb-8 border-b-parchment pb-4">
                <div>
                    <h2 class="text-2xl font-semibold serif-font mb-1">Open Claims Conflicts</h2>
                    <p class="text-xs text-muted">Contradictions identified between different team members' statements that require manual executive resolution.</p>
                </div>
            </div>

            <!-- Loading -->
            <div v-if="loading" class="density-card flex flex-col items-center justify-center p-12 text-center">
                <p class="text-sm font-semibold text-slate-600 animate-pulse">Analyzing DB claims alignment...</p>
            </div>

            <!-- Empty State -->
            <div v-else-if="conflicts.length === 0" class="density-card flex flex-col items-center justify-center p-12 text-center">
                <div class="w-12 h-12 bg-emerald-50 text-emerald-800 rounded-full flex items-center justify-center mb-3">
                    ✔
                </div>
                <p class="text-sm font-semibold text-slate-800">No open claims conflicts found.</p>
                <p class="text-xs text-slate-400 mt-1">Harry is continuously watching project logs for contradictions.</p>
            </div>

            <!-- Table -->
            <div v-else class="density-card p-0 overflow-hidden bg-white">
                <table class="density-table">
                    <thead>
                        <tr>
                            <th class="small-caps" style="width: 15%">Severity</th>
                            <th class="small-caps" style="width: 20%">Entity Slug</th>
                            <th class="small-caps" style="width: 45%">Contradiction Description</th>
                            <th class="small-caps" style="width: 20%">Actions</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr v-for="c in conflicts" :key="c.id">
                            <!-- Severity -->
                            <td class="align-top font-semibold">
                                <span class="px-2 py-0.5 rounded text-[10px] font-bold uppercase" :class="{
                                    'bg-red-100 text-red-800': c.severity === 'high',
                                    'bg-amber-100 text-amber-800': c.severity === 'medium',
                                    'bg-slate-100 text-slate-700': c.severity === 'low'
                                }">
                                    {{ c.severity }}
                                </span>
                            </td>
                            
                            <!-- Entity Slug -->
                            <td class="align-top font-mono text-xs text-slate-700 font-semibold">
                                <a :href="'#/project/' + c.entity_slug" class="hover:underline text-emerald-800">
                                    {{ c.entity_slug || 'project:phoenix' }}
                                </a>
                            </td>
                            
                            <!-- Description -->
                            <td class="align-top">
                                <div class="text-xs font-medium text-slate-800 leading-relaxed pr-8">
                                    {{ c.description }}
                                </div>
                                <div class="text-[9px] text-slate-400 font-mono mt-1.5">
                                    Detected at: {{ formatTime(c.detected_at) }}
                                </div>
                            </td>
                            
                            <!-- Actions -->
                            <td class="align-top flex gap-1.5">
                                <button @click="resolveConflict(c.id, 'resolved')" class="btn-outline border-emerald-100 text-emerald-800 hover:bg-emerald-50 text-xs py-1 px-2.5 font-semibold">
                                    Resolve
                                </button>
                                <button @click="resolveConflict(c.id, 'dismissed')" class="btn-outline text-slate-500 text-xs py-1 px-2.5">
                                    Dismiss
                                </button>
                            </td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>
    `
};
