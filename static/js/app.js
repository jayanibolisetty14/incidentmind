// ==========================================
// INCIDENTMIND - FRONTEND JAVASCRIPT
// ==========================================

let currentIncident = null;
let knownIncidents = [];

function getIncidentState(incident) {
    if (!incident) {
        return {
            state: "NONE",
            status: "NONE",
            memoryStatus: "NOT_ATTEMPTED",
            isRetained: false,
            isResolved: false,
            isActive: false,
            statusLabel: "NO ACTIVE INCIDENT",
            badgeText: "ACTIVE",
            badgeClass: "muted-badge"
        };
    }

    const state = incident.state || "DETECTED";
    const status = incident.status || "ACTIVE";
    const memoryStatus = incident.memory_status || "NOT_ATTEMPTED";

    const isRetained = state === "MEMORY_RETAINED" || memoryStatus === "RETAINED";
    const isResolved = isRetained || status === "RESOLVED" || status === "CLOSED" || state === "RECOVERED" || state === "POST_MORTEM";
    const isActive = !isResolved && (status === "ACTIVE" || !status);

    let statusLabel = "ACTIVE INCIDENT";
    let badgeText = "ACTIVE";
    let badgeClass = "active-badge";

    if (isRetained) {
        statusLabel = "MEMORY RETAINED INCIDENT";
        badgeText = "✓ MEMORY RETAINED";
        badgeClass = "retained-badge";
    } else if (isResolved) {
        statusLabel = "RESOLVED INCIDENT";
        badgeText = "✓ RESOLVED";
        badgeClass = "resolved-badge";
    } else if (state === "AWAITING_APPROVAL") {
        statusLabel = "AWAITING APPROVAL";
        badgeText = "AWAITING APPROVAL";
        badgeClass = "pending-badge";
    }

    return {
        state,
        status,
        memoryStatus,
        isRetained,
        isResolved,
        isActive,
        statusLabel,
        badgeText,
        badgeClass
    };
}

function setCurrentIncident(incident) {
    currentIncident = incident;
    const info = getIncidentState(incident);

    const detectionTime = incident.created_at || incident.detection_time || "N/A";
    document.getElementById("incidentTitle").textContent = incident.title;
    document.getElementById("incidentDescription").textContent = incident.description || "No description";
    document.getElementById("incidentService").textContent = incident.service || "Unknown Service";
    document.getElementById("incidentDeployment").textContent = incident.deployment || "N/A";
    document.getElementById("incidentId").textContent = incident.incident_key || (incident.id ? `INC-${String(incident.id).padStart(3, "0")}` : "N/A");
    document.getElementById("incidentDetection").textContent = detectionTime;
    renderCurrentEvidence(incident);
    loadObservability(incident.incident_key);

    const severity = document.getElementById("incidentSeverity");
    severity.textContent = (incident.severity || "N/A").toUpperCase();
    severity.className = `severity ${String(incident.severity || "").toLowerCase()}`;

    // Incident status label
    document.getElementById("incidentStatusLabel").textContent = info.statusLabel;

    // Resolved/Retained status badge
    const resolvedBadge = document.getElementById("incidentResolvedBadge");
    if (resolvedBadge) {
        if (info.isResolved) {
            resolvedBadge.style.display = "inline-flex";
            resolvedBadge.textContent = info.badgeText;
            resolvedBadge.className = `status-badge ${info.badgeClass}`;
        } else {
            resolvedBadge.style.display = "none";
        }
    }

    // Hero Action Buttons
    const primaryHeroButton = document.getElementById("primaryHeroButton");
    if (primaryHeroButton) {
        if (info.isActive) {
            primaryHeroButton.style.display = "";
            primaryHeroButton.disabled = false;
            primaryHeroButton.textContent = "INVESTIGATE";
            primaryHeroButton.className = "primary-button recall-button";
            primaryHeroButton.title = "Investigate this incident using Hindsight memory.";
        } else {
            // For RESOLVED / MEMORY_RETAINED incidents, do NOT show INVESTIGATE
            primaryHeroButton.style.display = "none";
            primaryHeroButton.disabled = true;
        }
    }

    // Secondary Hero Action button (VIEW DETAILS) - ALWAYS visible
    const secondaryHeroButton = document.getElementById("secondaryHeroButton");
    if (secondaryHeroButton) {
        secondaryHeroButton.style.display = "";
        secondaryHeroButton.textContent = "VIEW DETAILS";
    }

    // Resolution time block
    const resolutionTimeBlock = document.getElementById("resolutionTimeBlock");
    const resolutionTimeEl = document.getElementById("incidentResolution");
    if (resolutionTimeBlock && resolutionTimeEl) {
        if (info.isResolved && incident.resolution_time) {
            resolutionTimeBlock.style.display = "";
            resolutionTimeEl.textContent = incident.resolution_time;
        } else {
            resolutionTimeBlock.style.display = "none";
        }
    }

    // Hero icon
    const heroIcon = document.getElementById("heroIcon");
    if (heroIcon) heroIcon.textContent = info.isResolved ? "✓" : "!";

    document.querySelectorAll(".success-button, .failure-button").forEach((button) => {
        button.disabled = !info.isActive;
    });

    document.getElementById("rootCause").textContent = "Waiting for investigation...";
    document.getElementById("rootCauseText").textContent = "Run Recall to identify patterns from previous engineering incidents.";
    document.getElementById("recommendedFix").textContent = "Waiting for investigation...";
    document.getElementById("recommendedFixText").textContent = "IncidentMind will recommend an action based on previous incidents.";
    document.getElementById("historicalLesson").textContent = "Waiting for investigation...";
    document.getElementById("historicalLessonText").textContent = "Previous incident outcomes will appear here.";
    document.getElementById("analysisConfidence").textContent = "Waiting for investigation...";
    document.getElementById("analysisExplanation").innerHTML = "<h3>Why this fits</h3><p>Run Recall to see why past engineering experience may be relevant.</p>";
    document.getElementById("memoryResults").innerHTML = "<div class=\"empty-memory\"><h3>Ready to investigate</h3><p>Search Hindsight for relevant engineering experience.</p></div>";
    document.getElementById("historicalComparison").textContent = "No Hindsight memory recalled.";
    document.getElementById("matchingEvidence").textContent = "--";
    document.getElementById("conflictingEvidence").textContent = "--";
    document.getElementById("historicalRelevance").textContent = "--";
    const memoryMatch = document.getElementById("memoryMatch");
    if (memoryMatch) {
        const matches = Array.isArray(incident.historical_evidence) ? incident.historical_evidence.length : 0;
        memoryMatch.textContent = matches;
    }
    const postmortemBadge = document.getElementById("postmortemBadge");
    if (postmortemBadge) {
        const hasPm = incident.postmortem && Object.keys(incident.postmortem).length > 0;
        postmortemBadge.textContent = hasPm ? "POST-MORTEM GENERATED" : "NOT GENERATED";
        postmortemBadge.className = hasPm ? "data-badge cyan-badge" : "data-badge muted-badge";
    }
    renderTimeline(incident.timeline || []);
    resetWorkflowControls();
    updateWorkflowStrip(incident.state || "DETECTED");
    hydrateWorkflow(incident);
}

