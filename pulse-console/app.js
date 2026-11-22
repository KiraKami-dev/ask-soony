const form = document.querySelector('#prompt-form');
const prompt = document.querySelector('#prompt');
const send = document.querySelector('#send');
const messages = document.querySelector('#messages');
const canvas = document.querySelector('#canvas');
const title = document.querySelector('#workflow-title');
const state = document.querySelector('#run-state');
const summary = document.querySelector('#run-summary');
const activityDialog = document.querySelector('#activity-dialog');
const activityTitle = document.querySelector('#activity-title');
const activityList = document.querySelector('#activity-list');
const sourceStatus = document.querySelector('#source-status');
const inbox = document.querySelector('#inbox');
const inboxCount = document.querySelector('#inbox-count');
const history = [];
const types = { source: ['☷', 'Source'], preprocess: ['⊞', 'Data index'], trigger: ['◌', 'Change trigger'], verification: ['≡', 'Verification'], impact: ['◈', 'Impact'], agent: ['✦', 'AI agent'], condition: ['◇', 'Condition'], notification: ['▣', 'Inbox'], action: ['↗', 'Action'], approval: ['✓', 'Approval'], output: ['⌁', 'Outcome'] };
const positions = [[38, 35], [300, 35], [300, 175], [38, 175], [38, 315], [300, 315], [300, 455]];
let workflow = { summary: '', nodes: [] };
let latestRun;
let inboxItems = [];
let expandedInboxId;
let capabilities = { email: false };
let watcherPoll;
let voiceContext; let voiceState = 'idle';

function setState(label, className) { state.textContent = label; state.className = `run-state ${className}`; }
function autoGrow() { prompt.style.height = 'auto'; prompt.style.height = `${Math.min(prompt.scrollHeight, 100)}px`; }
function escapeHtml(value) { const element = document.createElement('div'); element.textContent = value; return element.innerHTML; }
function compactText(value, limit = 76) { const text = String(value ?? '').replace(/\s+/g, ' ').trim(); return text.length > limit ? `${text.slice(0, limit - 1).trimEnd()}…` : text; }
function wait(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }
function outputEvidence({ includeFinding = true, run = latestRun } = {}) {
	const sources = run?.sources ?? [];
	const assessment = run?.events.filter((event) => ['agent', 'verification', 'impact'].includes(event.type)).at(-1)?.assessment;
	const visuals = Array.isArray(assessment?.visuals) ? assessment.visuals : [];
	const sourceFor = (kind) => {
		const path = visuals.find((visual) => visual?.kind === kind)?.path;
		return path ? sources.find((source) => source.path === path) : undefined;
	};
	const document = sourceFor('document');
	const csv = sourceFor('table');
	const mismatch = assessment?.mismatch;
	const finding = includeFinding && assessment?.finding ? `<p class="output-finding">${escapeHtml(assessment.finding)}</p>` : '';
	const documentVisual = document ? `<button class="output-visual document-visual" data-action="view-document" data-path="${escapeHtml(document.path)}"><code>${escapeHtml(document.path)}</code><p>${escapeHtml(document.preview || document.error || '')}</p><small>Open document ↗</small></button>` : '';
	const rows = csv?.preview?.trim().split(/\r?\n/).slice(0, 3).map((row) => row.split(',').map((cell) => cell.trim())) ?? [];
	const csvVisual = rows.length ? `<section class="output-visual csv-visual"><code>${escapeHtml(csv.path)}</code><table>${rows.map((row, rowIndex) => `<tr>${row.map((cell) => `<${rowIndex ? 'td' : 'th'} class="${mismatch?.found && [mismatch.left.value, mismatch.right.value].includes(cell) ? 'highlight' : ''}">${escapeHtml(cell)}</${rowIndex ? 'td' : 'th'}>`).join('')}</tr>`).join('')}</table></section>` : '';
	const mismatchVisual = mismatch?.found && visuals.some((visual) => visual?.kind === 'mismatch') ? `<section class="output-visual mismatch-visual"><b>Mismatch</b><p>${escapeHtml(mismatch.left.label)} <mark>${escapeHtml(mismatch.left.value)}</mark></p><p>${escapeHtml(mismatch.right.label)} <mark>${escapeHtml(mismatch.right.value)}</mark></p></section>` : '';
	const requestedVisual = documentVisual || csvVisual || mismatchVisual;
	return finding || requestedVisual ? `<div class="output-evidence">${requestedVisual || finding}</div>` : '';
}
function inboxEvidence(run, approval) {
	const selectedVisual = outputEvidence({ includeFinding: false, run });
	const assessment = run?.events.filter((event) => ['agent', 'verification', 'impact'].includes(event.type)).at(-1)?.assessment;
	const evidence = Array.isArray(approval?.evidence) && approval.evidence.length ? approval.evidence : assessment?.evidence;
	const providerEvidence = Array.isArray(evidence) && evidence.length ? `<section class="inbox-provider-evidence"><small>VERIFIED EVIDENCE</small>${evidence.map((item) => `<p><span>${escapeHtml(item.source || 'Source')}</span>${escapeHtml(item.value || '')}</p>`).join('')}</section>` : '';
	const sources = (run?.sources ?? []).filter((source) => source?.path).slice(0, 3);
	const sourceList = sources.length ? `<section class="inbox-sources"><small>LIVE SOURCES</small>${sources.map((source) => `<button data-action="view-document" data-path="${escapeHtml(source.path)}"><code>${escapeHtml(source.path)}</code><span>Open ↗</span></button>`).join('')}</section>` : '';
	return selectedVisual || providerEvidence || sourceList ? `<div class="inbox-evidence">${selectedVisual}${providerEvidence}${sourceList}</div>` : '';
}
async function responseError(response, fallback) {
	try { const body = await response.json(); return typeof body.error === 'string' ? body.error : typeof body.detail === 'string' ? body.detail : fallback; } catch { return fallback; }
}

