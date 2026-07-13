// workload.js - Team Workload, Leaves, Reassignments & Newsletter View Component

export default {
    name: 'WorkloadView',
    data() {
        return {
            loading: true,
            members: [],
            tasks: [],
            followups: [],
            leaves: [],
            reassignments: [],
            digests: [],
            // Form state for leave marking
            leaveForm: {
                member_id: '',
                starts_on: '',
                ends_on: '',
                reason: ''
            },
            formSubmitting: false,
            formError: '',
            formSuccess: ''
        };
    },
    computed: {
        processedWorkload() {
            // Exclude Harry
            const team = this.members.filter(m => m.id !== 'U_HARRY');
            
            // Resolve today string from global simulated clock
            const todayStr = this.$root.clockState && this.$root.clockState.sim_time_ist 
                ? this.$root.clockState.sim_time_ist.substring(0, 10) 
                : '';

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
                
                // Find if user is on active leave today
                const activeLeave = this.leaves.find(l => l.member_id === m.id && l.starts_on <= todayStr && l.ends_on >= todayStr);

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
                    escalated_count: escalatedFollowups,
                    activeLeave
                };
            });
        },
        maxActiveTasks() {
            const counts = this.processedWorkload.map(w => w.task_stats.totalActive);
            return Math.max(...counts, 1); // Avoid division by zero
        },
        latestDigest() {
            return this.digests.length > 0 ? this.digests[0] : null;
        },
        eligibleMembersForLeave() {
            return this.members.filter(m => m.id !== 'U_HARRY');
        }
    },
    mounted() {
        this.fetchWorkloadData();
    },
    methods: {
        async fetchWorkloadData() {
            this.loading = true;
            try {
                const [teamRes, taskRes, followupRes, leavesRes, reassignRes, digestsRes] = await Promise.all([
                    fetch('api/team'),
                    fetch('api/tasks'),
                    fetch('api/followups?status=open'),
                    fetch('api/workload/leaves'),
                    fetch('api/workload/reassignments?status=suggested'),
                    fetch('api/workload/digests?limit=4')
                ]);
                
                if (teamRes.ok) this.members = await teamRes.json();
                if (taskRes.ok) this.tasks = await taskRes.json();
                if (followupRes.ok) this.followups = await followupRes.json();
                if (leavesRes.ok) this.leaves = await leavesRes.json();
                if (reassignRes.ok) this.reassignments = await reassignRes.json();
                if (digestsRes.ok) this.digests = await digestsRes.json();
            } catch (err) {
                console.error('Error fetching workload data:', err);
            } finally {
                this.loading = false;
            }
        },
        getPercentWidth(value) {
            return Math.min(100, Math.round((value / this.maxActiveTasks) * 100));
        },
        async submitLeave() {
            this.formSubmitting = true;
            this.formError = '';
            this.formSuccess = '';
            try {
                const res = await fetch('api/workload/leaves', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(this.leaveForm)
                });
                if (res.ok) {
                    this.formSuccess = 'Leave marked and suggestions drafted successfully!';
                    this.leaveForm = { member_id: '', starts_on: '', ends_on: '', reason: '' };
                    await this.fetchWorkloadData();
                } else {
                    const data = await res.json();
                    this.formError = data.detail || 'Failed to submit leave.';
                }
            } catch (err) {
                console.error('Error submitting leave:', err);
                this.formError = 'Network error submitting leave.';
            } finally {
                this.formSubmitting = false;
            }
        },
        async approveReassignment(id, taskTitle, toName) {
            const confirmed = confirm(`Are you sure you want to approve this reassignment?\n\nTask: "${taskTitle}"\nReassign to: ${toName}\n\nThis will reassign the task in the database and send Slack DM notifications to both developers immediately.`);
            if (!confirmed) return;
            
            try {
                const res = await fetch(`api/workload/reassignments/${id}/approve`, {
                    method: 'POST'
                });
                if (res.ok) {
                    await this.fetchWorkloadData();
                } else {
                    const data = await res.json();
                    alert(data.detail || 'Failed to approve reassignment.');
                }
            } catch (err) {
                console.error('Error approving reassignment:', err);
                alert('Network error approving reassignment.');
            }
        },
        async rejectReassignment(id) {
            const confirmed = confirm('Are you sure you want to reject this reassignment suggestion?');
            if (!confirmed) return;
            
            try {
                const res = await fetch(`api/workload/reassignments/${id}/reject`, {
                    method: 'POST'
                });
                if (res.ok) {
                    await this.fetchWorkloadData();
                } else {
                    const data = await res.json();
                    alert(data.detail || 'Failed to reject reassignment.');
                }
            } catch (err) {
                console.error('Error rejecting reassignment:', err);
                alert('Network error rejecting reassignment.');
            }
        },
        getMemberName(id) {
            const m = this.members.find(m => m.id === id);
            return m ? m.name : id;
        },
        getTaskTitle(id) {
            const t = this.tasks.find(t => t.id === id);
            return t ? t.title : `Task #${id}`;
        }
    },
    template: `
        <div>
            <div class="flex items-center justify-between mb-8 border-b-parchment pb-4">
                <div>
                    <h2 class="text-2xl font-semibold serif-font mb-1">Team Workload & Leave Management</h2>
                    <p class="text-xs text-muted">A clear audit trail of active task distributions, leave coverage, and training newsletter recommendations.</p>
                </div>
            </div>

            <!-- Loading -->
            <div v-if="loading" class="density-card flex flex-col items-center justify-center p-12 text-center">
                <p class="text-sm font-semibold text-slate-600 animate-pulse">Compiling task ratios...</p>
            </div>

            <div v-else class="grid grid-cols-1 lg:grid-cols-3 gap-8">
                <!-- Left Column (Span 2): Workloads & Leaves & Suggestions -->
                <div class="lg:col-span-2 space-y-8">
                    
                    <!-- Team Workload Stats -->
                    <div class="space-y-4">
                        <h3 class="text-lg font-semibold serif-font border-b border-parchment pb-1.5">Load Balance & Status</h3>
                        
                        <div v-for="w in processedWorkload" :key="w.id" class="density-card bg-white p-4 flex flex-col md:flex-row gap-4 items-start md:items-center justify-between">
                            <!-- Name Profile -->
                            <div style="width: 30%">
                                <div class="flex items-center flex-wrap gap-1.5">
                                    <h4 class="font-bold text-sm text-slate-800 leading-tight">{{ w.name }}</h4>
                                    <!-- Leave Badge -->
                                    <span v-if="w.activeLeave" class="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-bold bg-amber-100 text-amber-800 leading-none">
                                        Out till {{ w.activeLeave.ends_on }}
                                    </span>
                                </div>
                                <p class="text-[11px] text-muted">{{ w.role }}</p>
                                <div class="text-[9px] text-slate-400 font-mono mt-0.5">ID: {{ w.id }}</div>
                            </div>
                            
                            <!-- Distribution Chart Bar -->
                            <div class="flex-1 w-full space-y-1">
                                <div class="flex justify-between text-[11px] font-semibold text-slate-600">
                                    <span>Active Tasks: {{ w.task_stats.totalActive }}</span>
                                    <span class="text-muted">Completed: {{ w.task_stats.completed }}</span>
                                </div>
                                <!-- Progress Bar -->
                                <div class="h-4 w-full bg-slate-100 rounded overflow-hidden flex" style="max-width: 350px">
                                    <div v-if="w.task_stats.blocked > 0" 
                                         :style="{ width: getPercentWidth(w.task_stats.blocked) + '%' }" 
                                         class="bg-red-400 h-full flex items-center justify-center text-[9px] font-bold text-white font-mono"
                                         title="Blocked">
                                        {{ w.task_stats.blocked }}
                                    </div>
                                    <div v-if="w.task_stats.inProgress > 0" 
                                         :style="{ width: getPercentWidth(w.task_stats.inProgress) + '%' }" 
                                         class="bg-blue-400 h-full flex items-center justify-center text-[9px] font-bold text-white font-mono"
                                         title="In Progress">
                                        {{ w.task_stats.inProgress }}
                                    </div>
                                    <div v-if="w.task_stats.pending > 0" 
                                         :style="{ width: getPercentWidth(w.task_stats.pending) + '%' }" 
                                         class="bg-slate-300 h-full flex items-center justify-center text-[9px] font-bold text-slate-700 font-mono"
                                         title="Pending">
                                        {{ w.task_stats.pending }}
                                    </div>
                                </div>
                            </div>

                            <!-- Accountability Pings Indicators -->
                            <div class="flex gap-3 border-l border-parchment pl-4 shrink-0" style="width: 25%">
                                <div>
                                    <span class="text-[10px] font-bold text-slate-400 block small-caps leading-tight">Followups</span>
                                    <span class="text-sm font-bold font-mono" :class="w.followups_count > 0 ? 'text-blue-600' : 'text-slate-500'">
                                        {{ w.followups_count }}
                                    </span>
                                </div>
                                <div class="border-l border-parchment pl-3">
                                    <span class="text-[10px] font-bold text-slate-400 block small-caps leading-tight">Escalated</span>
                                    <span class="text-sm font-bold font-mono" :class="w.escalated_count > 0 ? 'text-red-600' : 'text-slate-500'">
                                        {{ w.escalated_count }}
                                    </span>
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- Leave Proposal Drafts -->
                    <div class="space-y-4">
                        <h3 class="text-lg font-semibold serif-font border-b border-parchment pb-1.5">Drafted Task Reassignments</h3>
                        
                        <div v-if="reassignments.length === 0" class="density-card bg-slate-50 p-6 text-center text-xs text-muted">
                            No active reassignment suggestions. Mark a developer on leave to draft coverage suggestions automatically.
                        </div>
                        
                        <div v-else class="space-y-3">
                            <div v-for="r in reassignments" :key="r.id" class="density-card bg-white p-4 border border-parchment flex flex-col md:flex-row gap-4 items-start md:items-center justify-between">
                                <div class="space-y-1 flex-1">
                                    <div class="flex items-center gap-1.5 flex-wrap">
                                        <span class="text-xs font-bold px-1.5 py-0.5 bg-slate-100 rounded text-slate-700">Suggestion #{{ r.id }}</span>
                                        <span class="text-xs text-slate-500">
                                            Reassign: <strong>{{ getMemberName(r.from_member_id) }}</strong> &rarr; <strong>{{ getMemberName(r.to_member_id) }}</strong>
                                        </span>
                                    </div>
                                    <p class="text-xs font-semibold text-slate-800">Task: "{{ getTaskTitle(r.task_id) }}"</p>
                                    <p class="text-[11px] text-muted leading-tight"><em>Harry's Rationale:</em> "{{ r.rationale }}"</p>
                                </div>
                                
                                <div class="flex items-center gap-2 shrink-0">
                                    <button @click="approveReassignment(r.id, getTaskTitle(r.task_id), getMemberName(r.to_member_id))" 
                                            class="px-2.5 py-1 text-xs font-semibold bg-emerald-50 text-emerald-800 border border-emerald-200 rounded hover:bg-emerald-100 transition-colors">
                                        Approve
                                    </button>
                                    <button @click="rejectReassignment(r.id)" 
                                            class="px-2.5 py-1 text-xs font-semibold bg-red-50 text-red-800 border border-red-200 rounded hover:bg-red-100 transition-colors">
                                        Reject
                                    </button>
                                </div>
                            </div>
                        </div>
                    </div>

                </div>

                <!-- Right Column (Span 1): Forms & Digests Card -->
                <div class="space-y-8">
                    
                    <!-- Mark Leave Form -->
                    <div class="density-card bg-white p-5 border border-parchment space-y-4">
                        <h3 class="text-base font-bold serif-font border-b border-parchment pb-2">Mark Team Member Leave</h3>
                        
                        <form @submit.prevent="submitLeave" class="space-y-3">
                            <div>
                                <label class="block text-[11px] font-bold text-slate-400 uppercase tracking-wider mb-1">Select Member</label>
                                <select v-model="leaveForm.member_id" required class="w-full text-xs bg-slate-50 border border-parchment rounded px-2.5 py-1.5 focus:outline-none focus:border-slate-400">
                                    <option value="" disabled>-- Select Developer --</option>
                                    <option v-for="m in eligibleMembersForLeave" :key="m.id" :value="m.id">{{ m.name }}</option>
                                </select>
                            </div>
                            
                            <div class="grid grid-cols-2 gap-3">
                                <div>
                                    <label class="block text-[11px] font-bold text-slate-400 uppercase tracking-wider mb-1">Starts On</label>
                                    <input type="date" v-model="leaveForm.starts_on" required class="w-full text-xs bg-slate-50 border border-parchment rounded px-2.5 py-1.5 focus:outline-none focus:border-slate-400" />
                                </div>
                                <div>
                                    <label class="block text-[11px] font-bold text-slate-400 uppercase tracking-wider mb-1">Ends On</label>
                                    <input type="date" v-model="leaveForm.ends_on" required class="w-full text-xs bg-slate-50 border border-parchment rounded px-2.5 py-1.5 focus:outline-none focus:border-slate-400" />
                                </div>
                            </div>
                            
                            <div>
                                <label class="block text-[11px] font-bold text-slate-400 uppercase tracking-wider mb-1">Reason (Optional)</label>
                                <input type="text" v-model="leaveForm.reason" placeholder="Vacation, conference, etc." class="w-full text-xs bg-slate-50 border border-parchment rounded px-2.5 py-1.5 focus:outline-none focus:border-slate-400" />
                            </div>
                            
                            <div v-if="formError" class="text-xs font-medium text-red-600 bg-red-50 p-2 rounded leading-tight">{{ formError }}</div>
                            <div v-if="formSuccess" class="text-xs font-medium text-emerald-700 bg-emerald-50 p-2 rounded leading-tight">{{ formSuccess }}</div>
                            
                            <button type="submit" :disabled="formSubmitting" class="w-full py-1.5 text-xs font-semibold bg-slate-800 text-white rounded hover:bg-slate-700 transition-colors disabled:opacity-50">
                                {{ formSubmitting ? 'Marking Leave...' : 'Register Leave & Draft Reassignments' }}
                            </button>
                        </form>
                    </div>

                    <!-- Technical Digest Side Card -->
                    <div class="density-card bg-white p-5 border border-parchment space-y-4">
                        <div class="flex items-center justify-between border-b border-parchment pb-2">
                            <h3 class="text-base font-bold serif-font">Weekly Technical Digest</h3>
                            <span class="text-[9px] uppercase font-bold px-1.5 py-0.5 bg-indigo-50 text-indigo-800 rounded">AI Coached</span>
                        </div>
                        
                        <div v-if="latestDigest" class="space-y-4">
                            <p class="text-[11px] text-muted italic">Tailored technical training and newsletters curated from active team blockers & conflicts on {{ latestDigest.week_start }}.</p>
                            
                            <div v-for="(item, idx) in latestDigest.content" :key="idx" class="border-b border-parchment pb-3 last:border-b-0 last:pb-0 space-y-1">
                                <div class="flex items-center justify-between">
                                    <span class="text-[9px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded" 
                                          :class="item.audience === 'team' ? 'bg-slate-100 text-slate-700' : 'bg-emerald-50 text-emerald-800'">
                                        Audience: {{ item.audience }}
                                    </span>
                                </div>
                                <h4 class="text-xs font-bold text-slate-800 leading-snug">{{ item.suggestion }}</h4>
                                <p class="text-[11px] text-muted leading-relaxed"><em>Signal Context:</em> {{ item.reason }}</p>
                            </div>
                        </div>
                        
                        <div v-else class="text-xs text-muted py-6 text-center italic">
                            No newsletter digests compiled yet. Digests generate automatically on simulated Monday mornings at 09:30 AM!
                        </div>
                    </div>

                </div>
            </div>
        </div>
    `
};