function updateWorkflowStrip(state) {
    const stepMap = {
        DETECTED: 1,
        TRIAGED: 2,
        TRIAGING: 2,
        INVESTIGATING: 3,
        AWAITING_APPROVAL: 4,
        APPROVED: 4,
        RECOVERING: 5,
        RECOVERED: 6,
        POST_MORTEM: 6,
        MEMORY_RETAINED: 6,
    };
    const currentStep = stepMap[state] || 1;
    const steps = document.querySelectorAll(".workflow-strip .workflow-step");
    steps.forEach((step, index) => {
        const stepNum = index + 1;
        if (stepNum <= currentStep) {
            step.classList.add("active");
        } else {
            step.classList.remove("active");
        }
    });
}

function resetWorkflowControls() {
    ["approveActionButton", "rejectActionButton", "runRecoveryButton", "createPostmortemButton", "retainButton"].forEach((id) => {
        const button = document.getElementById(id);
        if (button) button.disabled = true;
    });
}

function setIncidentFromResponse(data) {
    if (!data || !data.incident) return;
    currentIncident = data.incident;
    setCurrentIncident(data.incident);
}

function hydrateWorkflow(incident) {
    const info = getIncidentState(incident);
    const investigation = incident.investigation || {};
    const analysis = investigation.analysis || {};
    const comparison = investigation.comparison || {};
    const evidence = investigation.evidence || [];
    const historical = incident.historical_evidence || [];
    const hasInvestigation = Object.keys(analysis).length > 0;
    const hasRecovery = incident.recovery_result && Object.keys(incident.recovery_result).length > 0;
    const hasPostmortem = incident.postmortem && Object.keys(incident.postmortem).length > 0;

    // Restore analysis panels from persisted investigation
    if (hasInvestigation) {
        generateAnalysis(analysis, evidence, comparison);
    }

    // Restore historical memory results
    if (historical.length) {
        document.getElementById("memoryResults").innerHTML = `<div class="memory-success"><h3>Historical experience retained for review</h3><p>Evidence returned by Hindsight is shown as a hypothesis.</p><div class="memory-list">${historical.map((memory, index) => renderHistoricalMemory(memory, index)).join("")}</div></div>`;
        document.getElementById("historicalComparison").textContent = historical.join("\n");
        document.getElementById("historicalRelevance").textContent = comparison.historical_relevance || "REVIEW AVAILABLE";
        const memoryMatch = document.getElementById("memoryMatch");
        if (memoryMatch) memoryMatch.textContent = historical.length;
    }
    renderComparison(comparison);

    // --- Evidence Review badge (issue #3) ---
    const evidenceReviewBadge = document.getElementById("evidenceReviewBadge");
    if (evidenceReviewBadge) {
        if (hasInvestigation || historical.length) {
            evidenceReviewBadge.textContent = "RECALL COMPLETED";
            evidenceReviewBadge.className = "data-badge cyan-badge";
        } else {
            evidenceReviewBadge.textContent = "WAITING FOR RECALL";
            evidenceReviewBadge.className = "data-badge muted-badge";
        }
    }

    // --- Human Approval badge (issue #4) ---
    const approvalBadge = document.getElementById("approvalBadge");
    if (approvalBadge) {
        const approvalState = incident.approval_status || incident.state;
        if (approvalState === "APPROVED" || approvalState === "RECOVERED" || approvalState === "POST_MORTEM" || approvalState === "MEMORY_RETAINED") {
            approvalBadge.textContent = "✓ APPROVED";
            approvalBadge.className = "approval-badge approved";
        } else if (approvalState === "REJECTED") {
            approvalBadge.textContent = "✗ REJECTED";
            approvalBadge.className = "approval-badge rejected";
        } else if (approvalState === "AWAITING_APPROVAL") {
            approvalBadge.textContent = "AWAITING REVIEW";
            approvalBadge.className = "approval-badge";
        } else {
            approvalBadge.textContent = "AWAITING REVIEW";
            approvalBadge.className = "approval-badge";
        }
    }

    // --- Recovery state (issue #6) ---
    if (hasRecovery) {
        renderRecoveryResult(incident.recovery_result);
    }

    // --- Post-mortem badge and content (issue #5) ---
    if (hasPostmortem) {
        renderPostmortemContent(incident.postmortem);
    }

    // --- Workflow button enable/disable based on persisted state ---
    if (!info.isResolved) {
        if (incident.state === "AWAITING_APPROVAL") {
            document.getElementById("approveActionButton").disabled = false;
            document.getElementById("rejectActionButton").disabled = false;
        }
        if (incident.state === "APPROVED") document.getElementById("runRecoveryButton").disabled = false;
        if (incident.state === "RECOVERED") document.getElementById("createPostmortemButton").disabled = false;
        if (incident.state === "POST_MORTEM") document.getElementById("retainButton").disabled = false;
    }
}

