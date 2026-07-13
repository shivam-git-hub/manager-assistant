// app.js - Main Dashboard Orchestrator

import { parseHash } from './router.js';
import PortfolioView from './views/portfolio.js';
import ProjectView from './views/project.js';
import ConflictsView from './views/conflicts.js';
import WorkloadView from './views/workload.js';
import BriefsView from './views/briefs.js';
import MeetingsView from './views/meetings.js';
import MeetingDetailView from './views/meeting_detail.js';
import ChatDock from './components/chat_dock.js';

const { createApp, ref, computed, onMounted } = Vue;

createApp({
    components: {
        PortfolioView,
        ProjectView,
        ConflictsView,
        WorkloadView,
        BriefsView,
        MeetingsView,
        MeetingDetailView,
        ChatDock
    },
    setup() {
        const currentRoute = ref(parseHash());
        const portfolioData = ref([]);
        const clockState = ref({ sim_time_ist: '', epoch: 0 });
        const openConflictsCount = ref(0);
        
        const syncRoute = () => {
            currentRoute.value = parseHash();
            if (currentRoute.value.view === 'portfolio') {
                fetchPortfolio();
            }
        };
        
        const fetchPortfolio = async () => {
            try {
                const res = await fetch('api/dashboard/portfolio');
                if (res.ok) {
                    portfolioData.value = await res.json();
                }
            } catch (err) {
                console.error('Error fetching portfolio:', err);
            }
        };
        
        const fetchClock = async () => {
            try {
                const res = await fetch('api/time');
                if (res.ok) {
                    const data = await res.json();
                    clockState.value = data;
                }
            } catch (err) {
                console.error('Error syncing clock:', err);
            }
        };
        
        const fetchConflictsCount = async () => {
            try {
                const res = await fetch('api/kb/conflicts?status=open');
                if (res.ok) {
                    const list = await res.json();
                    openConflictsCount.value = list.length;
                }
            } catch (err) {
                console.error('Error fetching conflicts count:', err);
            }
        };
        
        // Formatted Live Clock display for Topbar
        const formattedClock = computed(() => {
            if (!clockState.value.sim_time_ist) return '--:--';
            
            // Format ISO datetime nicely (e.g. Mon 13 Jul, 09:42 IST)
            const d = new Date(clockState.value.sim_time_ist);
            const opt = {
                weekday: 'short',
                day: '2-digit',
                month: 'short',
                hour: '2-digit',
                minute: '2-digit',
                hour12: false
            };
            return d.toLocaleString('en-IN', opt) + ' IST';
        });
        
        onMounted(async () => {
            // Setup Routing Listeners
            window.addEventListener('hashchange', syncRoute);
            syncRoute();
            
            // Initial pollings
            await Promise.all([
                fetchClock(),
                fetchConflictsCount()
            ]);
            
            // Recurrent background syncing (every 5 seconds)
            setInterval(fetchClock, 5000);
            setInterval(fetchConflictsCount, 5000);
        });
        
        return {
            currentRoute,
            portfolioData,
            clockState,
            openConflictsCount,
            formattedClock,
            fetchPortfolio
        };
    }
}).mount('#app');
