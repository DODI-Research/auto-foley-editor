function manageTimeBar(elemId, time) {
    if (!window.visTimelineInstances) {
        console.error(`Timeline instances collection not found`);
        return;
    }

    const timeline = window.visTimelineInstances[elemId];
    if (!timeline) {
        console.error(`Timeline instance ${elemId} not found`);
        return;
    }
    
    if (!window.customTimeBarIds) {
        window.customTimeBarIds = {};
    }
    
    try {
        timeline.setCustomTime(time, elemId);
    } catch (e) {
        timeline.addCustomTime(time, elemId);
    }
}

function setTimeBarDirect(elemId, time) {
    manageTimeBar(elemId, time);
}

function setTimeBarNormalized(elemId, start, end, normalizedPos) {
    const time = start + (end - start) * normalizedPos;
    manageTimeBar(elemId, time);
}

class VideoTimelineSync  {
    constructor(videoId, timelineId, trackLengthItemId) {
        this.timelineId = timelineId;

        try {
            const trackLengthItemData = getTimelineItemData(timelineId, trackLengthItemId);
            if (trackLengthItemData != null) {
                const trackLengthStart = trackLengthItemData.start;
                const trackLengthEnd = trackLengthItemData.end;
                this.trackLength = trackLengthEnd - trackLengthStart;
            }
        } catch (error) {
            console.error('Error setting timeline video sync:', error);
            return;
        }

        const container = document.getElementById(videoId);
        if (!container) {
            console.error('Video container not found');
            return;
        }

        this.progressElement = container.querySelector('progress');
        if (!this.progressElement) {
            console.error('Progress element not found');
            return;
        }
        
        this.setupProgressObserver();
    }
    
    setupProgressObserver() {
        // Create mutation observer to watch for value changes of the progress element
        this.observer = new MutationObserver((mutations) => {
            mutations.forEach((mutation) => {
                if (mutation.type === 'attributes' && mutation.attributeName === 'value') {
                    this.onProgressUpdate();
                }
            });
        });
        
        // Observe the progress element for value changes
        this.observer.observe(this.progressElement, {
            attributes: true,
            attributeFilter: ['value']
        });
    }
    
    onProgressUpdate() {
        const value = this.progressElement.value;
        if (value === undefined || value === null) return;
        
        // Value is already normalized (between 0 and 1)
        this.syncTimeBarToPlayback(value);
    }
    
    syncTimeBarToPlayback(normalizedPosition) {
        const timeline = window.visTimelineInstances[this.timelineId];
        if (timeline) {
            setTimeBarNormalized(this.timelineId, 0, this.trackLength, normalizedPosition);
        }
    }

    cleanup() {
        // Disconnect observer
        if (this.observer) {
            this.observer.disconnect();
            this.observer = null;
        }
    }
}

function initVideoSync(videoId, timelineId, trackLengthItemId) {
    try {
        // Initialize syncs container if it doesn't exist
        if (!window.timelineSyncs) {
            window.timelineSyncs = {};
        }

        // Cleanup existing sync if any
        if (window.timelineSyncs[timelineId]) {
            window.timelineSyncs[timelineId].cleanup();
        }
        
        // Create new sync instance
        window.timelineSyncs[timelineId] = new VideoTimelineSync(videoId, timelineId, trackLengthItemId);
    } catch (error) {
        console.error('Error initializing video sync:', error);
    }

    return null;
}

function getTimelineItemData(timelineId, itemId) {
    const timeline = window.visTimelineInstances[timelineId];
    if (!timeline) {
        console.error(`Timeline instance ${timelineId} not found`);
        return null;
    }

    const items = timeline.itemSet?.items;
    if (!items) {
        console.error('Timeline items not found');
        return null;
    }

    const item = items[itemId];
    if (!item) {
        return null;
    }

    const itemData = item.data;
    if (!itemData) {
        console.error('Track length item data not found');
        return null;
    }

    return item.data;
}

function setTimelineWindowToItemLength(timelineId, itemId) {
    const itemData = getTimelineItemData(timelineId, itemId);
    if (!itemData) {
        return;
    }

    try {
        const timeline = window.visTimelineInstances[timelineId];
        console.log(itemData.end);
        timeline.setWindow(itemData.start, new Date(itemData.end.getTime() + 20), {animation: false});
    } catch (error) {
        console.error('Error setting timeline window:', error);
    }
}