function renderCurrentEvidence(incident) {
    const evidenceContainer = document.getElementById("currentEvidence");
    const evidence = incident.current_evidence || incident.symptoms || incident.description;
    const items = Array.isArray(evidence) ? evidence : [evidence];
    const visibleItems = items.map((item) => String(item || "").trim()).filter(Boolean);

    evidenceContainer.innerHTML = visibleItems.length
        ? visibleItems.map((item) => `<div class="evidence-item"><span class="evidence-marker">OBSERVED</span><p>${escapeHtml(item)}</p></div>`).join("")
        : '<p class="empty-state">No current evidence has been recorded.</p>';
    document.getElementById("currentComparison").textContent = visibleItems.join("\n") || "No current evidence loaded.";
}

function selectIncident(incidentId) {
    const incident = knownIncidents.find((item) => item.id === incidentId);
    if (incident) setCurrentIncident(incident);
}

function markCurrentIncidentClosed(status) {
    const finalStatus = status || (currentIncident && (currentIncident.status || currentIncident.state)) || "RESOLVED";
    if (currentIncident) {
        currentIncident.status = finalStatus;
        setCurrentIncident(currentIncident);
        return;
    }
    const label = document.getElementById("incidentStatusLabel");
    if (label) label.textContent = `${finalStatus} INCIDENT`;
    const recallBtn = document.querySelector(".recall-button");
    if (recallBtn) recallBtn.disabled = true;
    document.querySelectorAll(".success-button, .failure-button").forEach((button) => {
        button.disabled = true;
    });
}


// ------------------------------------------
// OPEN NEW INCIDENT FORM
// ------------------------------------------

function openIncidentForm() {
    const error = document.getElementById("incidentFormError");
    if (error) error.textContent = "";
    document
        .getElementById("incidentModal")
        .classList.remove("hidden");
}


// ------------------------------------------
// CLOSE NEW INCIDENT FORM
// ------------------------------------------

function closeIncidentForm() {

    document
        .getElementById("incidentModal")
        .classList.add("hidden");
}


// ------------------------------------------
// CREATE NEW INCIDENT
// ------------------------------------------

async function createIncident() {

    const title =
        document.getElementById("newTitle").value.trim();

    const description =
        document.getElementById("newDescription").value.trim();

    const service =
        document.getElementById("newService").value.trim();

    const severity =
        document.getElementById("newSeverity").value;

    const deployment =
        document.getElementById("newDeployment").value.trim();


    const formError = document.getElementById("incidentFormError");
    const missing = [
        [title, "Title"],
        [description, "Description"],
        [service, "Affected service"],
        [deployment, "Deployment"]
    ].filter(([value]) => !value).map(([, label]) => label);
    if (missing.length) {
        if (formError) formError.textContent = `Please complete: ${missing.join(", ")}.`;
        return;
    }
    if (!severity) {
        if (formError) formError.textContent = "Please select a severity.";
        return;
    }


    // Create incident object

    const incident = {

        title: title,

        description: description,

        service: service,

        severity: severity,

        deployment: deployment
    };


    try {

        const response = await fetch(
            "/api/incidents",
            {
                method: "POST",

                headers: {
                    "Content-Type": "application/json"
                },

                body: JSON.stringify(incident)
            }
        );


        const data = await response.json().catch(() => ({}));

        console.log(
            "Current incident set:",
            currentIncident
        );


        if (!response.ok) {
            if (formError) formError.textContent = data.error || "Unable to create incident.";
            return;
        }

        setCurrentIncident(data.incident || data);
        await loadIncidentHistory();
        await loadDashboardStats();


        // Close modal

        closeIncidentForm();


        // Clear form

        document.getElementById("newTitle").value = "";

        document.getElementById("newDescription").value = "";

        document.getElementById("newService").value = "";

        document.getElementById("newDeployment").value = "";


        if (formError) formError.textContent = "";


    } catch (error) {

        console.error(error);

        alert(
            "Could not connect to IncidentMind server."
        );
    }
}


