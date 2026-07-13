// router.js - Simple Hash Router

export function parseHash() {
    const hash = window.location.hash || '#/portfolio';
    
    // Match project detail: #/project/<slug>
    const projectMatch = hash.match(/^#\/project\/([a-z0-9:-]+)$/i);
    if (projectMatch) {
        return {
            view: 'project',
            slug: projectMatch[1],
            hash
        };
    }
    
    // Standard views: portfolio, conflicts, workload, briefs
    const viewName = hash.replace(/^#\//, '');
    const validViews = ['portfolio', 'conflicts', 'workload', 'briefs'];
    const resolvedView = validViews.includes(viewName) ? viewName : 'portfolio';
    
    return {
        view: resolvedView,
        slug: null,
        hash
    };
}

export function navigateTo(hash) {
    window.location.hash = hash;
}
