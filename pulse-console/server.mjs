import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { createConnection } from 'node:net';
import { connect as connectTls } from 'node:tls';
import { extname, join, normalize } from 'node:path';

const port = Number(process.env.PORT ?? 4178);
const root = new URL('.', import.meta.url).pathname;
let envValues;
let resolvedAnthropicModel;
let availableAnthropicModels;

async function getEnvValues() {
	if (envValues) return envValues;
	try {
		const env = await readFile(join(root, '..', '.env'), 'utf8');
		envValues = Object.fromEntries(env.split(/\r?\n/).flatMap((line) => {
			const equalsIndex = line.indexOf('=');
			const whitespaceIndex = line.search(/\s/);
			const index = equalsIndex > 0 ? equalsIndex : whitespaceIndex;
			return index > 0 && !line.startsWith('#') ? [[line.slice(0, index).trim(), line.slice(index + 1).trim().replace(/^['"]|['"]$/g, '')]] : [];
		}));
	} catch { envValues = {}; }
	return envValues;
}

async function getApiKey() {
	if (process.env.ANTHROPIC_API_KEY) return process.env.ANTHROPIC_API_KEY;
	return (await getEnvValues()).ANTHROPIC_API_KEY;
}

async function listAnthropicModels(key) {
	if (availableAnthropicModels) return availableAnthropicModels;
	let response;
	try {
		response = await fetch('https://api.anthropic.com/v1/models?limit=100', { headers: { 'x-api-key': key, 'anthropic-version': '2023-06-01' }, signal: AbortSignal.timeout(15000) });
	} catch { throw new Error('Could not reach the Claude model catalog.'); }
	if (!response.ok) throw new Error(`Could not list Claude models (${response.status}).`);
	const body = await response.json();
	const models = Array.isArray(body?.data) ? body.data : [];
	availableAnthropicModels = models.map((model) => model.id).filter((id) => typeof id === 'string');
	if (!availableAnthropicModels.length) throw new Error('No Claude text model is available to this API key.');
	return availableAnthropicModels;
}

async function getAnthropicModel(key, requestedModel = '') {
	const available = await listAnthropicModels(key);
	if (requestedModel) {
		if (!available.includes(requestedModel)) throw new Error('The selected Claude model is not available to this API key.');
		return requestedModel;
	}
	if (resolvedAnthropicModel) return resolvedAnthropicModel;
	const env = await getEnvValues();
	const configured = process.env.ANTHROPIC_MODEL ?? env.ANTHROPIC_MODEL;
	if (configured) {
		if (!available.includes(configured)) throw new Error('ANTHROPIC_MODEL is not available to this API key.');
		resolvedAnthropicModel = configured;
		return configured;
	}
	const preferred = available.find((id) => id.startsWith('claude-sonnet-')) ?? available.find((id) => id.startsWith('claude-haiku-')) ?? available.find((id) => id.startsWith('claude-opus-'));
	if (!preferred) throw new Error('No Claude text model is available to this API key.');
	resolvedAnthropicModel = preferred;
	return preferred;
}

const system = `You are Pulse, an autonomous procurement operations agent for Soony, a game publisher.
You turn operational requests into cautious executable workflow drafts. The launch is Sunday 20:00; 640,000 Standard and 72,000 Collector's Editions need parts from six suppliers delivered to Hamburg.
Use the create_workflow tool. The first node must be a source and the final node must be an output. This workflow will execute immediately inside Pulse against the connected procurement dataset. Do not state any metric, risk estimate, supplier status, or data-derived finding: live data is not loaded until the pipeline runs. Use approval for financial commitments, changed banking details, or production-impacting decisions. Keep each label short.`;

const workflowTool = {
	name: 'create_workflow',
	description: 'Create a procurement workflow that Pulse can build and run.',
	input_schema: {
		type: 'object',
		additionalProperties: false,
		required: ['message', 'summary', 'confidence', 'impact', 'nodes', 'actions'],
		properties: {
			message: { type: 'string', maxLength: 280 },
			summary: { type: 'string', maxLength: 100 },
			confidence: { type: 'number', minimum: 0, maximum: 100 },
			impact: { type: 'string', enum: ['Live assessment pending'] },
			nodes: { type: 'array', minItems: 3, maxItems: 6, items: { type: 'object', additionalProperties: false, required: ['id', 'label', 'type'], properties: { id: { type: 'string', maxLength: 40 }, label: { type: 'string', maxLength: 60 }, type: { type: 'string', enum: ['source', 'agent', 'action', 'approval', 'output'] } } } },
			actions: { type: 'array', maxItems: 4, items: { type: 'string', maxLength: 120 } },
		},
	},
};

const allowedNodeTypes = new Set(['source', 'agent', 'action', 'approval', 'output']);
const runs = [];

function getValue(name, env) { return process.env[name] ?? env[name] ?? ''; }

async function getMailConfig() {
	const env = await getEnvValues();
	const address = getValue('ASK_MAIL_ADDRESS', env);
	const password = getValue('ASK_MAIL_PASSWORD', env).replace(/\s/g, '');
	const host = getValue('ASK_SMTP_HOST', env);
	if (!address || !password || !host) throw new Error('Email requires ASK_MAIL_ADDRESS, ASK_MAIL_PASSWORD, and ASK_SMTP_HOST.');
	return { address, password, host };
}

function waitForSmtpResponse(socket) {
	return new Promise((resolve, reject) => {
		let data = '';
		const timeout = setTimeout(() => done(new Error('SMTP server timed out.')), 30000);
		const onData = (chunk) => {
			data += chunk.toString('utf8');
			const lines = data.trimEnd().split(/\r?\n/);
			const last = lines.at(-1) ?? '';
			if (/^\d{3} /.test(last)) done(undefined, { code: Number(last.slice(0, 3)), text: data });
		};
		const onError = (error) => done(error);
		const done = (error, result) => { clearTimeout(timeout); socket.off('data', onData); socket.off('error', onError); error ? reject(error) : resolve(result); };
		socket.on('data', onData); socket.on('error', onError);
	});
}

function openSmtpSocket(host, secureSocket) {
	return new Promise((resolve, reject) => {
		const socket = secureSocket ? connectTls({ socket: secureSocket, servername: host }) : createConnection({ host, port: 587 });
		socket.once('error', reject);
		socket.once(secureSocket ? 'secureConnect' : 'connect', () => { socket.off('error', reject); resolve(socket); });
	});
}

async function smtpCommand(socket, command) {
	const responsePromise = waitForSmtpResponse(socket);
	socket.write(`${command}\r\n`);
	const response = await responsePromise;
	if (response.code < 200 || response.code >= 400) throw new Error(`SMTP rejected a command (${response.code}).`);
	return response;
}

async function sendEmail({ to, subject, body }) {
	const { address, password, host } = await getMailConfig();
	if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(to)) throw new Error('The selected recipient is not a valid email address.');
	let socket = await openSmtpSocket(host);
	try {
		await waitForSmtpResponse(socket); await smtpCommand(socket, 'EHLO pulse'); await smtpCommand(socket, 'STARTTLS');
		socket = await openSmtpSocket(host, socket);
		await smtpCommand(socket, 'EHLO pulse'); await smtpCommand(socket, `AUTH PLAIN ${Buffer.from(`\0${address}\0${password}`).toString('base64')}`);
		await smtpCommand(socket, `MAIL FROM:<${address}>`); await smtpCommand(socket, `RCPT TO:<${to}>`); await smtpCommand(socket, 'DATA');
		const content = `From: ${address}\r\nTo: ${to}\r\nSubject: ${subject.replace(/[\r\n]/g, ' ')}\r\nMIME-Version: 1.0\r\nContent-Type: text/plain; charset=UTF-8\r\n\r\n${body.replace(/^\./gm, '..')}\r\n.`;
		await smtpCommand(socket, content); await smtpCommand(socket, 'QUIT');
		return { to, subject };
	} finally { socket.destroy(); }
}

async function getPortalConfig() {
	const env = await getEnvValues();
	const rawUrl = process.env.SOONY_PORTAL_URL ?? env.SOONY_PORTAL_URL ?? process.env.URL ?? env.URL;
	const token = process.env.SOONY_PORTAL_TOKEN ?? env.SOONY_PORTAL_TOKEN ?? process.env.TOKEN ?? env.TOKEN;
	if (!rawUrl || !token) throw new Error('A portal URL and token are required to run a workflow.');
	const url = /^https?:\/\//i.test(rawUrl) ? rawUrl : `https://${rawUrl}`;
	return { url, token };
}

async function fetchPortalData() {
	const { url, token } = await getPortalConfig();
	let response;
	try {
		response = await fetch(`${url.replace(/\/$/, '')}/api/v1/files`, { headers: { accept: 'application/json', authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(15000) });
	} catch {
		throw new Error('The live Soony portal could not be reached. Check SOONY_PORTAL_URL and network access.');
	}
	if (!response.ok) throw new Error(`The live Soony portal rejected the request (${response.status}).`);
	let body;
	try { body = await response.json(); } catch { throw new Error('The Soony files API returned invalid JSON.'); }
	const files = Array.isArray(body) ? body : body?.files;
	if (!Array.isArray(files)) throw new Error('The Soony files API did not return a file list.');
	const relevant = files.filter((file) => typeof file?.path === 'string' && /\.(json|csv|txt|md)$/i.test(file.path) && /(erp|shipment|stock|purchase|goods|pack|supplier|contract|notice|launch)/i.test(file.path)).slice(0, 12);
	const documents = await Promise.all(relevant.map(async (file) => {
		try {
			const path = file.path.split('/').map(encodeURIComponent).join('/');
			const documentResponse = await fetch(`${url.replace(/\/$/, '')}/api/v1/files/${path}`, { headers: { authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(15000) });
			if (!documentResponse.ok) return { path: file.path, bytes: file.bytes ?? file.size ?? null, error: `HTTP ${documentResponse.status}` };
			return { path: file.path, bytes: file.bytes ?? file.size ?? null, text: (await documentResponse.text()).slice(0, 12000) };
		} catch { return { path: file.path, bytes: file.bytes ?? file.size ?? null, error: 'Download failed' }; }
	}));
	return { fileCount: files.length, documents };
}

function describePortalData(data) {
	if (data && typeof data.fileCount === 'number') return `${data.fileCount} portal files and ${data.documents.length} relevant live documents`;
	return 'live portal data';
}

function portalEmailAddresses(portalData) {
	const found = new Set();
	for (const document of portalData.documents) {
		for (const match of document.text?.matchAll(/[\w.+-]+@[\w-]+(?:\.[\w-]+)+/g) ?? []) found.add(match[0].toLowerCase());
	}
	return [...found].slice(0, 12);
}

function normalizeWorkflow(result) {
	const rawNodes = Array.isArray(result.nodes) ? result.nodes : [];
	const nodes = rawNodes.slice(0, 6).flatMap((node, index) => {
		if (!node || typeof node.label !== 'string' || !allowedNodeTypes.has(node.type)) return [];
		return [{ id: String(node.id ?? index + 1), label: node.label.slice(0, 60), type: node.type }];
	});
	if (!nodes.some((node) => node.type === 'source')) nodes.unshift({ id: 'source', label: 'Soony portal', type: 'source' });
	if (!nodes.some((node) => node.type === 'output')) {
		if (nodes.length >= 6) nodes[nodes.length - 1] = { id: 'outcome', label: 'Run outcome', type: 'output' };
		else nodes.push({ id: 'outcome', label: 'Run outcome', type: 'output' });
	}
	return { ...result, nodes: nodes.slice(0, 6) };
}

async function analyzeLivePortalData(node, request, portalData, requestedModel) {
	const key = await getApiKey();
	if (!key) throw new Error('ANTHROPIC_API_KEY is required for live agent analysis.');
	const model = await getAnthropicModel(key, requestedModel);
	const source = JSON.stringify(portalData).slice(0, 24000);
	let response;
	try {
		response = await fetch('https://api.anthropic.com/v1/messages', {
			method: 'POST',
			headers: { 'content-type': 'application/json', 'x-api-key': key, 'anthropic-version': '2023-06-01' },
			body: JSON.stringify({ model, max_tokens: 220, system: 'You are a procurement operations worker. Use only the supplied portal data. Give one concise operational finding. Do not say that you contacted anyone or changed external data.', messages: [{ role: 'user', content: `Workflow request: ${request}\nCurrent node: ${node.label}\nLive portal data: ${source}` }] }),
		});
	} catch { throw new Error(`Live analysis at ${node.label} could not reach Claude.`); }
	if (!response.ok) {
		const failure = await response.json().catch(() => undefined);
		const detail = typeof failure?.error?.message === 'string' ? failure.error.message : 'No error detail returned.';
		throw new Error(`Live analysis at ${node.label} failed (${response.status}): ${detail}`);
	}
	const result = await response.json();
	const text = result.content?.find((block) => block.type === 'text')?.text?.trim();
	if (!text) throw new Error(`Live analysis at ${node.label} returned no finding.`);
	return text;
}

async function reply(request, history, requestedModel) {
	const key = await getApiKey();
	if (!key) throw new Error('ANTHROPIC_API_KEY is required to plan a workflow.');
	const model = await getAnthropicModel(key, requestedModel);
	try {
		const response = await fetch('https://api.anthropic.com/v1/messages', {
			method: 'POST',
			headers: {
				'content-type': 'application/json',
				'x-api-key': key,
				'anthropic-version': '2023-06-01',
			},
			body: JSON.stringify({
				model,
				max_tokens: 800,
				system,
				tools: [workflowTool],
				tool_choice: { type: 'tool', name: 'create_workflow' },
				messages: [...history.slice(-6), { role: 'user', content: request }],
			}),
		});
		if (!response.ok) {
			const failure = await response.json().catch(() => undefined);
			const detail = typeof failure?.error?.message === 'string' ? failure.error.message : 'No error detail returned.';
			throw new Error(`Claude API request failed (${response.status}): ${detail}`);
		}
		const result = await response.json();
		const toolUse = result.content?.find((block) => block.type === 'tool_use' && block.name === 'create_workflow');
		const parsed = toolUse?.input;
		if (!parsed || typeof parsed.message !== 'string' || !Array.isArray(parsed.nodes)) throw new Error('Claude returned an invalid workflow plan.');
		return normalizeWorkflow(parsed);
	} catch (error) {
		throw new Error(error instanceof Error ? `Workflow planning failed: ${error.message}` : 'Workflow planning failed.');
	}
}

async function executeWorkflow(workflow, request, requestedModel) {
	const nodes = Array.isArray(workflow?.nodes) ? workflow.nodes : [];
	if (!nodes.length) throw new Error('A workflow with steps is required.');
	const portalData = await fetchPortalData();
	const events = [];
	let approval;
	for (const node of nodes) {
		if (node.type === 'source') events.push({ node: node.label, type: node.type, status: 'completed', detail: `Read ${describePortalData(portalData)} from the live Soony portal.` });
		if (node.type === 'agent') events.push({ node: node.label, type: node.type, status: 'completed', detail: await analyzeLivePortalData(node, request, portalData, requestedModel) });
		if (node.type === 'action') {
			const recipients = portalEmailAddresses(portalData);
			approval = { id: `approval_${Date.now()}`, node: node.label, recipients, subject: `Soony procurement: ${node.label}`, body: `Hello,\n\nPlease review this procurement request: ${request}\n\nPlease reply with the current status and any constraints.\n\nRegards,\nSoony Procurement` };
			events.push({ node: node.label, type: node.type, status: 'waiting', detail: recipients.length ? 'Approval is required before the prepared email is sent.' : 'Approval is required, but no recipient email was found in the live documents.' });
			break;
		}
		if (node.type === 'approval') {
			events.push({ node: node.label, type: node.type, status: 'waiting', detail: 'The pipeline stopped at this approval gate. No downstream step ran.' });
			break;
		}
		if (node.type === 'output') events.push({ node: node.label, type: node.type, status: 'completed', detail: 'Live-data run completed.' });
	}
	const hasWaitingStep = events.some((event) => event.status === 'waiting');
	const run = { id: `run_${Date.now()}`, createdAt: new Date().toISOString(), request, status: hasWaitingStep ? 'waiting_for_approval' : 'completed', events, approval, sources: portalData.documents.map((document) => ({ path: document.path, bytes: document.bytes ?? null, preview: document.text?.slice(0, 500) ?? '', error: document.error })) };
	runs.unshift(run);
	return run;
}

const types = { '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8' };
createServer(async (req, res) => {
	if (req.method === 'POST' && req.url === '/api/agent') {
		let body = '';
		for await (const chunk of req) body += chunk;
		try {
			const data = JSON.parse(body);
			const result = await reply(String(data.message ?? ''), Array.isArray(data.history) ? data.history : [], typeof data.model === 'string' ? data.model : '');
			res.writeHead(200, { 'content-type': 'application/json' });
			res.end(JSON.stringify(result));
		} catch (error) {
			res.writeHead(502, { 'content-type': 'application/json' });
			res.end(JSON.stringify({ error: error instanceof Error ? error.message : 'Workflow planning failed.' }));
		}
		return;
	}
	if (req.method === 'POST' && req.url === '/api/run') {
		let body = '';
		for await (const chunk of req) body += chunk;
		try {
			const data = JSON.parse(body);
			const run = await executeWorkflow(data.workflow, String(data.request ?? 'Procurement workflow'), typeof data.model === 'string' ? data.model : '');
			res.writeHead(200, { 'content-type': 'application/json' });
			res.end(JSON.stringify(run));
		} catch (error) {
			res.writeHead(400, { 'content-type': 'application/json' });
			res.end(JSON.stringify({ error: error instanceof Error ? error.message : 'Could not run workflow.' }));
		}
		return;
	}
	if (req.method === 'GET' && req.url === '/api/models') {
		try {
			const key = await getApiKey();
			if (!key) throw new Error('ANTHROPIC_API_KEY is required.');
			const models = await listAnthropicModels(key);
			res.writeHead(200, { 'content-type': 'application/json' });
			res.end(JSON.stringify({ models }));
		} catch (error) {
			res.writeHead(502, { 'content-type': 'application/json' });
			res.end(JSON.stringify({ error: error instanceof Error ? error.message : 'Could not load Claude models.' }));
		}
		return;
	}
	if (req.method === 'POST' && req.url === '/api/approve') {
		let body = '';
		for await (const chunk of req) body += chunk;
		try {
			const data = JSON.parse(body);
			const run = runs.find((item) => item.id === data.runId);
			if (!run?.approval || run.status !== 'waiting_for_approval') throw new Error('This approval is no longer pending.');
			const recipient = String(data.recipient ?? '').toLowerCase();
			if (!run.approval.recipients.includes(recipient)) throw new Error('Choose an email address found in the live portal documents.');
			const sent = await sendEmail({ to: recipient, subject: run.approval.subject, body: run.approval.body });
			run.status = 'completed';
			run.events.push({ node: run.approval.node, type: 'action', status: 'completed', detail: `Email sent to ${sent.to}.` });
			res.writeHead(200, { 'content-type': 'application/json' });
			res.end(JSON.stringify({ run, sent }));
		} catch (error) {
			res.writeHead(422, { 'content-type': 'application/json' });
			res.end(JSON.stringify({ error: error instanceof Error ? error.message : 'Approval failed.' }));
		}
		return;
	}
	if (req.method === 'GET' && req.url === '/api/capabilities') {
		const env = await getEnvValues();
		const emailReady = Boolean(getValue('ASK_MAIL_ADDRESS', env) && getValue('ASK_MAIL_PASSWORD', env) && getValue('ASK_SMTP_HOST', env));
		const callReady = Boolean(getValue('TWILIO_ACCOUNT_SID', env) && getValue('TWILIO_AUTH_TOKEN', env) && getValue('TWILIO_FROM_NUMBER', env) && getValue('OPENAI_API_KEY', env) && getValue('OPENAI_PROJECT_ID', env) && getValue('OPENAI_WEBHOOK_SECRET', env) && getValue('ASK_PUBLIC_URL', env));
		res.writeHead(200, { 'content-type': 'application/json' });
		res.end(JSON.stringify({ email: emailReady, calling: callReady }));
		return;
	}
	if (req.method === 'GET' && req.url === '/api/health') {
		try {
			const portalData = await fetchPortalData();
			res.writeHead(200, { 'content-type': 'application/json' });
			res.end(JSON.stringify({ portalConnected: true, summary: describePortalData(portalData) }));
		} catch (error) {
			res.writeHead(200, { 'content-type': 'application/json' });
			res.end(JSON.stringify({ portalConnected: false, error: error instanceof Error ? error.message : 'Portal unavailable.' }));
		}
		return;
	}
	const url = req.url === '/' ? '/index.html' : req.url ?? '/index.html';
	const pathname = normalize(url.split('?')[0]).replace(/^\/+/, '');
	if (pathname.includes('..')) { res.writeHead(403); res.end(); return; }
	try {
		const file = await readFile(join(root, pathname));
		res.writeHead(200, { 'content-type': types[extname(pathname)] ?? 'application/octet-stream' });
		res.end(file);
	} catch { res.writeHead(404); res.end('Not found'); }
}).listen(port, () => console.log(`Pulse is running at http://localhost:${port}`));