// ------------------------------------------
// INVESTIGATE WITH RECALL
// ------------------------------------------
async function investigate() {
    const title = document.getElementById("incidentTitle").textContent.trim();
    const description = document.getElementById("incidentDescription").textContent.trim();
    const incident = currentIncident || {};
    const investigationIncident = {
        id: incident.id,
        incident_key: incident.incident_key,
        title,
        description,
        service: incident.service || document.getElementById("incidentService").textContent.trim(),
        deployment: incident.deployment || document.getElementById("incidentDeployment").textContent.trim(),
        severity: incident.severity || document.getElementById("incidentSeverity").textContent.trim()
    };
    const memoryResults = document.getElementById("memoryResults");

    if (memoryResults) {
        memoryResults.innerHTML = `
            <div class="empty-memory">
                <div class="memory-icon">🧠</div>
                <h3>Searching Hindsight...</h3>
                <p>IncidentMind is comparing this incident with prior engineering experience.</p>
            </div>`;
    }

    try {
        const response = await fetch("/api/investigate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(investigationIncident)
        });
        const data = await response.json().catch(() => ({}));

        if (!response.ok) {
            throw new Error([data.error, data.reason].filter(Boolean).join(" ") || "Investigation temporarily unavailable.");
        }

        const memories = data.memories || data.previous_incidents || [];
        generateAnalysis(data.analysis || {}, data.evidence || [], data.comparison || {});
        setIncidentFromResponse(data);

        if (memoryResults) {
            memoryResults.innerHTML = memories.length
                ? `<div class="memory-success">
                    <div class="memory-icon">🧠</div>
                    <h3>${memories.length} Previous Incidents Found</h3>
                    <p>Relevant experience retrieved from Hindsight memory.</p>
                    <div class="memory-list">${memories.map((memory, index) => renderHistoricalMemory(memory, index)).join("")}</div>
                </div>`
                : `<div class="empty-memory">
                    <div class="memory-icon">🧠</div>
                    <h3>No relevant historical incident found.</h3>
                    <p>Hindsight returned no matching memory. Continue with current evidence only.</p>
                </div>`;
        }

            const historicalEvidence = (data.evidence || []).map((item) => typeof item === "string" ? item : item.text).filter(Boolean);
            document.getElementById("historicalComparison").textContent = historicalEvidence.length
                ? historicalEvidence.join("\n")
                : (memories.length ? memories[0] : "No Hindsight memory recalled.");
            document.getElementById("historicalRelevance").textContent = memories.length ? "REVIEW AVAILABLE" : "NO MATCH";
        renderComparison(data.comparison || {});

        const memoryMatch = document.getElementById("memoryMatch");
        if (memoryMatch) memoryMatch.textContent = memories.length;
        const hasRecommendation = Boolean(data.analysis && data.analysis.recommended_fix);
        document.getElementById("approveActionButton").disabled = !hasRecommendation;
        document.getElementById("rejectActionButton").disabled = !hasRecommendation;
        await loadDashboardStats();
    } catch (error) {
        console.error("Investigation error:", error);
        if (memoryResults) {
            memoryResults.innerHTML = `
                <div class="empty-memory">
                    <div class="memory-icon">⚠️</div>
                    <h3>Hindsight memory temporarily unavailable.</h3>
                    <p>${escapeHtml(error.message)}</p>
                </div>`;
        }
    }
}

window.investigate = investigate;

function escapeHtml(text) {
    const element = document.createElement("div");
    element.textContent = String(text);
    return element.innerHTML;
}

function renderHistoricalMemory(memory, index) {
    const text = String(memory || "");
    const fields = ["Historical incident", "Service", "Symptoms", "Root cause", "Historical resolution", "Resolution", "Historical result", "Lessons learned", "Lessons"];
    const details = fields.map((label) => {
        const match = text.match(new RegExp(`^${label}:\\s*(.+)$`, "im"));
        return match ? `<div><span>${escapeHtml(label)}</span><p>${escapeHtml(match[1])}</p></div>` : "";
    }).filter(Boolean).join("");
    return `<div class="memory-card"><div class="memory-number">MEMORY ${index + 1}</div>${details ? `<div class="memory-details">${details}</div>` : ""}<div class="memory-text">${escapeHtml(text)}</div></div>`;
}

function generateAnalysis(analysis, evidence, comparison) {
    document.getElementById("rootCause").textContent =
        analysis.possible_root_cause || "No historical root-cause hypothesis available.";
    document.getElementById("rootCauseText").textContent =
        "Possible pattern from Hindsight memory; validate against current telemetry.";

    document.getElementById("recommendedFix").textContent =
        analysis.recommended_fix || "No evidence-backed recommendation available.";
    document.getElementById("recommendedFixText").textContent =
        analysis.recommended_fix
            ? "Recommendation synthesized from recalled incident experience; verify before applying."
            : "No relevant historical memory supports an action. Investigate current logs and service dependencies.";

    document.getElementById("historicalLesson").textContent =
        analysis.historical_lesson || "No historical lesson available.";
    document.getElementById("historicalLessonText").textContent =
        "Based on outcomes and observations retained in the Hindsight memory bank.";
    document.getElementById("analysisConfidence").textContent = analysis.confidence || comparison.historical_relevance || "REVIEW RELEVANCE";

    const evidenceItems = evidence
        .map((item) => typeof item === "string" ? item : item.text)
        .filter(Boolean)
        .slice(0, 3);
    document.getElementById("historicalComparison").textContent = evidenceItems.length
        ? evidenceItems.join("\n")
        : "No historical evidence attached to this reflection.";
    const relevance = analysis.why_relevant ||
        "Hindsight did not return an explanation for the match.";
    const evidenceMarkup = evidenceItems.length
        ? `<ul>${evidenceItems.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`
        : "<p>No supporting memory facts were attached to this reflection.</p>";

    document.getElementById("analysisExplanation").innerHTML = `
        <h3>Why this fits</h3>
        <p>${escapeHtml(relevance)}</p>
        <p>Historical memory supports this hypothesis, but current evidence must be verified before action.</p>
        <h4>Evidence used</h4>
        ${evidenceMarkup}`;
}
// ==========================================
// DEVELOPER OUTCOME
// ==========================================

