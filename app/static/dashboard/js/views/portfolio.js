// portfolio.js - Portfolio View Component

export default {
    name: 'PortfolioView',
    props: {
        portfolioData: {
            type: Array,
            required: true
        }
    },
    methods: {
        formatTime(dateStr) {
            if (!dateStr) return 'No activity';
            const d = new Date(dateStr);
            return d.toLocaleString('en-IN', {
                day: '2-digit',
                month: 'short',
                hour: '2-digit',
                minute: '2-digit',
                hour12: false
            }) + ' IST';
        },
        navigateToProject(slug) {
            window.location.hash = `#/project/${slug}`;
        }
    },
    template: `
        <div>
            <div class="flex items-center justify-between mb-8 border-b-parchment pb-4">
                <div>
                    <h2 class="text-2xl font-semibold serif-font mb-1">Project Portfolio</h2>
                    <p class="text-xs text-muted">Executive summary of active projects sorted by severity rank and last activity.</p>
                </div>
            </div>

            <!-- Empty State -->
            <div v-if="portfolioData.length === 0" class="density-card flex flex-col items-center justify-center p-12 text-center">
                <p class="text-sm font-semibold text-slate-600">No projects are being tracked currently.</p>
                <p class="text-xs text-slate-300 mt-1">Add projects from the main Integration Simulator.</p>
            </div>

            <!-- Portfolio Table -->
            <div v-else class="density-card p-0 overflow-hidden">
                <table class="density-table">
                    <thead>
                        <tr>
                            <th class="small-caps" style="width: 25%">Project Name</th>
                            <th class="small-caps" style="width: 15%">Health</th>
                            <th class="small-caps" style="width: 30%">Current Status / Truth</th>
                            <th class="small-caps" style="width: 15%">Tasks Status</th>
                            <th class="small-caps" style="width: 15%">Last Activity</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr v-for="p in portfolioData" :key="p.project_id" @click="navigateToProject(p.entity_slug)">
                            <!-- Name -->
                            <td class="align-top font-medium text-slate-800">
                                <span class="text-sm font-bold">{{ p.name }}</span>
                                <div v-if="p.conflict_count > 0" class="inline-flex items-center ml-2 px-1.5 py-0.5 rounded text-[10px] font-bold bg-red-100 text-red-800 font-mono">
                                    {{ p.conflict_count }} Conflict(s)
                                </div>
                            </td>
                            
                            <!-- Health -->
                            <td class="align-top">
                                <div class="flex items-center gap-2">
                                    <span class="health-dot" :class="p.health.toLowerCase()"></span>
                                    <span class="text-xs font-semibold text-slate-700 capitalize">{{ p.health }}</span>
                                </div>
                                <div class="text-[11px] text-muted leading-relaxed mt-1 italic pr-4" v-if="p.health_reasons">
                                    "{{ p.health_reasons }}"
                                </div>
                            </td>
                            
                            <!-- Truth / Status Teaser -->
                            <td class="align-top">
                                <div class="text-xs text-slate-800 font-medium leading-relaxed max-w-md">
                                    {{ p.compiled_truth_teaser || 'Pending synthesis...' }}
                                </div>
                            </td>
                            
                            <!-- Tasks Counts -->
                            <td class="align-top">
                                <div class="flex flex-wrap gap-1 font-mono text-[10px] font-semibold">
                                    <span v-if="p.task_counts.in_progress > 0" class="px-1.5 py-0.5 rounded bg-blue-100 text-blue-800">
                                        IP: {{ p.task_counts.in_progress }}
                                    </span>
                                    <span v-if="p.task_counts.blocked > 0" class="px-1.5 py-0.5 rounded bg-amber-100 text-amber-800">
                                        BL: {{ p.task_counts.blocked }}
                                    </span>
                                    <span v-if="p.task_counts.pending > 0" class="px-1.5 py-0.5 rounded bg-slate-100 text-slate-700">
                                        PE: {{ p.task_counts.pending }}
                                    </span>
                                    <span v-if="p.task_counts.completed > 0" class="px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-800">
                                        CO: {{ p.task_counts.completed }}
                                    </span>
                                    <span v-if="p.task_counts.in_progress === 0 && p.task_counts.blocked === 0 && p.task_counts.pending === 0 && p.task_counts.completed === 0" class="text-slate-400">
                                        No tasks
                                    </span>
                                </div>
                            </td>
                            
                            <!-- Last Activity -->
                            <td class="align-top text-xs text-slate-500 font-mono">
                                {{ formatTime(p.last_activity_at) }}
                            </td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>
    `
};
