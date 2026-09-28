/* Aiko's dependency-free Code-OSS integration. */
const vscode = require('vscode');
const https = require('https');
const http = require('http');
const { execFile } = require('child_process');
const { promisify } = require('util');

const TOKEN_KEY = 'daemonBearerToken';

function request(url, token, method = 'GET', body) {
  return new Promise((resolve, reject) => {
    const target = new URL(url);
    const payload = body === undefined ? undefined : Buffer.from(JSON.stringify(body));
    const transport = target.protocol === 'https:' ? https : http;
    const req = transport.request(target, { method, headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(payload ? { 'Content-Type': 'application/json', 'Content-Length': payload.length } : {})
    } }, (res) => {
      let data = '';
      res.setEncoding('utf8');
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => {
        let parsed;
        try { parsed = data ? JSON.parse(data) : {}; } catch { parsed = { raw: data }; }
        if (res.statusCode >= 200 && res.statusCode < 300) resolve(parsed);
        else reject(new Error(parsed.detail || parsed.error || `aikod returned HTTP ${res.statusCode}`));
      });
    });
    req.once('error', reject);
    if (payload) req.write(payload);
    req.end();
  });
}

function daemonUrl() { return vscode.workspace.getConfiguration('aiko').get('daemon.url', '').replace(/\/$/, ''); }
function escapeHtml(value) { return String(value).replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char])); }

class SessionItem extends vscode.TreeItem {
  constructor(session) {
    super(session.title || session.id, vscode.TreeItemCollapsibleState.None);
    this.session = session;
    this.description = `${session.provider || 'agent'} · ${session.task_state || session.session_state || 'unknown'}`;
    this.tooltip = `${session.id}\n${this.description}`;
    this.contextValue = 'aiko-session';
    this.command = { command: 'aiko.attachSession', title: 'Attach Aiko Session', arguments: [this] };
  }
}

class LocalSessionItem extends vscode.TreeItem {
  constructor(session) {
    super(session.title, vscode.TreeItemCollapsibleState.None);
    this.session = session;
    this.description = session.description;
    this.tooltip = session.tooltip;
    this.contextValue = session.attachable ? 'aiko-local-tmux' : 'aiko-local-process';
    if (session.attachable) this.command = { command: 'aiko.attachLocalSession', title: 'Attach Local Session', arguments: [this] };
  }
}

async function discoverLocalSessions() {
  const exec = promisify(execFile);
  const rows = [];
  try {
    const { stdout } = await exec('tmux', ['list-sessions', '-F', '#{session_name}|#{session_created}|#{session_attached}'], { maxBuffer: 64 * 1024 });
    for (const line of stdout.split(/\r?\n/).filter(Boolean)) {
      const [name, created, attached] = line.split('|');
      if (!name) continue;
      rows.push({ kind: 'tmux', name, title: name, attachable: true,
        description: `tmux · ${attached === '1' ? 'attached' : 'detached'}`,
        tooltip: `Local tmux session: ${name}\nCreated: ${created || 'unknown'}` });
    }
  } catch { /* tmux is optional; process discovery still works. */ }
  try {
    const { stdout } = await exec('ps', ['-axo', 'pid=,command='], { maxBuffer: 256 * 1024 });
    for (const line of stdout.split(/\r?\n/)) {
      const match = line.trim().match(/^(\d+)\s+(.+)$/);
      if (!match || !/\b(codex|hermes|opencode|agy|antigravity)\b/i.test(match[2])) continue;
      const command = match[2].replace(/(api[_-]?key|token|password|secret)=?\S+/ig, '$1=[redacted]');
      rows.push({ kind: 'process', pid: match[1], title: `PID ${match[1]}`, attachable: false,
        description: 'local agent process', tooltip: command });
    }
  } catch { /* Process discovery is best-effort on restricted hosts. */ }
  return rows;
}

class ApprovalItem extends vscode.TreeItem {
  constructor(approval) {
    super(`${approval.action}: ${approval.title || approval.task_id}`, vscode.TreeItemCollapsibleState.None);
    this.approval = approval;
    this.description = 'needs your decision';
    this.tooltip = `${approval.action}\n${approval.title || approval.task_id}\nDigest: ${approval.payload_digest}`;
    this.contextValue = 'aiko-approval';
    this.command = { command: 'aiko.decideApproval', title: 'Decide Aiko Approval', arguments: [this] };
  }
}