async function submitOutcome(status) {

    const title =
        document.getElementById(
            "incidentTitle"
        ).textContent;


    const description =
        document.getElementById(
            "incidentDescription"
        ).textContent;


    const notes =
        document.getElementById(
            "outcomeNotes"
        ).value.trim();


    const message =
        document.getElementById(
            "outcomeMessage"
        );


    if (!notes) {

        message.textContent =
            "Please add a short note about the outcome.";

        message.style.color = "#ffcc66";

        return;
    }


    const outcome = {

        incident_id: currentIncident ? currentIncident.id : null,

        title: title,

        description: description,

        service: currentIncident ? currentIncident.service : document.getElementById("incidentService").textContent,

        deployment: currentIncident ? currentIncident.deployment : document.getElementById("incidentDeployment").textContent,

        severity: currentIncident ? currentIncident.severity : document.getElementById("incidentSeverity").textContent,

        status: status,

        notes: notes,

        recommended_action:
            document.getElementById(
                "recommendedFix"
            ).textContent
    };

    if (!currentIncident) {
        message.textContent = "Select an active incident before recording its outcome.";
        message.style.color = "#ffcc66";
        return;
    }


    message.textContent =
        "Saving outcome to Hindsight...";

    message.style.color = "#91a4bd";


    try {

        const response = await fetch(
            "/api/outcome",
            {
                method: "POST",

                headers: {
                    "Content-Type": "application/json"
                },

                body: JSON.stringify(outcome)
            }
        );


        const data = await response.json().catch(() => ({}));


        if (!response.ok) {

            if (data.stored_locally) {
                markCurrentIncidentClosed(data.status);
                await loadIncidentHistory();
                await loadDashboardStats();
                await loadAnalytics();
            }

            message.textContent =
                data.error ||
                "Unable to save outcome.";

            message.style.color =
                "#ff647c";

            return;
        }


        message.textContent =
            status === "success"
                ? "✓ Outcome saved. IncidentMind learned that the fix worked."
                : "✓ Outcome saved. IncidentMind learned that the fix failed.";


        const incidentStatus = (data.incident && (data.incident.status || data.incident.state)) || data.status || (status === "success" ? "RESOLVED" : "REJECTED");
        markCurrentIncidentClosed(incidentStatus);


        document.getElementById(
            "outcomeNotes"
        ).value = "";

        await loadIncidentHistory();
        await loadDashboardStats();
        await loadAnalytics();


    } catch (error) {

        console.error(error);

        message.textContent =
            "Could not connect to IncidentMind server.";

        message.style.color =
            "#ff647c";
    }
}
// ==========================================
// LOAD INCIDENT HISTORY
// ==========================================