function renderCanvas(activeIndex = -1, states = {}) {
	canvas.replaceChildren();
	const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
	svg.setAttribute('viewBox', '0 0 610 700');
	svg.innerHTML = '<defs><marker id="arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7" fill="#bdc7bd" /></marker></defs>';
	workflow.nodes.forEach((node, index) => {
		if (!index) return;
		const [fromX, fromY] = positions[index - 1]; const [toX, toY] = positions[index];
		const x1 = fromX + (toX > fromX ? 190 : 95); const y1 = fromY + (toY > fromY ? 66 : 33);
		const x2 = toX + (toX > fromX ? 0 : 95); const y2 = toY + (toY > fromY ? 0 : 33);
		const line = document.createElementNS('http://www.w3.org/2000/svg', 'path');
		line.setAttribute('d', `M ${x1} ${y1} L ${x2} ${y2}`); line.setAttribute('class', 'connection'); line.setAttribute('marker-end', 'url(#arrow)'); svg.append(line);
	});
	canvas.append(svg);
	workflow.nodes.forEach((node, index) => {
		const [x, y] = positions[index]; const [icon, label] = types[node.type] ?? types.agent;
		const card = document.createElement('div');
		card.className = `node ${node.type ?? 'agent'} ${index === activeIndex ? 'building' : ''} ${states[node.label] ?? ''}`;
		card.style.left = `${x}px`; card.style.top = `${y}px`;
		const approvalControl = '';
		const triggerEvent = latestRun?.events.filter((event) => event.node === node.label && event.type === 'trigger').at(-1);
		const triggerStatus = node.type === 'trigger' ? `<div class="trigger-status ${states[node.label] ? 'watching' : ''}"><span class="trigger-beacon"></span><span>${states[node.label] ? 'Monitoring live portal · 30s' : escapeHtml(triggerEvent?.detail || 'Will monitor source changes')}</span></div>` : '';
		const evidence = node.type === 'output' && states[node.label] === 'done' ? outputEvidence() : '';
		card.innerHTML = `<span class="node-icon">${icon}</span><span><b>${escapeHtml(node.label)}</b><small>${label}</small></span>${triggerStatus}${approvalControl}${evidence}`;
		canvas.append(card);
	});
	renderInbox();
}

