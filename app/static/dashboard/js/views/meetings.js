// meetings.js - Meetings List / Calendar View Component

export default {
    name: 'MeetingsView',
    props: {
        clockState: {
            type: Object,
            required: true
        }
    },
    data() {
        return {
            loading: true,
            meetings: [],
            teamMembers: [],
            projects: [],
            
            // Scheduling form state
            showForm: false,
            newMeeting: {
                title: '',
                starts_at_date: '',
                starts_at_time: '10:00',
                attendees: [],
                project_id: ''
            }
        };
    },
    computed: {
        currentSimDateStr() {
            if (!this.clockState.sim_time_ist) return '';
            return this.clockState.sim_time_ist.split('T')[0];
        },
        groupedMeetings() {
            const groups = {};
            this.meetings.forEach(m => {
                const dateStr = m.starts_at.split('T')[0];
                if (!groups[dateStr]) {
                    groups[dateStr] = [];
                }
                groups[dateStr].push(m);
            });
            
            // Sort dates
            const sortedDates = Object.keys(groups).sort();
            return sortedDates.map(dateStr => {
                return {
                    dateStr,
                    isToday: dateStr === this.currentSimDateStr,
                    label: this.formatDateLabel(dateStr),
                    list: groups[dateStr]
                };
            });
        }
    },
    mounted() {
        this.fetchData();
    },
    methods: {
        async fetchData() {
            this.loading = true;
            try {
                const [meetRes, teamRes, projRes] = await Promise.all([
                    fetch('api/meetings'),
                    fetch('api/team'),
                    fetch('api/dashboard/portfolio')
                ]);
                
                if (meetRes.ok) this.meetings = await meetRes.json();
                if (teamRes.ok) {
                    const allMembers = await teamRes.json();
                    this.teamMembers = allMembers.filter(m => m.id !== 'U_HARRY');
                }
                if (projRes.ok) this.projects = await projRes.json();
                
                // Prefill date form
                if (this.currentSimDateStr) {
                    this.newMeeting.starts_at_date = this.currentSimDateStr;
                }
            } catch (err) {
                console.error('Error fetching meetings data:', err);
            } finally {
                this.loading = false;
            }
        },
        async scheduleMeeting() {
            const m = this.newMeeting;
            if (!m.title.trim() || !m.starts_at_date || !m.starts_at_time || m.attendees.length === 0) {
                alert('Please fill out all fields and select at least one attendee.');
                return;
            }
            
            const starts_at = `${m.starts_at_date} ${m.starts_at_time}:00`;
            const payload = {
                title: m.title,
                starts_at,
                attendees: m.attendees,
                project_id: m.project_id ? parseInt(m.project_id, 10) : null
            };
            
            try {
                const res = await fetch('api/meetings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                
                if (res.ok) {
                    alert('Meeting successfully scheduled.');
                    this.showForm = false;
                    // Reset form
                    this.newMeeting.title = '';
                    this.newMeeting.attendees = [];
                    this.newMeeting.project_id = '';
                    await this.fetchData();
                } else {
                    const err = await res.json();
                    alert(`Error: ${err.detail || 'Could not schedule meeting'}`);
                }
            } catch (err) {
                console.error('Error scheduling meeting:', err);
            }
        },
        toggleAttendee(id) {
            const idx = this.newMeeting.attendees.indexOf(id);
            if (idx > -1) {
                this.newMeeting.attendees.splice(idx, 1);
            } else {
                this.newMeeting.attendees.push(id);
            }
        },
        formatTime(dateStr) {
            const d = new Date(dateStr);
            return d.toLocaleTimeString('en-IN', {
                hour: '2-digit',
                minute: '2-digit',
                hour12: false
            }) + ' IST';
        },
        formatDateLabel(dateStr) {
            const d = new Date(dateStr);
            return d.toLocaleDateString('en-IN', {
                weekday: 'short',
                day: '2-digit',
                month: 'short'
            });
        },
        viewMeetingDetail(id) {
            window.location.hash = `#/meeting/${id}`;
        }
    },
    template: `
        <div>
            <div class="flex items-center justify-between mb-8 border-b-parchment pb-4">
                <div>
                    <h2 class="text-2xl font-semibold serif-font mb-1">Corporate Meeting Calendar</h2>
                    <p class="text-xs text-muted">Weekly agenda of project checkpoints, meetings, and propagated decisions.</p>
                </div>
                <button @click="showForm = !showForm" class="btn-primary flex items-center gap-1.5 text-xs py-2">
                    {{ showForm ? 'Cancel' : 'Schedule Meeting +' }}
                </button>
            </div>

            <!-- Schedule Form Popover Drawer -->
            <div v-if="showForm" class="density-card bg-white p-6 mb-8 border border-parchment space-y-4 max-w-xl">
                <h3 class="font-serif font-bold text-base text-slate-800 border-b pb-1.5">Schedule Corporate Meeting</h3>
                
                <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div class="space-y-1">
                        <label class="small-caps">Meeting Title</label>
                        <input type="text" v-model="newMeeting.title" placeholder="Status alignment..." class="w-full border border-parchment rounded px-3 py-1.5 text-xs bg-[#faf8f4]">
                    </div>
                    <div class="space-y-1">
                        <label class="small-caps">Associated Project</label>
                        <select v-model="newMeeting.project_id" class="w-full border border-parchment rounded px-3 py-1.5 text-xs bg-[#faf8f4]">
                            <option value="">None</option>
                            <option v-for="p in projects" :key="p.project_id" :value="p.project_id">{{ p.name }}</option>
                        </select>
                    </div>
                    <div class="space-y-1">
                        <label class="small-caps">Start Date</label>
                        <input type="date" v-model="newMeeting.starts_at_date" class="w-full border border-parchment rounded px-3 py-1.5 text-xs bg-[#faf8f4]">
                    </div>
                    <div class="space-y-1">
                        <label class="small-caps">Start Time</label>
                        <input type="time" v-model="newMeeting.starts_at_time" class="w-full border border-parchment rounded px-3 py-1.5 text-xs bg-[#faf8f4]">
                    </div>
                </div>

                <div class="space-y-1.5">
                    <label class="small-caps">Select Attendees</label>
                    <div class="flex flex-wrap gap-2 pt-1">
                        <button 
                            v-for="m in teamMembers" 
                            :key="m.id"
                            @click="toggleAttendee(m.id)"
                            type="button"
                            class="px-3 py-1.5 rounded border text-xs font-medium transition"
                            :class="newMeeting.attendees.includes(m.id) ? 'bg-emerald-800 text-white border-emerald-800' : 'bg-white text-slate-700 border-parchment hover:bg-slate-50'"
                        >
                            {{ m.name }}
                        </button>
                    </div>
                </div>

                <div class="pt-4 border-t border-slate-100 text-right">
                    <button @click="scheduleMeeting" class="btn-primary py-2 px-4 text-xs">
                        Confirm Schedule
                    </button>
                </div>
            </div>

            <!-- Loading -->
            <div v-if="loading" class="density-card flex flex-col items-center justify-center p-12 text-center">
                <p class="text-sm font-semibold text-slate-600 animate-pulse">Syncing corporate schedules...</p>
            </div>

            <!-- Empty State -->
            <div v-else-if="meetings.length === 0" class="density-card flex flex-col items-center justify-center p-12 text-center">
                <p class="text-sm font-semibold text-slate-800">No scheduled meetings found.</p>
                <p class="text-xs text-slate-400 mt-1">Schedule a meeting or ask Harry to do it from chat.</p>
            </div>

            <!-- Grouped list -->
            <div v-else class="space-y-8">
                <div v-for="group in groupedMeetings" :key="group.dateStr" class="space-y-3">
                    <!-- Day label -->
                    <div class="flex items-center gap-2">
                        <span class="text-sm font-bold font-serif text-slate-800">{{ group.label }}</span>
                        <span v-if="group.isToday" class="px-2 py-0.5 text-[9px] font-bold font-mono bg-emerald-100 text-emerald-800 rounded uppercase">
                            TODAY
                        </span>
                    </div>
                    
                    <!-- Day agenda list -->
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div 
                            v-for="m in group.list" 
                            :key="m.id"
                            @click="viewMeetingDetail(m.id)"
                            class="density-card bg-white p-4 border hover:border-slate-400 cursor-pointer transition select-none flex justify-between items-start gap-4"
                        >
                            <div class="space-y-1">
                                <h4 class="font-serif font-bold text-sm text-slate-800">{{ m.title }}</h4>
                                <div class="text-[10px] text-muted font-mono flex items-center gap-1.5">
                                    <span>🕒 {{ formatTime(m.starts_at) }}</span>
                                    <span>•</span>
                                    <span>👤 {{ m.attendees.length }} attendees</span>
                                </div>
                            </div>
                            
                            <span class="px-2 py-0.5 rounded text-[9px] font-bold font-mono uppercase shrink-0" :class="{
                                'bg-blue-100 text-blue-800': m.status === 'scheduled',
                                'bg-slate-100 text-slate-600': m.status === 'completed',
                                'bg-red-100 text-red-800': m.status === 'cancelled'
                            }">
                                {{ m.status }}
                            </span>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `
};