async function loadIncidentHistory() {

    try {

        const response = await fetch("/api/incidents");

        const data = await response.json();

        console.log("Incident history:", data);

        const incidents = data.incidents || [];
        knownIncidents = incidents;

        if (currentIncident) {
            const latestIncident = incidents.find((item) => item.id === currentIncident.id);
            if (latestIncident) {
                const stateChanged = latestIncident.state !== currentIncident.state;
                const statusChanged = latestIncident.status !== currentIncident.status;
                if (stateChanged || statusChanged) {
                    setCurrentIncident(latestIncident);
                } else {
                    currentIncident = latestIncident;
                }
            }
        } else {
            const activeIncident = incidents.find((item) => item.status === "ACTIVE") || incidents.find((item) => item.incident_key === "INC-019") || incidents[0];
            if (activeIncident) {
                setCurrentIncident(activeIncident);
            } else {
                currentIncident = null;
                    document.getElementById("incidentTitle").textContent = "No active incident";
                document.getElementById("incidentDescription").textContent = "Create or select an active incident to begin.";
                document.getElementById("incidentService").textContent = "N/A";
                document.getElementById("incidentDeployment").textContent = "N/A";
                document.getElementById("incidentSeverity").textContent = "N/A";
                document.getElementById("incidentId").textContent = "N/A";
                document.getElementById("incidentDetection").textContent = "N/A";
                renderCurrentEvidence({});
                document.getElementById("incidentStatusLabel").textContent = "NO ACTIVE INCIDENT";
                document.querySelector(".recall-button").disabled = true;
                document.querySelectorAll(".success-button, .failure-button").forEach((button) => {
                    button.disabled = true;
                });
            }
        }

        const historyContainer =
            document.getElementById("incidentHistory");


        if (!historyContainer) {

            console.log(
                "Incident history container not found."
            );

            return;
        }


        if (incidents.length === 0) {

            historyContainer.innerHTML = `
                <div class="empty-memory">

                    <div class="memory-icon">
                        📋
                    </div>

                    <h3>
                        No incidents yet
                    </h3>

                    <p>
                        Create your first engineering
                        incident to begin building history.
                    </p>

                </div>
            `;

            return;
        }


        const renderIncident = (item) => {
            const info = getIncidentState(item);
            return `
                <div class="history-card ${info.isResolved ? "history-resolved" : ""}">
                    <div class="history-header">
                        <h3>${escapeHtml(item.title || "Untitled Incident")}</h3>
                        <span class="history-service">${escapeHtml(item.service || "Unknown Service")}</span>
                    </div>
                    <p class="history-description">${escapeHtml(item.description || "")}</p>
                    <div class="history-details">
                        <span>Deployment: ${escapeHtml(item.deployment || "N/A")}</span>
                        <span>Severity: ${escapeHtml(item.severity || "N/A")}</span>
                        <span class="history-status ${info.badgeClass}">${escapeHtml(info.badgeText)}</span>
                        <span>${escapeHtml(item.created_at || "")}</span>
                        ${info.isActive 
                            ? `<button type="button" class="history-select" data-incident-id="${item.id}">Investigate this</button>` 
                            : `<button type="button" class="history-select secondary" data-incident-id="${item.id}">View Details</button>`}
                    </div>
                </div>
            `;
        };
        const recentIncidents = incidents.slice(0, 3);
        const olderIncidents = incidents.slice(3);

        historyContainer.innerHTML = recentIncidents.map(renderIncident).join("") +
            (olderIncidents.length
                ? `<details class="history-archive">
                    <summary>
                        <span>Show older incidents</span>
                        <span class="history-archive-count">${olderIncidents.length} more</span>
                    </summary>
                    <div class="history-archive-list">${olderIncidents.map(renderIncident).join("")}</div>
                </details>`
                : "");

        historyContainer.querySelectorAll(".history-select").forEach((button) => {
            button.addEventListener("click", () => selectIncident(Number(button.dataset.incidentId)));
        });

    }

    catch (error) {

        console.error(
            "Failed to load incident history:",
            error
        );

    }

}


// ==========================================
// PAGE LOAD
// ==========================================
// ==========================================
// LOAD DASHBOARD STATISTICS
// ==========================================

async function loadDashboardStats() {

    try {

        const response = await fetch("/api/stats");
        if (!response.ok) throw new Error("Live dashboard data is unavailable.");

        const data = await response.json();

        console.log("Dashboard statistics:", data);


        const activeCount =
            document.getElementById("activeCount");

        const resolvedCount =
            document.getElementById("resolvedCount");

        const memoryMatch =
            document.getElementById("memoryMatch");


        if (activeCount) {

            activeCount.textContent =
                String(data.active).padStart(2, "0");

        }

        if (resolvedCount) {
            resolvedCount.textContent = String(data.resolved).padStart(2, "0");
        }


        if (memoryMatch) {
            if (currentIncident && Array.isArray(currentIncident.historical_evidence) && currentIncident.historical_evidence.length) {
                memoryMatch.textContent = currentIncident.historical_evidence.length;
            } else if (currentIncident && (!currentIncident.investigation || !Object.keys(currentIncident.investigation).length)) {
                memoryMatch.textContent = 0;
            } else {
                memoryMatch.textContent = data.memory_matches !== undefined ? data.memory_matches : 0;
            }
        }

        const liveStatus = document.getElementById("liveStatus");
        const lastSyncTime = document.getElementById("lastSyncTime");
        if (liveStatus && lastSyncTime) {
            const time = new Date().toLocaleTimeString([], {
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit"
            });
            liveStatus.classList.remove("is-stale");
            document.getElementById("liveStatusLabel").textContent = "LIVE DATA";
            lastSyncTime.textContent = `UPDATED ${time}`;
        }

    }

    catch (error) {

        const liveStatus = document.getElementById("liveStatus");
        const lastSyncTime = document.getElementById("lastSyncTime");
        if (liveStatus && lastSyncTime) {
            liveStatus.classList.add("is-stale");
            document.getElementById("liveStatusLabel").textContent = "RECONNECTING";
            lastSyncTime.textContent = "DATA DELAYED";
        }

        console.error(
            "Failed to load dashboard statistics:",
            error
        );

    }
}
document.addEventListener("DOMContentLoaded", function () {

    console.log("IncidentMind application loaded.");

    const revealObserver = new IntersectionObserver((entries, observer) => {
        entries.forEach((entry) => {
            if (entry.isIntersecting) {
                entry.target.classList.add("scene-visible");
                observer.unobserve(entry.target);
            }
        });
    }, { threshold: 0.12, rootMargin: "0px 0px -5% 0px" });

    document.querySelectorAll(".main > section").forEach((section) => {
        section.classList.add("scene-reveal");
        revealObserver.observe(section);
    });

    loadIncidentHistory();

    loadDashboardStats();
    loadHindsightStatus();
    loadMemoryVault();
    loadAnalytics();

    window.setInterval(() => {
        if (document.visibilityState !== "visible") return;
        loadDashboardStats();
        loadIncidentHistory();
        loadAnalytics();
    }, 15000);

    document.querySelectorAll("[data-target]").forEach((button) => {
        button.addEventListener("click", () => {
            document.querySelectorAll(".nav-button").forEach((item) => item.classList.remove("active"));
            if (button.classList.contains("nav-button")) button.classList.add("active");
            document.querySelector(button.dataset.target).scrollIntoView({ behavior: "smooth", block: "start" });
        });
    });

    const currentTime = document.getElementById("currentTime");
    const updateClock = () => {
        if (currentTime) currentTime.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    };
    updateClock();
    window.setInterval(updateClock, 30000);

    const incidentModal = document.getElementById("incidentModal");
    incidentModal.addEventListener("click", (event) => {
        if (event.target === incidentModal) closeIncidentForm();
    });

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && !incidentModal.classList.contains("hidden")) {
            closeIncidentForm();
        }
    });

});

