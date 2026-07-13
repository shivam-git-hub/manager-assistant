// router.js - Simple Hash Router

export function parseHash() {
    const hash = window.location.hash || '#/portfolio';
    
    // Match project detail: #/project/<slug>
    const projectMatch = hash.match(/^#\/project\/([a-z0-9:-]+)$/i);
    if (projectMatch) {
        return {
            view: 'project',
            slug: projectMatch[1],
            id: null,
            hash
        };
    }

    // Match meeting detail: #/meeting/<id>
    const meetingMatch = hash.match(/^#\/meeting\/(\d+)$/i);
    if (meetingMatch) {
        return {
            view: 'meeting_detail',
            slug: null,
            id: parseInt(meetingMatch[1], 10),
            hash
        };
    }
    
    // Standard views: portfolio, conflicts, workload, briefs, meetings
    const viewName = hash.replace(/^#\//, '');
    const validViews = ['portfolio', 'conflicts', 'workload', 'briefs', 'meetings'];
    const resolvedView = validViews.includes(viewName) ? viewName : 'portfolio';
    
    return {
        view: resolvedView,
        slug: null,
        id: null,
        hash
    };
}

export function navigateTo(hash) {
    window.location.hash = hash;
}