class SessionsProvider {
  constructor(context) { this.context = context; this.changed = new vscode.EventEmitter(); this.onDidChangeTreeData = this.changed.event; }
  refresh() { this.changed.fire(); }
  async getChildren() {
    const base = daemonUrl();
    const local = (await discoverLocalSessions()).map((session) => new LocalSessionItem(session));
    if (!base) return local.length ? local : [new vscode.TreeItem('Configure an aikod daemon, or open a local persistent agent.')];
    try {
      const response = await request(`${base}/sessions`, await this.context.secrets.get(TOKEN_KEY));
      return [...(response.sessions || []).map((session) => new SessionItem(session)), ...local];
    } catch (error) {
      const item = new vscode.TreeItem(`Aiko unavailable: ${error.message}`);
      item.command = { command: 'aiko.configureDaemon', title: 'Configure Aiko' };
      return [item];
    }
  }
}

class ApprovalsProvider {
  constructor(context) { this.context = context; this.changed = new vscode.EventEmitter(); this.onDidChangeTreeData = this.changed.event; }
  refresh() { this.changed.fire(); }
  async getChildren() {
    const base = daemonUrl();
    if (!base) return [new vscode.TreeItem('Configure an aikod daemon to receive approvals.')];
    try {
      const response = await request(`${base}/approvals`, await this.context.secrets.get(TOKEN_KEY));
      const approvals = response.approvals || [];
      return approvals.length ? approvals.map((approval) => new ApprovalItem(approval)) : [new vscode.TreeItem('No approvals waiting.')];
    } catch (error) {
      const item = new vscode.TreeItem(`Approvals unavailable: ${error.message}`);
      item.command = { command: 'aiko.configureDaemon', title: 'Configure Aiko' };
      return [item];
    }
  }
}

async function openSkillDocument(cli, name) {
  try {
    const { stdout } = await promisify(execFile)(cli, ['skills', name], { maxBuffer: 160 * 1024 });
    const document = await vscode.workspace.openTextDocument({ content: stdout, language: 'markdown' });
    await vscode.window.showTextDocument(document, { preview: true });
  } catch (error) { vscode.window.showErrorMessage(`Aiko could not read skill ${name}: ${error.message}`); }
}