async function loadMemoryVault() {
    const container = document.getElementById("memoryVaultResults");
    if (!container) return;
    container.textContent = "Loading Hindsight memories...";

    try {
        const service = currentIncident && currentIncident.service ? `?service=${encodeURIComponent(currentIncident.service)}` : "";
        const response = await fetch(`/api/memories${service}`);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error([data.error, data.reason].filter(Boolean).join(" ") || "Memory Vault is temporarily unavailable.");

        const memories = data.memories || [];
        document.getElementById("vaultTotal").textContent = memories.length;
        document.getElementById("vaultSuccess").textContent = data.operations ? data.operations.successful_retains : "--";
        document.getElementById("vaultFailure").textContent = data.operations ? data.operations.failed_operations : "--";
        container.innerHTML = memories.length
            ? `<details class="vault-memory">
                <summary>
                    <span class="vault-memory-title">Show all memories</span>
                    <span class="vault-memory-count">${memories.length} memories</span>
                </summary>
                <div class="vault-memory-list">${memories.map((memory, index) => {
                const fullText = escapeHtml(memory);
                return `<article class="vault-memory-item">
                    <span>MEMORY ${index + 1}</span>
                    <p>${fullText}</p>
                </article>`;
            }).join("")}</div>
            </details>`
            : "<p>No relevant historical incident found.</p>";
    } catch (error) {
        container.textContent = `Hindsight memory temporarily unavailable. ${error.message}`;
    }
}

async function loadAnalytics() {
    try {
        const response = await fetch("/api/analytics");
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || "Analytics are temporarily unavailable.");

        document.getElementById("analyticsTotal").textContent = data.total;
        document.getElementById("analyticsResolved").textContent = data.resolved;
        document.getElementById("analyticsFailed").textContent = data.failed;
        document.getElementById("analyticsService").textContent = data.most_affected_service || "No service data";
        document.getElementById("analyticsFailure").textContent = data.most_common_failure || "No failed incidents";
        document.getElementById("analyticsMatches").textContent = data.memory_matches;
    } catch (error) {
        document.getElementById("analyticsError").textContent = error.message;
    }
}

async function recallMemory() {
    if (!currentIncident) {
        await loadDemoIncident();
    }
    await investigate();
}