function renderInbox() {
	const items = [...inboxItems];
	if (latestRun?.approval && !items.some((item) => item.id === latestRun.id)) items.unshift(latestRun);
	inboxCount.textContent = items.length ? String(items.length) : '';
	if (!items.length) {
		inbox.replaceChildren();
		return;
	}
	inbox.innerHTML = items.map((run) => {
		const approval = run.approval; const expanded = expandedInboxId === run.id;
		const finding = approval.finding || run.events.filter((event) => ['agent', 'verification', 'impact'].includes(event.type)).at(-1)?.detail || 'Review the proposed workflow action.';
		const callRunning = approval.operation === 'call' && voiceContext?.runId === run.id && ['connecting', 'live'].includes(voiceState);
		const kind = approval.operation === 'email' ? 'Email approval' : approval.operation === 'call' ? 'Spoken briefing' : 'Approval request';
		const riskLevel = ['urgent', 'attention'].includes(approval.riskLevel) ? approval.riskLevel : 'standard';
		const riskLabel = riskLevel === 'urgent' ? 'Urgent' : riskLevel === 'attention' ? 'Needs attention' : kind;
		const target = approval.recipientLabel || (approval.operation === 'email' ? approval.recipients?.[0] : '') || approval.actionNode || 'the proposed recipient';
		const actionTitle = approval.operation === 'email' ? `Email ${target}` : approval.operation === 'call' ? `Call ${target}` : `Notify ${target}`;
		const issueTitle = compactText(approval.issueTitle || actionTitle);
		const approve = approval.operation === 'email' ? approval.recipients?.length ? '<button class="inbox-action" data-action="open-email">Email</button>' : '<button class="inbox-action" disabled title="No live or requested recipient was found">No recipient available</button>' : approval.operation === 'call' ? `<button class="inbox-action" data-action="start-voice" ${callRunning ? 'disabled' : ''}>${callRunning ? 'Calling <span class="call-dots"><i>.</i><i>.</i><i>.</i></span>' : 'Call'}</button>` : '<button class="inbox-action" data-action="approve-notification">Approve</button>';
		const details = expanded ? `<p>${escapeHtml(finding)}</p>${inboxEvidence(run, approval)}<footer><div class="inbox-actions">${approve}<button class="inbox-reject" data-action="reject-notification">Reject</button></div></footer>` : '';
		return `<article class="inbox-card risk-${riskLevel} ${expanded ? 'expanded' : ''}"><button class="inbox-preview" data-action="toggle-inbox" data-run-id="${escapeHtml(run.id)}"><span class="inbox-mark">${approval.operation === 'email' ? '↗' : approval.operation === 'call' ? '◌' : '✓'}</span><span><small>${riskLabel}</small><b>${escapeHtml(issueTitle)}</b><span class="inbox-context">${escapeHtml(actionTitle)}</span></span><i>${expanded ? '−' : '+'}</i></button>${details}</article>`;
	}).join('');
}

async function refreshInbox() {
	try {
		const response = await fetch('/api/inbox');
		if (!response.ok) return;
		const data = await response.json(); inboxItems = Array.isArray(data.items) ? data.items : []; renderInbox();
	} catch { /* The current run still renders its pending notification. */ }
}

function selectWorkspace(name) {
	document.querySelector('#inbox-panel').hidden = name !== 'inbox';
	document.querySelector('#pipeline-panel').hidden = name !== 'pipeline';
	document.querySelectorAll('.workspace-tab').forEach((tab) => tab.classList.toggle('active', tab.dataset.workspace === name));
}