function activate(context) {
  const sessions = new SessionsProvider(context);
  const approvals = new ApprovalsProvider(context);
  context.subscriptions.push(vscode.window.registerTreeDataProvider('aiko.sessions', sessions));
  context.subscriptions.push(vscode.window.registerTreeDataProvider('aiko.approvals', approvals));
  context.subscriptions.push(vscode.commands.registerCommand('aiko.refreshSessions', () => sessions.refresh()));
  context.subscriptions.push(vscode.commands.registerCommand('aiko.configureDaemon', async () => {
    const url = await vscode.window.showInputBox({ prompt: 'aikod daemon URL', value: daemonUrl(), ignoreFocusOut: true });
    if (url === undefined) return;
    const token = await vscode.window.showInputBox({ prompt: 'Bearer token (stored in SecretStorage)', password: true, ignoreFocusOut: true });
    if (token === undefined) return;
    await vscode.workspace.getConfiguration('aiko').update('daemon.url', url, vscode.ConfigurationTarget.Global);
    await context.secrets.store(TOKEN_KEY, token);
    sessions.refresh();
    approvals.refresh();
  }));
  context.subscriptions.push(vscode.commands.registerCommand('aiko.newGoal', async () => {
    const base = daemonUrl();
    if (!base) return vscode.commands.executeCommand('aiko.configureDaemon');
    const text = await vscode.window.showInputBox({ prompt: 'What should Aiko accomplish?', ignoreFocusOut: true });
    if (!text || !text.trim()) return;
    const locality = await vscode.window.showQuickPick([{ label: 'Automatic routing', value: 'auto' }, { label: 'This Mac', value: 'local' }, { label: 'Server', value: 'server' }], { placeHolder: 'Where should this goal run?' });
    if (!locality) return;
    try {
      const result = await request(`${base}/goals`, await context.secrets.get(TOKEN_KEY), 'POST', { text, locality: locality.value });
      vscode.window.showInformationMessage(`Aiko queued ${result.goal_id}.`);
      sessions.refresh();
      approvals.refresh();
    } catch (error) { vscode.window.showErrorMessage(`Aiko could not create the goal: ${error.message}`); }
  }));
  context.subscriptions.push(vscode.commands.registerCommand('aiko.openAgent', async () => {
    const folder = vscode.workspace.workspaceFolders && vscode.workspace.workspaceFolders[0];
    if (!folder) return vscode.window.showWarningMessage('Open a workspace folder before starting an agent.');
    const agent = await vscode.window.showQuickPick(['codex', 'opencode', 'hermes', 'antigravity'], { placeHolder: 'Choose a configured agent' });
    if (!agent) return;
    const configuredTargets = vscode.workspace.getConfiguration('aiko').get('agent.targets', ['local']);
    const targets = ['local', ...configuredTargets.filter((target) => typeof target === 'string' && target && target !== 'local')];
    const target = await vscode.window.showQuickPick(targets, { placeHolder: 'Choose execution target' });
    if (!target) return;
    if (target !== 'local') {
      const ok = await vscode.window.showWarningMessage('This workspace is local. Aiko will not copy it to a server. Continue only if the selected path also exists there.', { modal: true }, 'Continue');
      if (ok !== 'Continue') return;
    }
    const cli = vscode.workspace.getConfiguration('aiko').get('cli.path', 'aiko');
    const terminal = vscode.window.createTerminal({ name: `Aiko · ${agent}`, shellPath: cli, shellArgs: ['open', agent, '--repo', folder.uri.fsPath, '--target', target] });
    terminal.show();
  }));
  context.subscriptions.push(vscode.commands.registerCommand('aiko.attachSession', async (item) => {
    if (!item || !item.session) return;
    try {
      const base = daemonUrl();
      const transcript = await request(`${base}/sessions/${encodeURIComponent(item.session.id)}/transcript?tail=30000`, await context.secrets.get(TOKEN_KEY));
      const panel = vscode.window.createWebviewPanel('aikoSession', `Aiko · ${item.session.title || item.session.id}`, vscode.ViewColumn.Beside, {});
      panel.webview.html = `<!doctype html><html><body><pre>${escapeHtml(transcript.tail || '')}</pre></body></html>`;
    } catch (error) { vscode.window.showErrorMessage(`Aiko could not attach: ${error.message}`); }
  }));
  context.subscriptions.push(vscode.commands.registerCommand('aiko.attachLocalSession', async (item) => {
    if (!item || !item.session || item.session.kind !== 'tmux') return;
    const terminal = vscode.window.createTerminal({ name: `Aiko · ${item.session.name}`, shellPath: 'tmux', shellArgs: ['attach-session', '-t', item.session.name] });
    terminal.show();
  }));
  context.subscriptions.push(vscode.commands.registerCommand('aiko.messageSession', async (item) => {
    if (!item || !item.session) return;
    const text = await vscode.window.showInputBox({ prompt: `Message ${item.session.title || item.session.id}`, ignoreFocusOut: true });
    if (!text || !text.trim()) return;
    try {
      const base = daemonUrl();
      await request(`${base}/sessions/${encodeURIComponent(item.session.id)}/send`, await context.secrets.get(TOKEN_KEY), 'POST', { text });
      vscode.window.showInformationMessage('Sent to the durable Aiko worker.');
    } catch (error) { vscode.window.showErrorMessage(`Aiko could not send the message: ${error.message}`); }
  }));
  context.subscriptions.push(vscode.commands.registerCommand('aiko.decideApproval', async (item) => {
    if (!item || !item.approval) return;
    const choice = await vscode.window.showWarningMessage(`${item.approval.action}: ${item.approval.title || item.approval.task_id}`, { modal: true, detail: `Digest: ${item.approval.payload_digest}` }, 'Grant', 'Deny');
    if (!choice) return;
    try {
      const base = daemonUrl();
      await request(`${base}/approvals/${encodeURIComponent(item.approval.id)}/decision`, await context.secrets.get(TOKEN_KEY), 'POST', { decision: choice === 'Grant' ? 'granted' : 'denied' });
      vscode.window.showInformationMessage(`Approval ${choice.toLowerCase()}ed.`);
      approvals.refresh();
    } catch (error) { vscode.window.showErrorMessage(`Aiko could not record the decision: ${error.message}`); }
  }));
  context.subscriptions.push(vscode.commands.registerCommand('aiko.browseSkills', async () => {
    const cli = vscode.workspace.getConfiguration('aiko').get('cli.path', 'aiko');
    try {
      const { stdout } = await promisify(execFile)(cli, ['skills'], { maxBuffer: 64 * 1024 });
      const choices = stdout.split(/\r?\n/).filter(Boolean).map((line) => {
        const [name, ...rest] = line.split(':');
        return { label: name.trim(), description: rest.join(':').trim() };
      }).filter((entry) => entry.label);
      const selected = await vscode.window.showQuickPick(choices, { placeHolder: 'Aiko skill guidance for this workspace' });
      if (selected) await openSkillDocument(cli, selected.label);
    } catch (error) { vscode.window.showErrorMessage(`Aiko could not list skills: ${error.message}`); }
  }));
  const refreshTimer = setInterval(() => { sessions.refresh(); approvals.refresh(); }, 15_000);
  context.subscriptions.push({ dispose: () => clearInterval(refreshTimer) });
}

function deactivate() {}
module.exports = { activate, deactivate, request, escapeHtml };