async function workflowRequest(path, method = "POST") {
    const reference = currentIncident && (currentIncident.incident_key || currentIncident.id);
    if (!reference) throw new Error("Select an incident first.");
    const response = await fetch(`/api/incidents/${encodeURIComponent(reference)}/${path}`, {
        method,
        headers: { "Content-Type": "application/json" }
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || data.reason || "Workflow action failed.");
    return data;
}

async function approveAction() {
    try {
        const data = await workflowRequest("approve");
        setIncidentFromResponse(data);
        document.getElementById("runRecoveryButton").disabled = false;
        document.getElementById("approveActionButton").disabled = true;
        document.getElementById("rejectActionButton").disabled = true;
    } catch (error) {
        showWorkflowMessage(error.message);
    }
}

async function rejectAction() {
    try {
        const data = await workflowRequest("reject");
        setIncidentFromResponse(data);
        document.getElementById("approveActionButton").disabled = true;
        document.getElementById("rejectActionButton").disabled = true;
        showWorkflowMessage("Action rejected by human operator.");
    } catch (error) {
        showWorkflowMessage(error.message);
    }
}

async function runRecovery() {
    try {
        const data = await workflowRequest("recover");
        setIncidentFromResponse(data);
        renderRecoveryResult(data.recovery);
        document.getElementById("runRecoveryButton").disabled = true;
        document.getElementById("createPostmortemButton").disabled = false;
    } catch (error) {
        showWorkflowMessage(error.message);
    }
}

async function createPostmortem() {
    try {
        const data = await workflowRequest("postmortem");
        setIncidentFromResponse(data);
        renderPostmortemContent(data.postmortem);
        document.getElementById("createPostmortemButton").disabled = true;
        document.getElementById("retainButton").disabled = false;
    } catch (error) {
        showWorkflowMessage(error.message);
    }
}

async function retainKnowledge() {
    try {
        const data = await workflowRequest("retain");
        setIncidentFromResponse(data);
        showWorkflowMessage(data.message || "MEMORY UPDATED");
        document.getElementById("retainButton").disabled = true;
    } catch (error) {
        showWorkflowMessage(`MEMORY UPDATE FAILED: ${error.message}`);
    }
}

async function simulateSimilarIncident() {
    try {
        const payload = currentIncident && currentIncident.incident_key
            ? { source_incident_key: currentIncident.incident_key }
            : {};
        const response = await fetch("/api/incidents/simulate-similar", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Unable to create similar incident.");
        setCurrentIncident(data.incident);
        await loadIncidentHistory();
        showWorkflowMessage(`${data.incident.incident_key} is ready for investigation.`);
    } catch (error) {
        showWorkflowMessage(error.message);
    }
}

async function loadHindsightStatus() {
    const status = document.getElementById("hindsightStatus");
    if (!status) return;
    try {
        const response = await fetch("/api/hindsight/status");
        const data = await response.json();
        status.textContent = data.connected ? "HINDSIGHT: CONNECTED" : "HINDSIGHT: OFFLINE";
        status.title = data.error || (data.connected ? `Bank: ${data.bank_id}` : "Configure HINDSIGHT_API_URL");
    } catch (error) {
        status.textContent = "HINDSIGHT: OFFLINE";
        status.title = error.message;
    }
}

async function loadObservability(incidentKey = "INC-019") {
    try {
        const response = await fetch(`/api/demo/observability?incident_key=${encodeURIComponent(incidentKey)}`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Unable to load demo telemetry.");
        const telemetry = data.telemetry || {};
        document.getElementById("telemetrySource").textContent = telemetry.source || "DEMO / SIMULATED TELEMETRY";
        document.querySelector("#serviceHealth .health-grid").innerHTML = (telemetry.metrics || []).map((metric) => `
            <div class="metric-card"><span>${escapeHtml(metric.label)}</span><strong>${escapeHtml(metric.value)}</strong><small>${escapeHtml(telemetry.source || "DEMO / SIMULATED TELEMETRY")}</small><div class="metric-line"><i></i></div></div>`).join("");
    } catch (error) {
        console.error("Failed to load simulated observability:", error);
        const grid = document.querySelector("#serviceHealth .health-grid");
        if (grid) grid.innerHTML = `<p class="empty-state">${escapeHtml(error.message)}</p>`;
    }
}

function showWorkflowMessage(message) {
    const target = document.getElementById("outcomeMessage");
    if (target) target.textContent = message;
}

async function loadDemoIncident() {
    try {
        const response = await fetch("/api/incidents/INC-019");
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Demo incident is unavailable.");
        setCurrentIncident(data.incident);
        await loadIncidentHistory();
        document.getElementById("activeIncident").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) {
        showWorkflowMessage(error.message);
    }
}

function renderTimeline(timeline) {
    const timelineContainer = document.getElementById("incidentTimeline");
    if (!timelineContainer) return;
    const events = Array.isArray(timeline) ? timeline : [];
    timelineContainer.innerHTML = events.length
        ? events.map((item) => `<div class="timeline-item"><i></i><div><strong>${escapeHtml(item.event || "Incident event")}</strong><span>${escapeHtml(item.at || "")}</span></div></div>`).join("")
        : '<div class="timeline-item current"><i></i><div><strong>Incident detected</strong><span>Awaiting incident event</span></div></div>';
}

function renderRecoveryResult(recovery) {
    if (!recovery) return;
    const keys = Object.keys(recovery.before || {}).filter((key) => key !== "source" && Object.prototype.hasOwnProperty.call(recovery.after || {}, key));
    const formatLabel = (key) => key.replaceAll("_", " ").toUpperCase();
    document.getElementById("recoveryContent").innerHTML = `
        <div class="recovery-result"><strong>${escapeHtml(recovery.result || "RECOVERY VERIFIED")}</strong><p>${escapeHtml(recovery.label || "SIMULATED DEMO ACTION")} · DEMO METRICS ONLY</p>
        <p>${escapeHtml(recovery.action || "")}</p><div>${keys.map((key) => `<span>${escapeHtml(formatLabel(key))}: ${escapeHtml(recovery.before[key])} → ${escapeHtml(recovery.after[key])}</span>`).join("")}</div></div>`;
}

function renderPostmortemContent(postmortem) {
    if (!postmortem) return;
    const postmortemBadge = document.getElementById("postmortemBadge");
    if (postmortemBadge) {
        postmortemBadge.textContent = "POST-MORTEM GENERATED";
        postmortemBadge.className = "data-badge cyan-badge";
    }
    document.getElementById("postmortemContent").innerHTML = Object.entries({
        Impact: postmortem.impact,
        "Root Cause": postmortem.root_cause,
        Resolution: postmortem.resolution || postmortem.action_taken,
        "What Worked": postmortem.what_worked,
        "What Did Not Work": postmortem.what_did_not_work,
        "Lessons Learned": postmortem.lessons_learned,
        "Recommended Follow-up": postmortem.recommended_follow_up
    }).map(([label, value]) => `<div><span>${escapeHtml(label)}</span><p>${escapeHtml(value || "Not recorded")}</p></div>`).join("");
}

function renderComparison(comparison) {
    if (!comparison) return;
    document.getElementById("matchingEvidence").textContent = comparison.matching_signals && comparison.matching_signals.length
        ? comparison.matching_signals.join(", ")
        : "None detected";
    document.getElementById("conflictingEvidence").textContent = comparison.conflicting_evidence && comparison.conflicting_evidence.length
        ? comparison.conflicting_evidence.join(", ")
        : "None detected";
    document.getElementById("historicalRelevance").textContent = comparison.historical_relevance || "REVIEW AVAILABLE";
}

function viewIncidentDetails() {
    const target = document.getElementById("postmortem") || document.getElementById("incidentTimeline") || document.getElementById("currentEvidence");
    if (target) {
        target.scrollIntoView({ behavior: "smooth" });
    }
}
window.viewIncidentDetails = viewIncidentDetails;