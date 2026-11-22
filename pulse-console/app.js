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
const modelSelect = document.querySelector('#model-select');
const outcomes = document.querySelector('#outcomes');
const history = [];
const types = { source: ['☷', 'Source'], agent: ['✦', 'AI agent'], action: ['↗', 'Action'], approval: ['✓', 'Approval'], output: ['⌁', 'Outcome'] };
const positions = [[38, 65], [300, 65], [300, 205], [38, 205], [38, 345], [300, 345]];
let workflow = { summary: 'Launch risk monitor', nodes: [{ label: 'Soony portal', type: 'source' }, { label: 'Shipment monitor', type: 'agent' }, { label: 'Launch brief', type: 'output' }] };
let latestRun;
let capabilities = { email: false, calling: false };

function setState(label, className) { state.textContent = label; state.className = `run-state ${className}`; }
function autoGrow() { prompt.style.height = 'auto'; prompt.style.height = `${Math.min(prompt.scrollHeight, 100)}px`; }
function escapeHtml(value) { const element = document.createElement('div'); element.textContent = value; return element.innerHTML; }
function wait(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }
async function responseError(response, fallback) {
	try { const body = await response.json(); return typeof body.error === 'string' ? body.error : fallback; } catch { return fallback; }
}

function renderCanvas(activeIndex = -1, states = {}) {
	canvas.replaceChildren();
	const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
	svg.setAttribute('viewBox', '0 0 610 560');
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
		card.innerHTML = `<span class="node-icon">${icon}</span><span><b>${escapeHtml(node.label)}</b><small>${label}</small></span>`;
		canvas.append(card);
	});
}

function appendUserMessage(text) { const item = document.querySelector('#user-message').content.cloneNode(true); item.querySelector('div').textContent = text; messages.append(item); messages.scrollTop = messages.scrollHeight; }
function appendAssistantMessage(data) { const item = document.querySelector('#assistant-message').content.cloneNode(true); item.querySelector('.assistant-copy').textContent = data.message; item.querySelector('.assistant-meta').textContent = 'Live assessment starts when the pipeline runs'; messages.append(item); messages.scrollTop = messages.scrollHeight; }
async function buildPipeline(data) {
	workflow = data; title.textContent = data.summary || 'Procurement workflow';
	setState('BUILDING', 'running'); summary.textContent = 'Creating workflow nodes…';
	for (let index = 0; index < workflow.nodes.length; index += 1) { renderCanvas(index); summary.textContent = `Added ${workflow.nodes[index].label}`; await wait(400); }
	renderCanvas();
}
async function runPipeline(request) {
	setState('RUNNING', 'running'); summary.textContent = 'Running pipeline against Soony procurement data…';
	const response = await fetch('/api/run', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ workflow, request, model: modelSelect.disabled ? '' : modelSelect.value }) });
	if (!response.ok) throw new Error(await responseError(response, 'The pipeline could not run.'));
	latestRun = await response.json();
	const nodeStates = {};
	for (const event of latestRun.events) {
		nodeStates[event.node] = event.status === 'waiting' ? 'waiting' : 'running'; renderCanvas(-1, nodeStates); summary.textContent = `${event.status === 'waiting' ? 'Waiting for approval at' : 'Running'} ${event.node}`; await wait(500);
		if (event.status !== 'waiting') nodeStates[event.node] = 'done'; renderCanvas(-1, nodeStates);
	}
	if (latestRun.status === 'completed') { setState('COMPLETE', 'complete'); summary.textContent = 'Pipeline completed. Actions are recorded in Pulse.'; }
	else { setState('AWAITING REVIEW', 'waiting'); summary.textContent = 'Pipeline paused at an approval gate.'; }
	renderOutcomes(latestRun);
}

function renderOutcomes(run) {
	const sources = run.sources ?? [];
	const document = sources[0];
	const finding = run.events.find((event) => event.node && !['source', 'action', 'approval', 'output'].includes(event.type))?.detail ?? run.events.find((event) => event.detail?.length > 40)?.detail ?? 'No live analysis result was returned.';
	const documentCard = document ? `<article class="outcome-card"><h3>LIVE DOCUMENT</h3><code>${escapeHtml(document.path)}</code><p>${escapeHtml(document.preview || document.error || 'No text preview is available for this file.')}</p></article>` : '<article class="outcome-card"><h3>LIVE DOCUMENT</h3><p>No relevant text document was returned by the portal.</p></article>';
	const tableCard = `<article class="outcome-card"><h3>SOURCE TABLE</h3><table>${sources.slice(0, 4).map((source) => `<tr><td>${escapeHtml(source.path.split('/').at(-1) || source.path)}</td><td>${source.bytes ?? '—'}</td></tr>`).join('') || '<tr><td>No source files</td><td>—</td></tr>'}</table></article>`;
	const mismatchCard = `<article class="outcome-card mismatch"><h3>LIVE MISMATCH CHECK</h3><p>${escapeHtml(finding)}</p></article>`;
	let approvalCard = '';
	if (run.approval) {
		const options = run.approval.recipients.map((recipient) => `<option value="${escapeHtml(recipient)}">${escapeHtml(recipient)}</option>`).join('');
		const unavailable = !capabilities.email || !run.approval.recipients.length;
		const reason = !capabilities.email ? 'SMTP mail is not fully configured.' : !run.approval.recipients.length ? 'No supplier email was found in the live documents.' : 'Review the recipient and approve the email.';
		approvalCard = `<article class="outcome-card approval-card"><div><h3>EMAIL APPROVAL REQUIRED</h3><p>${escapeHtml(reason)}</p><p class="call-note">${capabilities.calling ? 'Calling is configured.' : 'Calling requires Twilio credentials and a public webhook URL.'}</p></div><div><select id="approval-recipient" ${unavailable ? 'disabled' : ''}>${options || '<option>No live recipient</option>'}</select><button data-action="approve-email" ${unavailable ? 'disabled' : ''}>Approve &amp; send</button></div></article>`;
	}
	outcomes.innerHTML = `${documentCard}${tableCard}${mismatchCard}${approvalCard}`;
}

