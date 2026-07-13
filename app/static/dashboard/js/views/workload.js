// workload.js - Team Workload View Component

export default {
    name: 'WorkloadView',
    data() {
        return {
            loading: true,
            members: [],
            tasks: [],
            followups: []
        };
    },
    computed: {
        processedWorkload() {
            // Exclude Harry
            const team = this.members.filter(m => m.id !== 'U_HARRY');
            
            return team.map(m => {
                // Get tasks assigned to this user
                const userTasks = this.tasks.filter(t => t.assignee_id === m.id);
                const pending = userTasks.filter(t => t.status === 'pending').length;
                const inProgress = userTasks.filter(t => t.status === 'in_progress').length;
                const blocked = userTasks.filter(t => t.status === 'blocked').length;
                const completed = userTasks.filter(t => t.status === 'completed').length;
                const totalActive = pending + inProgress + blocked;
                
                // Open follow-ups
                const activeFollowups = this.followups.filter(f => f.target_member_id === m.id && f.status === 'open').length;
                const escalatedFollowups = this.followups.filter(f => f.target_member_id === m.id && f.status === 'escalated').length;
                
                return {
                    id: m.id,
                    name: m.name,
                    role: m.role,
                    task_stats: {
                        pending,
                        inProgress,
                        blocked,
                        completed,
                        totalActive
                    },
                    followups_count: activeFollowups,
                    escalated_count: escalatedFollowups
                };
            });
        },
        maxActiveTasks() {
            const counts = this.processedWorkload.map(w => w.task_stats.totalActive);
            return Math.max(...counts, 1); // Avoid division by zero
        }
    },
    mounted() {
        this.fetchWorkloadData();
    },
    methods: {
        async fetchWorkloadData() {
            this.loading = true;
            try {
                const [teamRes, taskRes, followupRes] = await Promise.all([
                    fetch('api/team'),
                    fetch('api/tasks'),
                    fetch('api/followups?status=open')
                ]);
                
                if (teamRes.ok) this.members = await teamRes.json();
                if (taskRes.ok) this.tasks = await taskRes.json();
                if (followupRes.ok) this.followups = await followupRes.json();
            } catch (err) {
                console.error('Error fetching workload data:', err);
            } finally {
                this.loading = false;
            }
        },
        getPercentWidth(value) {
            return Math.min(100, Math.round((value / this.maxActiveTasks) * 100));
        }
    },
    template: `
        <div>
            <div class="flex items-center justify-between mb-8 border-b-parchment pb-4">
                <div>
                    <h2 class="text-2xl font-semibold serif-font mb-1">Team Workload & Accountability</h2>
                    <p class="text-xs text-muted">A clear audit trail of active task distributions and silent follow-up escalations per team member.</p>
                </div>
            </div>

            <!-- Loading -->
            <div v-if="loading" class="density-card flex flex-col items-center justify-center p-12 text-center">
                <p class="text-sm font-semibold text-slate-600 animate-pulse">Compiling task ratios...</p>
            </div>

            <div v-else class="space-y-6">
                <div v-for="w in processedWorkload" :key="w.id" class="density-card bg-white p-6 flex flex-col md:flex-row gap-6 items-start md:items-center justify-between">
                    <!-- Name Profile -->
                    <div style="width: 25%">
                        <h3 class="font-bold text-base text-slate-800 leading-tight mb-0.5">{{ w.name }}</h3>
                        <p class="text-xs text-muted">{{ w.role }}</p>
                        <div class="text-[10px] text-slate-400 font-mono mt-1">ID: {{ w.id }}</div>
                    </div>
                    
                    <!-- Distribution Chart Bar -->
                    <div class="flex-1 w-full space-y-2">
                        <div class="flex justify-between text-xs font-semibold text-slate-600">
                            <span>Active Tasks: {{ w.task_stats.totalActive }}</span>
                            <span class="text-muted">Completed: {{ w.task_stats.completed }}</span>
                        </div>
                        <!-- Custom CSS Bar -->
                        <div class="h-6 w-full bg-slate-100 rounded overflow-hidden flex" style="max-width: 450px">
                            <!-- Blocked width -->
                            <div v-if="w.task_stats.blocked > 0" 
                                 :style="{ width: getPercentWidth(w.task_stats.blocked) + '%' }" 
                                 class="bg-red-400 h-full flex items-center justify-center text-[10px] font-bold text-white font-mono"
                                 title="Blocked tasks">
                                {{ w.task_stats.blocked }}
                            </div>
                            <!-- In Progress width -->
                            <div v-if="w.task_stats.inProgress > 0" 
                                 :style="{ width: getPercentWidth(w.task_stats.inProgress) + '%' }" 
                                 class="bg-blue-400 h-full flex items-center justify-center text-[10px] font-bold text-white font-mono"
                                 title="In progress tasks">
                                {{ w.task_stats.inProgress }}
                            </div>
                            <!-- Pending width -->
                            <div v-if="w.task_stats.pending > 0" 
                                 :style="{ width: getPercentWidth(w.task_stats.pending) + '%' }" 
                                 class="bg-slate-300 h-full flex items-center justify-center text-[10px] font-bold text-slate-700 font-mono"
                                 title="Pending tasks">
                                {{ w.task_stats.pending }}
                            </div>
                        </div>
                    </div>

                    <!-- Accountability Pings Indicators -->
                    <div class="flex gap-4 border-l border-parchment pl-6 shrink-0" style="width: 25%">
                        <div>
                            <span class="text-xs font-bold text-slate-400 block small-caps">Open Followups</span>
                            <span class="text-xl font-bold font-mono" :class="w.followups_count > 0 ? 'text-blue-600' : 'text-slate-500'">
                                {{ w.followups_count }}
                            </span>
                        </div>
                        <div class="border-l border-parchment pl-4">
                            <span class="text-xs font-bold text-slate-400 block small-caps">Escalated</span>
                            <span class="text-xl font-bold font-mono" :class="w.escalated_count > 0 ? 'text-red-600' : 'text-slate-500'">
                                {{ w.escalated_count }}
                            </span>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `
};