function appendUserMessage(text) { const item = document.querySelector('#user-message').content.cloneNode(true); item.querySelector('div').textContent = text; messages.append(item); messages.scrollTop = messages.scrollHeight; }
function appendAssistantMessage(data) { const item = document.querySelector('#assistant-message').content.cloneNode(true); item.querySelector('.assistant-copy').textContent = data.message; messages.append(item); messages.scrollTop = messages.scrollHeight; }
async function buildPipeline(data) {
	workflow = data; title.textContent = data.summary || 'Procurement workflow';
	setState('BUILDING', 'running'); summary.textContent = 'Creating workflow nodes…';
	for (let index = 0; index < workflow.nodes.length; index += 1) { renderCanvas(index); summary.textContent = `Added ${workflow.nodes[index].label}`; await wait(400); }
	renderCanvas();
}
async function runPipeline(request) {
	await loadCapabilities();
	setState('RUNNING', 'running'); summary.textContent = 'Running pipeline against Soony procurement data…';
	const response = await fetch('/api/run', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ workflow, request }) });
	if (!response.ok) throw new Error(await responseError(response, 'The pipeline could not run.'));
	latestRun = await response.json();
	const nodeStates = {};
	for (const event of latestRun.events) {
		nodeStates[event.node] = event.status === 'waiting' ? 'waiting' : 'running'; renderCanvas(-1, nodeStates); summary.textContent = `${event.status === 'waiting' ? 'Waiting for approval at' : 'Running'} ${event.node}`; await wait(500);
		if (event.status !== 'waiting') nodeStates[event.node] = 'done'; renderCanvas(-1, nodeStates);
	}
	if (latestRun.status === 'completed') { setState('COMPLETE', 'complete'); summary.textContent = 'Pipeline completed. Actions are recorded in Pulse.'; }
	else if (latestRun.status === 'waiting_for_trigger') { setState('WATCHING', 'waiting'); summary.textContent = 'Baseline stored. Waiting for a real portal data change.'; watchForChanges(latestRun.id); }
	else { setState('AWAITING REVIEW', 'waiting'); summary.textContent = 'A live notification is ready in Inbox.'; }
	void refreshInbox();
}

function statesForRun(run) {
	return Object.fromEntries(run.events.map((event) => [event.node, event.status === 'waiting' ? 'waiting' : 'done']));
}
function watchForChanges(runId) {
	clearInterval(watcherPoll);
	watcherPoll = setInterval(async () => {
		try {
			const response = await fetch(`/api/runs/${encodeURIComponent(runId)}`);
			if (!response.ok) return;
			const updated = await response.json();
			if (updated.events.length === latestRun?.events.length && updated.status === latestRun?.status) return;
			latestRun = updated; renderCanvas(-1, statesForRun(latestRun)); void refreshInbox();
			if (updated.status === 'waiting_for_trigger') { setState('WATCHING', 'waiting'); summary.textContent = 'A live change was handled. Waiting for the next portal change.'; return; }
			clearInterval(watcherPoll);
			if (updated.status === 'waiting_for_approval') { setState('AWAITING REVIEW', 'waiting'); summary.textContent = 'A live change triggered an action for review.'; }
			else { setState('COMPLETE', 'complete'); summary.textContent = 'The change-triggered run completed.'; }
		} catch { /* Keep waiting; the next poll will retry. */ }
	}, 5000);
}

function refreshVoiceCard() { if (latestRun) renderCanvas(-1, statesForRun(latestRun)); }
function endVoice(state = 'idle') { window.speechSynthesis?.cancel(); voiceState = state; refreshVoiceCard(); }
function speakableText(text) {
	return text.replace(/_/g, ' ').replace(/(\d[\d,]*)(?:\.(\d+))?%/g, (_, whole, decimal) => `${new Intl.NumberFormat('en-US').format(Number(whole.replaceAll(',', '')))}${decimal ? ` point ${decimal}` : ''} percent`).replace(/\b\d[\d,]*(?:\.\d+)?\b/g, (value) => {
		const [whole, decimal] = value.replaceAll(',', '').split('.'); const formatted = new Intl.NumberFormat('en-US').format(Number(whole));
		return decimal ? `${formatted} point ${decimal}` : formatted;
	}).replace(/\s+/g, ' ').trim();
}
async function startVoice() {
	if (!voiceContext || !window.speechSynthesis) { summary.textContent = 'Browser speech is unavailable.'; return; }
	const finding = latestRun?.events.filter((event) => ['agent', 'verification', 'impact'].includes(event.type)).at(-1)?.detail;
	if (!finding) { summary.textContent = 'No live finding is available to speak.'; return; }
	voiceState = 'connecting'; refreshVoiceCard();
	const utterance = new SpeechSynthesisUtterance(speakableText(finding));
	utterance.onstart = () => { voiceState = 'live'; refreshVoiceCard(); };
	utterance.onend = async () => {
		try {
			const response = await fetch(`/api/runs/${encodeURIComponent(voiceContext.runId)}/say-complete`, { method: 'POST' });
			if (!response.ok) throw new Error(await responseError(response, 'Could not complete the spoken briefing.'));
			const result = await response.json(); latestRun = result.run; renderCanvas(-1, statesForRun(latestRun)); void refreshInbox(); setState(latestRun.status === 'waiting_for_trigger' ? 'WATCHING' : 'COMPLETE', latestRun.status === 'waiting_for_trigger' ? 'waiting' : 'complete'); summary.textContent = result.detail; if (latestRun.status === 'waiting_for_trigger') watchForChanges(latestRun.id);
		} catch (error) { summary.textContent = error instanceof Error ? error.message : 'Could not complete the spoken briefing.'; }
		finally { voiceState = 'idle'; refreshVoiceCard(); }
	};
	utterance.onerror = () => { summary.textContent = 'The browser could not speak this briefing.'; endVoice('error'); };
	window.speechSynthesis.speak(utterance);
}