form.addEventListener('submit', async (event) => {
	event.preventDefault(); const request = prompt.value.trim(); if (!request) return;
	appendUserMessage(request); prompt.value = ''; autoGrow(); send.disabled = true; setState('THINKING', 'running'); summary.textContent = 'Pulse is planning the workflow…';
	try {
		const response = await fetch('/api/agent', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ message: request, history, model: modelSelect.disabled ? '' : modelSelect.value }) });
		if (!response.ok) throw new Error(await responseError(response, 'Agent request failed')); const data = await response.json();
		appendAssistantMessage(data); await buildPipeline(data); await runPipeline(request); history.push({ role: 'user', content: request }, { role: 'assistant', content: data.message });
	} catch (error) { setState('ERROR', 'waiting'); summary.textContent = error instanceof Error ? error.message : 'The workflow could not be created.'; }
	finally { send.disabled = false; }
});
prompt.addEventListener('input', autoGrow);
prompt.addEventListener('keydown', (event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); form.requestSubmit(); } });
document.querySelectorAll('[data-prompt]').forEach((button) => button.addEventListener('click', () => { prompt.value = button.dataset.prompt; autoGrow(); prompt.focus(); }));
document.querySelector('#activity-button').addEventListener('click', () => {
	if (!latestRun) { activityTitle.textContent = 'No workflow run yet'; activityList.innerHTML = '<div class="activity-row"><p>Ask Pulse to create a workflow. It will run automatically when the canvas is built.</p></div>'; }
	else { activityTitle.textContent = latestRun.status === 'completed' ? 'Pipeline completed' : 'Pipeline awaiting approval'; activityList.innerHTML = latestRun.events.map((event) => `<div class="activity-row"><b>${escapeHtml(event.status === 'waiting' ? 'Awaiting review' : 'Completed')} · ${escapeHtml(event.node)}</b><p>${escapeHtml(event.detail)}</p></div>`).join(''); }
	activityDialog.showModal();
});
document.querySelector('.close-dialog').addEventListener('click', () => activityDialog.close());
document.addEventListener('click', async (event) => {
	if (event.target.dataset.action !== 'approve-email' || !latestRun?.approval) return;
	const button = event.target; const recipient = document.querySelector('#approval-recipient').value; button.disabled = true; button.textContent = 'Sending…';
	try {
		const response = await fetch('/api/approve', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ runId: latestRun.id, recipient }) });
		if (!response.ok) throw new Error(await responseError(response, 'Email approval failed.'));
		const result = await response.json(); latestRun = result.run; latestRun.approval = undefined; setState('SENT', 'complete'); summary.textContent = `Approved email sent to ${result.sent.to}.`; renderOutcomes(latestRun);
	} catch (error) { button.disabled = false; button.textContent = 'Approve & send'; summary.textContent = error instanceof Error ? error.message : 'Email approval failed.'; }
});
async function verifyPortalConnection() {
	try {
		const response = await fetch('/api/health'); const health = await response.json();
		if (health.portalConnected) { sourceStatus.innerHTML = '<i></i> Live portal connected'; summary.textContent = `Live source ready: ${health.summary}.`; }
		else { sourceStatus.innerHTML = '<i></i> Portal unavailable'; sourceStatus.classList.add('offline'); summary.textContent = health.error || 'Live portal is not available.'; }
	} catch { sourceStatus.innerHTML = '<i></i> Portal unavailable'; sourceStatus.classList.add('offline'); summary.textContent = 'Could not verify the live portal.'; }
}
async function loadModels() {
	try {
		const response = await fetch('/api/models');
		if (!response.ok) throw new Error(await responseError(response, 'Could not load models.'));
		const data = await response.json();
		modelSelect.replaceChildren(...data.models.map((model) => { const option = document.createElement('option'); option.value = model; option.textContent = model; return option; })); modelSelect.disabled = false;
	} catch (error) { modelSelect.replaceChildren(); const option = document.createElement('option'); option.textContent = error instanceof Error ? error.message : 'Models unavailable'; option.value = ''; modelSelect.append(option); modelSelect.disabled = true; }
}
async function loadCapabilities() {
	try { const response = await fetch('/api/capabilities'); if (response.ok) capabilities = await response.json(); } catch { capabilities = { email: false, calling: false }; }
}
loadModels();
loadCapabilities();
verifyPortalConnection();
renderCanvas();
