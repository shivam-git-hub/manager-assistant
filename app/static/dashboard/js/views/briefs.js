// briefs.js - Briefs Inbox View Component

export default {
    name: 'BriefsView',
    data() {
        return {
            loading: true,
            briefs: [],
            expandedBriefId: null
        };
    },
    mounted() {
        this.fetchBriefs();
    },
    methods: {
        async fetchBriefs() {
            this.loading = true;
            try {
                const res = await fetch('api/briefs?limit=7');
                if (res.ok) {
                    this.briefs = await res.json();
                    if (this.briefs.length > 0) {
                        // Expand the newest brief by default
                        this.expandedBriefId = this.briefs[0].id;
                    }
                }
            } catch (err) {
                console.error('Error fetching briefs:', err);
            } finally {
                this.loading = false;
            }
        },
        toggleBrief(id) {
            if (this.expandedBriefId === id) {
                this.expandedBriefId = null;
            } else {
                this.expandedBriefId = id;
            }
        },
        parseMarkdown(text) {
            if (!text) return '';
            // Basic HTML escape
            let html = text
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;');
                
            // Convert Bold
            html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
            
            // Convert Bullets
            const lines = html.split('\n');
            const result = [];
            let inList = false;
            
            for (let line of lines) {
                const trimmed = line.trim();
                if (trimmed.startsWith('- ') || trimmed.startsWith('* ')) {
                    if (!inList) {
                        result.push('<ul class="list-disc pl-6 space-y-1.5 my-2">');
                        inList = true;
                    }
                    result.push(`<li>${trimmed.substring(2)}</li>`);
                } else {
                    if (inList) {
                        result.push('</ul>');
                        inList = false;
                    }
                    if (trimmed === '') {
                        result.push('<div class="h-3"></div>');
                    } else {
                        result.push(`<p class="leading-relaxed my-1">${line}</p>`);
                    }
                }
            }
            if (inList) {
                result.push('</ul>');
            }
            return result.join('\n');
        },
        formatDate(dateStr) {
            if (!dateStr) return '';
            const d = new Date(dateStr);
            return d.toLocaleDateString('en-IN', {
                weekday: 'long',
                day: '2-digit',
                month: 'long',
                year: 'numeric'
            });
        }
    },
    template: `
        <div>
            <div class="flex items-center justify-between mb-8 border-b-parchment pb-4">
                <div>
                    <h2 class="text-2xl font-semibold serif-font mb-1">Executive Morning Briefings</h2>
                    <p class="text-xs text-muted font-sans">Synthesized daily briefs compiled automatically at 09:00 IST detailing overnight activity, project health ratings, and pending conflicts.</p>
                </div>
            </div>

            <!-- Loading -->
            <div v-if="loading" class="density-card flex flex-col items-center justify-center p-12 text-center">
                <p class="text-sm font-semibold text-slate-600 animate-pulse">Retrieving historical reports...</p>
            </div>

            <!-- Empty State -->
            <div v-else-if="briefs.length === 0" class="density-card flex flex-col items-center justify-center p-12 text-center">
                <p class="text-sm font-semibold text-slate-600">No briefings found.</p>
                <p class="text-xs text-slate-400 mt-1">Briefings generate automatically when advancing sim time past 09:00 AM.</p>
            </div>

            <!-- Briefs Inbox accordion -->
            <div v-else class="space-y-4">
                <div v-for="b in briefs" :key="b.id" class="density-card p-0 overflow-hidden bg-white">
                    <!-- Title Bar -->
                    <div 
                        @click="toggleBrief(b.id)"
                        class="px-6 py-4 border-b border-slate-100 hover:bg-slate-50 flex items-center justify-between cursor-pointer transition select-none"
                    >
                        <div class="flex items-center gap-3">
                            <span class="text-xs font-mono font-bold text-emerald-800 bg-emerald-50 px-2 py-1 rounded">
                                {{ b.created_date }}
                            </span>
                            <h3 class="font-bold text-sm text-slate-800 font-serif m-0">
                                {{ formatDate(b.created_date) }}
                            </h3>
                        </div>
                        <div class="text-xs font-semibold text-slate-400 font-mono">
                            {{ expandedBriefId === b.id ? 'Collapse ▴' : 'Expand ▾' }}
                        </div>
                    </div>
                    
                    <!-- Content Block -->
                    <div 
                        v-if="expandedBriefId === b.id" 
                        class="p-6 bg-[#fafafc] border-t border-slate-50"
                    >
                        <div 
                            class="text-sm text-slate-800 leading-relaxed max-w-3xl prose font-serif"
                            v-html="parseMarkdown(b.content)"
                        ></div>
                    </div>
                </div>
            </div>
        </div>
    `
};