form.addEventListener('submit', async (event) => {
	event.preventDefault(); const request = prompt.value.trim(); if (!request) return;
	clearInterval(watcherPoll);
	appendUserMessage(request); prompt.value = ''; autoGrow(); send.disabled = true; setState('THINKING', 'running'); summary.textContent = 'Pulse is planning the workflow…';
	try {
		const response = await fetch('/api/agent', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ message: request, history }) });
		if (!response.ok) throw new Error(await responseError(response, 'Agent request failed')); const data = await response.json();
		latestRun = undefined; appendAssistantMessage(data); await buildPipeline(data); await runPipeline(request); history.push({ role: 'user', content: request }, { role: 'assistant', content: data.message });
	} catch (error) { setState('ERROR', 'waiting'); summary.textContent = error instanceof Error ? error.message : 'The workflow could not be created.'; }
	finally { send.disabled = false; }
});
prompt.addEventListener('input', autoGrow);
prompt.addEventListener('keydown', (event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); form.requestSubmit(); } });
document.querySelectorAll('[data-prompt]').forEach((button) => button.addEventListener('click', () => { prompt.value = button.dataset.prompt; autoGrow(); prompt.focus(); }));
document.querySelectorAll('.workspace-tab').forEach((tab) => tab.addEventListener('click', () => selectWorkspace(tab.dataset.workspace)));
document.querySelector('#activity-button').addEventListener('click', () => {
	if (!latestRun) { activityTitle.textContent = 'No workflow run yet'; activityList.innerHTML = '<div class="activity-row"><p>Ask Pulse to create a workflow. It will run automatically when the canvas is built.</p></div>'; }
	else { activityTitle.textContent = latestRun.status === 'completed' ? 'Pipeline completed' : 'Pipeline awaiting approval'; activityList.innerHTML = latestRun.events.map((event) => `<div class="activity-row"><b>${escapeHtml(event.status === 'waiting' ? 'Awaiting review' : 'Completed')} · ${escapeHtml(event.node)}</b><p>${escapeHtml(event.detail)}</p></div>`).join(''); }
	activityDialog.showModal();
});
document.querySelector('.close-dialog').addEventListener('click', () => activityDialog.close());
document.querySelector('.close-document-dialog').addEventListener('click', () => document.querySelector('#document-dialog').close());
document.querySelector('.close-email-dialog').addEventListener('click', () => document.querySelector('#email-dialog').close());
document.querySelector('#email-send').addEventListener('click', async () => {
	if (!latestRun?.approval || latestRun.approval.operation !== 'email') return;
	const button = document.querySelector('#email-send'); const recipient = document.querySelector('#email-recipient').value; const subject = document.querySelector('#email-subject').value; const body = document.querySelector('#email-body').value;
	const previousEventCount = latestRun.events.length; const approvedNode = latestRun.approval.node; button.disabled = true; button.textContent = 'Sending…';
	try {
		const response = await fetch('/api/approve', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ runId: latestRun.id, recipient, subject, body }) });
		if (!response.ok) throw new Error(await responseError(response, 'Email approval failed.'));
		const result = await response.json(); latestRun = result.run; void refreshInbox(); document.querySelector('#email-dialog').close(); const nodeStates = Object.fromEntries(latestRun.events.slice(0, previousEventCount).map((item) => [item.node, item.status === 'completed' ? 'done' : 'waiting'])); nodeStates[approvedNode] = 'done';
		for (const runEvent of latestRun.events.slice(previousEventCount)) { nodeStates[runEvent.node] = runEvent.status === 'completed' ? 'running' : 'waiting'; renderCanvas(-1, nodeStates); summary.textContent = `Running ${runEvent.node}`; await wait(420); if (runEvent.status === 'completed') { nodeStates[runEvent.node] = 'done'; renderCanvas(-1, nodeStates); } }
		if (latestRun.status === 'completed') { setState('COMPLETE', 'complete'); summary.textContent = `Email sent to ${result.action.sent.to}. Pipeline completed.`; }
		else if (latestRun.status === 'waiting_for_trigger') { setState('WATCHING', 'waiting'); summary.textContent = 'Email sent. Waiting for the next real portal change.'; watchForChanges(latestRun.id); }
		else { setState('AWAITING REVIEW', 'waiting'); summary.textContent = 'Pipeline reached another approval gate.'; }
	} catch (error) { button.disabled = false; button.textContent = 'Send email'; summary.textContent = error instanceof Error ? error.message : 'Email approval failed.'; }
});
document.addEventListener('click', async (event) => {
	if (event.target.closest('[data-action="toggle-inbox"]')) {
		const button = event.target.closest('[data-action="toggle-inbox"]'); const run = inboxItems.find((item) => item.id === button.dataset.runId) ?? (latestRun?.id === button.dataset.runId ? latestRun : undefined);
		if (!run) return;
		latestRun = run; expandedInboxId = expandedInboxId === run.id ? undefined : run.id; renderInbox();
		return;
	}
	if (event.target.closest('[data-action="view-document"]')) {
		const button = event.target.closest('[data-action="view-document"]');
		const dialog = document.querySelector('#document-dialog'); const dialogTitle = document.querySelector('#document-title'); const dialogBody = document.querySelector('#document-body');
		dialogTitle.textContent = button.dataset.path; dialogBody.textContent = 'Loading current live document…'; dialog.showModal();
		try {
			const response = await fetch(`/api/runs/${encodeURIComponent(latestRun.id)}/document?path=${encodeURIComponent(button.dataset.path)}`);
			if (!response.ok) throw new Error(await responseError(response, 'Could not load this document.'));
			const document = await response.json(); dialogBody.textContent = document.text || document.error || 'This live document is empty.';
		} catch (error) { dialogBody.textContent = error instanceof Error ? error.message : 'Could not load this document.'; }
		return;
	}
	if (event.target.closest('[data-action="start-voice"]') && latestRun?.approval?.operation === 'call') {
		voiceContext = { runId: latestRun.id, recipient: '' };
		startVoice();
		return;
	}
	if (event.target.closest('[data-action="approve-notification"]') && latestRun?.approval?.operation === 'notification') {
		const approvedNode = latestRun.approval.node;
		try {
			const response = await fetch('/api/approve', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ runId: latestRun.id }) });
			if (!response.ok) throw new Error(await responseError(response, 'Could not confirm this approval.'));
			const result = await response.json(); latestRun = result.run; void refreshInbox(); const states = Object.fromEntries(latestRun.events.map((item) => [item.node, item.status === 'waiting' ? 'waiting' : 'done'])); states[approvedNode] = 'done'; renderCanvas(-1, states);
			setState(latestRun.status === 'waiting_for_trigger' ? 'WATCHING' : 'COMPLETE', latestRun.status === 'waiting_for_trigger' ? 'waiting' : 'complete'); summary.textContent = latestRun.status === 'waiting_for_trigger' ? 'Approval confirmed. Waiting for the next data change.' : 'Approval confirmed. Pipeline completed.'; if (latestRun.status === 'waiting_for_trigger') watchForChanges(latestRun.id);
		} catch (error) { summary.textContent = error instanceof Error ? error.message : 'Could not confirm this approval.'; }
		return;
	}
	if (event.target.closest('[data-action="reject-notification"]') && latestRun?.approval) {
		const button = event.target.closest('[data-action="reject-notification"]'); button.disabled = true; button.textContent = 'Rejected';
		try {
			const response = await fetch('/api/reject', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ runId: latestRun.id }) });
			if (!response.ok) throw new Error(await responseError(response, 'Could not reject this Inbox item.'));
			const result = await response.json(); latestRun = result.run; void refreshInbox(); renderCanvas(-1, statesForRun(latestRun));
			if (latestRun.status === 'waiting_for_trigger') { setState('WATCHING', 'waiting'); summary.textContent = 'Rejected. Waiting for the next live data change.'; watchForChanges(latestRun.id); }
			else { setState('REJECTED', 'waiting'); summary.textContent = 'Rejected. No external action was taken.'; }
		} catch (error) { button.disabled = false; button.textContent = 'Reject'; summary.textContent = error instanceof Error ? error.message : 'Could not reject this Inbox item.'; }
		return;
	}
	if (event.target.closest('[data-action="open-email"]') && latestRun?.approval?.operation === 'email') {
		const approval = latestRun.approval; const recipient = document.querySelector('#email-recipient'); const selected = document.querySelector('#approval-recipient')?.value;
		recipient.innerHTML = approval.recipients.map((address) => `<option value="${escapeHtml(address)}" ${address === selected ? 'selected' : ''}>${escapeHtml(address)}</option>`).join('');
		document.querySelector('#email-subject').value = approval.subject;
		document.querySelector('#email-body').value = approval.body;
		document.querySelector('#email-dialog').showModal();
		return;
	}
	if (event.target.dataset.action !== 'execute-action' || !latestRun?.approval) return;
	const button = event.target; const isCall = latestRun.approval.operation === 'call'; const recipient = document.querySelector('#approval-recipient')?.value; const previousEventCount = latestRun.events.length; const approvedNode = latestRun.approval.node; button.disabled = true; button.textContent = isCall ? 'Generating…' : 'Sending…';
	try {
		const response = await fetch('/api/approve', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ runId: latestRun.id, recipient }) });
		if (!response.ok) throw new Error(await responseError(response, 'Email approval failed.'));
		const result = await response.json(); latestRun = result.run; const nodeStates = Object.fromEntries(latestRun.events.slice(0, previousEventCount).map((item) => [item.node, item.status === 'completed' ? 'done' : 'waiting'])); nodeStates[approvedNode] = 'done';
		for (const runEvent of latestRun.events.slice(previousEventCount)) { nodeStates[runEvent.node] = runEvent.status === 'completed' ? 'running' : 'waiting'; renderCanvas(-1, nodeStates); summary.textContent = `Running ${runEvent.node}`; await wait(420); if (runEvent.status === 'completed') { nodeStates[runEvent.node] = 'done'; renderCanvas(-1, nodeStates); } }
		if (latestRun.status === 'completed') { setState('COMPLETE', 'complete'); summary.textContent = isCall ? 'Live voice review completed.' : `Approved email sent to ${result.action.sent.to}. Pipeline completed.`; }
		else if (latestRun.status === 'waiting_for_trigger') { setState('WATCHING', 'waiting'); summary.textContent = 'Action completed. Waiting for the next real portal change.'; watchForChanges(latestRun.id); }
		else { setState('AWAITING REVIEW', 'waiting'); summary.textContent = 'Pipeline reached another approval gate.'; }
	} catch (error) { button.disabled = false; button.textContent = isCall ? 'Call' : 'Email'; summary.textContent = error instanceof Error ? error.message : 'Action failed.'; }
});
async function verifyPortalConnection() {
	try {
		const response = await fetch('/api/health'); const health = await response.json();
		if (health.portalConnected) { sourceStatus.classList.add('connected'); sourceStatus.setAttribute('aria-label', `Live portal connected: ${health.summary}`); summary.textContent = `Live source ready: ${health.summary}.`; }
		else { sourceStatus.classList.add('offline'); sourceStatus.setAttribute('aria-label', health.error || 'Live portal unavailable'); summary.textContent = health.error || 'Live portal is not available.'; }
	} catch { sourceStatus.classList.add('offline'); sourceStatus.setAttribute('aria-label', 'Live portal unavailable'); summary.textContent = 'Could not verify the live portal.'; }
}
async function loadCapabilities() {
	try { const response = await fetch('/api/capabilities'); if (response.ok) capabilities = await response.json(); } catch { capabilities = { email: false }; }
}
loadCapabilities();
verifyPortalConnection();
renderCanvas();
void refreshInbox();
