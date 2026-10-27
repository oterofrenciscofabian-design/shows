const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const state = { view: 'dashboard', engine: 'kokoro', imageUrl: null, videoUrl: null, currentFile: null, originalFile: '', dirty: false, socket: null, toolNames: [] };
let toastTimer;
const fileEditor = $('#fileEditor');
const editorSurface = document.createElement('div');
editorSurface.id = 'codeEditorSurface';
editorSurface.className = 'code-editor-surface';
editorSurface.setAttribute('aria-label', 'Editor de código');
fileEditor.before(editorSurface);
const aceEditor = window.ace ? window.ace.edit(editorSurface) : null;
if (aceEditor) {
  aceEditor.setTheme('ace/theme/twilight');
  aceEditor.setOptions({ fontSize: '12px', showPrintMargin: false, useWorker: false, tabSize: 2 });
  $('.editor-main').classList.add('ace-ready');
}
function editorValue() { return aceEditor ? aceEditor.getValue() : fileEditor.value; }
function setEditorValue(value) { if (aceEditor) aceEditor.setValue(value, -1); else fileEditor.value = value; }
function setEditorMode(path) {
  if (!aceEditor) return;
  const extension = path.split('.').pop().toLowerCase();
  const modes = { py: 'python', js: 'javascript', mjs: 'javascript', ts: 'typescript', html: 'html', css: 'css', json: 'json', md: 'markdown', yml: 'yaml', yaml: 'yaml', sh: 'sh', toml: 'toml', xml: 'xml', sql: 'sql', java: 'java', go: 'golang', rs: 'rust' };
  aceEditor.session.setMode(`ace/mode/${modes[extension] || 'text'}`);
}
function updateEditorDirty() {
  state.dirty = editorValue() !== state.originalFile;
  $('#editorDirty').textContent = state.dirty ? '●' : '';
}
if (aceEditor) aceEditor.session.on('change', updateEditorDirty);

function toast(message, error = false) {
  const node = $('#toast');
  node.textContent = message;
  node.classList.toggle('error', error);
  node.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove('show'), 3600);
}

async function api(path, options = {}) {
  const response = await fetch(path, { credentials: 'same-origin', ...options, headers: { ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }), ...options.headers } });
  if (response.status === 401) {
    showLogin();
    throw new Error('La sesión terminó. Vuelve a iniciar sesión.');
  }
  if (!response.ok) {
    let message = `Error ${response.status}`;
    try { message = (await response.json()).detail || message; } catch {}
    throw new Error(message);
  }
  return response;
}

const jsonPost = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body) });
const pretty = (value) => typeof value === 'string' ? value : JSON.stringify(value, null, 2);

function showLogin() {
  $('#app').hidden = true;
  $('#login').hidden = false;
  $('#password').focus();
  const telegram = window.Telegram?.WebApp;
  $('#telegramLogin').hidden = !telegram?.initData;
}

function showApp() {
  $('#login').hidden = true;
  $('#app').hidden = false;
  navigate(state.view, false);
  loadModels();
  loadMcp();
  connectStatusSocket();
  refreshGit();
  refreshSystem();
}

function navigate(view, updateHistory = true) {
  if (!$('#view-' + view)) return;
  state.view = view;
  $$('.view').forEach((section) => section.classList.toggle('active-view', section.id === `view-${view}`));
  $$('.nav-link').forEach((button) => button.classList.toggle('active', button.dataset.view === view));
  $('#currentCrumb').textContent = ({ dashboard: 'Dashboard', agent: 'Codespace Agent', chat: 'Chat', image: 'Image Studio', video: 'Video Lab', tts: 'Voice Lab', files: 'Archivos', terminal: 'Terminal', git: 'Git', mcp: 'MCP tools', system: 'Sistema' })[view] || view;
  $('#mobileNav').value = view;
  if (updateHistory) history.replaceState(null, '', '#' + view);
  if (view === 'files') refreshFiles();
  if (view === 'git') refreshGit();
  if (view === 'system') refreshSystem();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

$$('[data-view]').forEach((button) => button.addEventListener('click', () => navigate(button.dataset.view)));
$('#mobileNav').addEventListener('change', (event) => navigate(event.target.value));

$('#loginForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  $('#loginError').textContent = '';
  try {
    await jsonPost('/api/login', { password: $('#password').value });
    $('#password').value = '';
    showApp();
  } catch (error) {
    $('#loginError').textContent = error.message;
  }
});

$('#telegramLogin').addEventListener('click', async () => {
  const initData = window.Telegram?.WebApp?.initData;
  if (!initData) return;
  try { await jsonPost('/api/login/telegram', { init_data: initData }); showApp(); }
  catch (error) { $('#loginError').textContent = error.message; }
});

$('#logoutButton').addEventListener('click', async () => {
  try { await api('/api/logout', { method: 'POST' }); } catch {}
  state.socket?.close();
  showLogin();
});

async function loadModels() {
  try {
    const data = await (await api('/api/models')).json();
    const models = data.models.length ? data.models : data.suggested.map((id) => ({ id }));
    const family = (id) => /qwen/i.test(id) ? 'Qwen 2.5' : /deepseek/i.test(id) ? 'DeepSeek Coder' : /dolphin/i.test(id) ? 'Dolphin' : /mistral/i.test(id) ? 'Mistral' : /llama/i.test(id) ? 'Llama' : id;
    const makeOptions = () => models.map(({ id }) => {
      const option = document.createElement('option'); option.value = id;
      option.textContent = `${family(id)} · ${id}${data.connected ? '' : ' · CONFIGURA OLLAMA'}`;
      return option;
    });
    const select = $('#chatModel');
    select.replaceChildren(...makeOptions());
    const preferredChat = models.find(({ id }) => /qwen/i.test(id));
    if (preferredChat) select.value = preferredChat.id;
    const agentSelect = $('#agentModel');
    agentSelect.replaceChildren(...makeOptions());
    const preferredAgent = models.find(({ id }) => /deepseek/i.test(id)) || models.find(({ id }) => /dolphin/i.test(id));
    if (preferredAgent) agentSelect.value = preferredAgent.id;
    const updateHero = () => { $('#heroModel').textContent = family(select.value); };
    updateHero(); select.onchange = updateHero;
    if (!data.connected) toast('Ollama no responde. Configura OLLAMA_HOST y descarga un modelo.', true);
  } catch (error) { toast(error.message, true); }
}

function connectStatusSocket() {
  if (state.socket?.readyState === WebSocket.OPEN) return;
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  state.socket = new WebSocket(`${protocol}//${location.host}/ws`);
  state.socket.addEventListener('message', (event) => {
    const message = JSON.parse(event.data);
    if (message.type === 'bridge_status') setConnection(Boolean(message.connected));
  });
  state.socket.addEventListener('close', () => setConnection(false));
}

function setConnection(connected) {
  ['#sideDot', '#heroDot', '#mobileDot'].forEach((id) => $(id).classList.toggle('connected', connected));
  $('#sideConnection').textContent = connected ? 'Codespace conectado' : 'Sin conectar';
  $('#heroConnection').textContent = connected ? 'Listo para trabajar' : 'Esperando conexión';
  $('#heroMcp').textContent = connected ? 'Conectado · MCP' : 'Sin conexión';
  $('#mcpBadge').textContent = connected ? 'CONECTADO' : 'DESCONECTADO';
  $('#mcpBadge').classList.toggle('muted-pill', !connected);
  $('#mcpDiagramStatus').textContent = connected ? 'Conexión segura activa' : 'Esperando al Codespace';
}

$('#chatForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const input = $('#chatInput');
  const content = input.value.trim();
  if (!content) return;
  const messages = $('#chatMessages');
  if (messages.querySelector('.empty-state')) messages.replaceChildren();
  const historyMessages = [...messages.querySelectorAll('.message')].map((node) => ({ role: node.dataset.role, content: node.dataset.content }));
  appendMessage('user', content);
  input.value = '';
  const send = $('#chatForm button'); send.disabled = true;
  const model = $('#chatModel').value || 'qwen2.5:7b';
  try {
    const result = await (await jsonPost('/api/chat', { model, messages: [...historyMessages, { role: 'user', content }] })).json();
    appendMessage('assistant', result.content || '(El modelo devolvió una respuesta vacía.)', model);
  } catch (error) { appendMessage('assistant', `No se pudo completar la solicitud. ${error.message}`); }
  finally { send.disabled = false; input.focus(); }
});

function appendMessage(role, content, model = '') {
  const messages = $('#chatMessages');
  const node = document.createElement('article');
  node.className = `message ${role === 'user' ? 'user' : ''}`;
  node.dataset.role = role; node.dataset.content = content;
  const meta = document.createElement('span'); meta.className = 'message-meta'; meta.textContent = role === 'user' ? 'TÚ' : `ASISTENTE${model ? ' · ' + model : ''}`;
  const text = document.createElement('span'); text.textContent = content;
  node.append(meta, text); messages.append(node); messages.scrollTop = messages.scrollHeight;
}

$('#chatInput').addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); $('#chatForm').requestSubmit(); }
});

$('#planButton').addEventListener('click', async () => {
  const button = $('#planButton');
  button.disabled = true; button.textContent = 'Analizando workspace…';
  try {
    const plan = await (await jsonPost('/api/agent', { task: $('#agentTask').value, model: $('#agentModel').value })).json();
    renderPlan(plan);
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.innerHTML = 'Analizar y proponer <span>↗</span>'; }
});

function renderPlan(plan) {
  const panel = $('#agentResult'); panel.replaceChildren(); panel.hidden = false;
  const title = document.createElement('strong'); title.className = 'result-summary'; title.textContent = plan.summary;
  const list = document.createElement('p'); list.textContent = `${Object.keys(plan.files).length} archivo(s) · ${plan.delete.length} eliminación(es)`;
  const diff = document.createElement('pre'); diff.textContent = plan.diff || 'Sin diferencias de texto.';
  const actions = document.createElement('div'); actions.className = 'result-actions';
  const cancel = document.createElement('button'); cancel.className = 'button outline'; cancel.textContent = 'Descartar';
  cancel.addEventListener('click', () => resolvePlan(plan.id, 'cancel', cancel, actions));
  const apply = document.createElement('button'); apply.className = 'button primary'; apply.innerHTML = 'Aprobar y aplicar <span>↗</span>';
  apply.addEventListener('click', () => { if (confirm('¿Aplicar todos los cambios mostrados al Codespace?')) resolvePlan(plan.id, 'apply', apply, actions); });
  actions.append(cancel, apply); panel.append(title, list, diff, actions); panel.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

async function resolvePlan(planId, action, button, actions) {
  actions.querySelectorAll('button').forEach((item) => { item.disabled = true; });
  try {
    const result = await (await jsonPost('/api/agent', { action, plan_id: planId, task: 'confirmación' })).json();
    actions.textContent = action === 'apply' ? `Cambios aplicados: ${(result.written || []).join(', ')}` : 'Plan descartado.';
    toast(action === 'apply' ? 'Cambios aplicados al Codespace.' : 'Plan descartado.');
  } catch (error) { actions.querySelectorAll('button').forEach((item) => { item.disabled = false; }); toast(error.message, true); }
}

let imageBlob = null;
$('#imageGenerate').addEventListener('click', async () => {
  const button = $('#imageGenerate');
  if (!$('#imagePrompt').value.trim()) return toast('Escribe una descripción para la imagen.', true);
  button.disabled = true; button.textContent = 'Generando imagen…'; setMediaStatus('#imageStatus', 'GENERANDO', 'loading');
  try {
    const response = await jsonPost('/api/image', { prompt: $('#imagePrompt').value, model: $('#imageModel').value });
    imageBlob = await response.blob();
    if (state.imageUrl) URL.revokeObjectURL(state.imageUrl);
    state.imageUrl = URL.createObjectURL(imageBlob); $('#imageResult').src = state.imageUrl;
    $('#imageResult').hidden = false; $('#imageEmpty').hidden = true; $('#imageActions').hidden = false;
    setMediaStatus('#imageStatus', 'LISTA', 'ready');
  } catch (error) { setMediaStatus('#imageStatus', 'ERROR', ''); toast(error.message, true); }
  finally { button.disabled = false; button.innerHTML = 'Generar imagen <span>↗</span>'; }
});

$('#imageDownload').addEventListener('click', () => downloadBlob(imageBlob, 'neon-image.png'));
$('#imageToAgent').addEventListener('click', () => { $('#agentTask').value = `Usa la imagen generada como referencia para este proyecto: ${$('#imagePrompt').value}`; navigate('agent'); });
$('#imageSave').addEventListener('click', () => saveBlob(imageBlob, 'assets/generated-image.png', 'image/png'));

let videoBlob = null;
$('#videoGenerate').addEventListener('click', async () => {
  const button = $('#videoGenerate');
  if (!$('#videoPrompt').value.trim()) return toast('Describe la escena del vídeo.', true);
  button.disabled = true; button.textContent = 'Generando vídeo…'; setMediaStatus('#videoStatus', 'GENERANDO', 'loading');
  try {
    const response = await jsonPost('/api/video', { prompt: $('#videoPrompt').value, model: $('#videoModel').value });
    videoBlob = await response.blob();
    if (state.videoUrl) URL.revokeObjectURL(state.videoUrl);
    state.videoUrl = URL.createObjectURL(videoBlob); $('#videoResult').src = state.videoUrl;
    $('#videoResult').hidden = false; $('#videoEmpty').hidden = true; $('#videoActions').hidden = false;
    setMediaStatus('#videoStatus', 'LISTO', 'ready');
  } catch (error) { setMediaStatus('#videoStatus', 'ERROR', ''); toast(error.message, true); }
  finally { button.disabled = false; button.innerHTML = 'Generar vídeo <span>↗</span>'; }
});

$('#videoDownload').addEventListener('click', () => downloadBlob(videoBlob, 'neon-video.mp4'));
$('#videoSave').addEventListener('click', () => saveBlob(videoBlob, 'assets/generated-video.mp4', 'video/mp4'));
function setMediaStatus(selector, text, stateName) { const status = $(selector); status.textContent = text; status.className = `preview-status ${stateName}`; }
function downloadBlob(blob, filename) { if (!blob) return; const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = filename; link.click(); setTimeout(() => URL.revokeObjectURL(link.href), 500); }
async function saveBlob(blob, path, mime) {
  if (!blob) return;
  if (blob.size > 8_000_000) return toast('El archivo supera el límite de guardado de 8 MB.', true);
  try { const content = await blobToBase64(blob); await jsonPost('/api/media/save', { path, content }); toast(`Guardado en ${path}`); }
  catch (error) { toast(error.message, true); }
}
function blobToBase64(blob) { return new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(',')[1]); reader.onerror = reject; reader.readAsDataURL(blob); }); }

$$('.segment').forEach((button) => button.addEventListener('click', () => { $$('.segment').forEach((item) => item.classList.toggle('active', item === button)); state.engine = button.dataset.engine; }));
$('#ttsGenerate').addEventListener('click', async () => {
  const button = $('#ttsGenerate');
  button.disabled = true; button.textContent = 'Generando audio…';
  try {
    const response = await jsonPost('/api/tts', { text: $('#ttsText').value, engine: state.engine, format: $('#ttsFormat').value });
    const blob = await response.blob(); const url = URL.createObjectURL(blob);
    $('#ttsAudio').src = url; $('#ttsDownload').href = url; $('#ttsDownload').download = `speech.${$('#ttsFormat').value}`; $('#ttsOutput').hidden = false;
    toast('Audio generado.');
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.innerHTML = 'Generar audio <span>↗</span>'; }
});

async function refreshFiles() {
  try {
    const paths = await (await api('/api/files')).json(); const tree = $('#fileTree'); tree.replaceChildren();
    for (const path of paths) { const button = document.createElement('button'); button.textContent = `⌑  ${path}`; button.title = path; button.classList.toggle('selected', path === state.currentFile); button.addEventListener('click', () => openFile(path)); tree.append(button); }
    if (!paths.length) tree.textContent = 'El workspace está vacío.';
  } catch (error) { $('#fileTree').textContent = error.message; }
}
async function openFile(path) {
  if (state.dirty && !confirm('Hay cambios sin guardar. ¿Descartarlos?')) return;
  try {
    const data = await (await api(`/api/files/read?path=${encodeURIComponent(path)}`)).json();
    state.currentFile = data.path; state.originalFile = data.content; state.dirty = false;
    $('#editorPath').textContent = data.path; setEditorValue(data.content); setEditorMode(data.path); $('#fileEditor').disabled = false; aceEditor?.setReadOnly(false);
    $('#saveFile').disabled = false; $('#deleteFile').disabled = false; $('#editorDirty').textContent = '';
    $$('#fileTree button').forEach((item) => item.classList.toggle('selected', item.title === path));
  } catch (error) { toast(error.message, true); }
}
$('#fileEditor').addEventListener('input', updateEditorDirty);
$('#saveFile').addEventListener('click', async () => {
  try { const content = editorValue(); await jsonPost('/api/files/write', { path: state.currentFile, content }); state.originalFile = content; state.dirty = false; $('#editorDirty').textContent = ''; toast('Archivo guardado.'); }
  catch (error) { toast(error.message, true); }
});
$('#newFile').addEventListener('click', async () => {
  const path = prompt('Ruta relativa del nuevo archivo:');
  if (!path) return;
  try { await jsonPost('/api/files/write', { path, content: '' }); await refreshFiles(); await openFile(path); }
  catch (error) { toast(error.message, true); }
});
$('#deleteFile').addEventListener('click', async () => {
  if (!state.currentFile || !confirm(`¿Eliminar ${state.currentFile}?`)) return;
  try { await api(`/api/files?path=${encodeURIComponent(state.currentFile)}`, { method: 'DELETE' }); state.currentFile = null; state.dirty = false; state.originalFile = ''; setEditorValue(''); aceEditor?.setReadOnly(true); $('#fileEditor').value = ''; $('#fileEditor').disabled = true; $('#editorPath').textContent = 'Sin archivo abierto'; $('#deleteFile').disabled = true; $('#saveFile').disabled = true; await refreshFiles(); toast('Archivo eliminado.'); }
  catch (error) { toast(error.message, true); }
});
$('#refreshFiles').addEventListener('click', refreshFiles);

$('#terminalForm').addEventListener('submit', async (event) => {
  event.preventDefault(); const input = $('#terminalInput'); const command = input.value.trim(); if (!command) return;
  input.value = ''; appendTerminal(command, null); const button = $('#terminalForm button'); button.disabled = true;
  try { const result = await (await jsonPost('/api/terminal', { command })).json(); appendTerminal('', result, result.returncode !== 0); }
  catch (error) { appendTerminal('', error.message, true); }
  finally { button.disabled = false; input.focus(); }
});
function appendTerminal(command, result, failed = false) {
  const row = document.createElement('div'); row.className = `terminal-line${failed ? ' error' : ''}`;
  if (command) { const prefix = document.createElement('span'); prefix.className = 'line-prefix'; prefix.textContent = '$_ '; row.append(prefix, document.createTextNode(command)); }
  if (result !== null) { const output = document.createElement('pre'); output.textContent = typeof result === 'string' ? result : [result.stdout, result.stderr].filter(Boolean).join('\n') || `Proceso finalizado (${result.returncode ?? 0}).`; row.append(output); }
  $('#terminalOutput').append(row); $('#terminalOutput').scrollTop = $('#terminalOutput').scrollHeight;
}
$('#clearTerminal').addEventListener('click', () => $('#terminalOutput').replaceChildren());

async function refreshGit() {
  try { const result = await (await api('/api/git')).json(); $('#gitOutput').textContent = [result.stdout, result.stderr].filter(Boolean).join('\n') || 'Working tree clean.'; $('#gitState').textContent = result.returncode === 0 ? 'DISPONIBLE' : 'REVISAR'; }
  catch (error) { $('#gitOutput').textContent = error.message; $('#gitState').textContent = 'DESCONECTADO'; }
}
$('#refreshGit').addEventListener('click', refreshGit);
$('#commitButton').addEventListener('click', async () => {
  const message = $('#commitMessage').value.trim(); if (!message) return toast('Escribe un mensaje de commit.', true);
  if (!confirm(`¿Crear commit: “${message}”?`)) return;
  try { const result = await (await jsonPost('/api/git', { action: 'commit', message })).json(); $('#gitOutput').textContent = [result.stdout, result.stderr].filter(Boolean).join('\n'); toast(result.returncode === 0 ? 'Commit creado.' : 'Git devolvió un error.', result.returncode !== 0); await refreshGit(); }
  catch (error) { toast(error.message, true); }
});
$('#pushButton').addEventListener('click', async () => {
  if (!confirm('¿Enviar los commits pendientes al remoto Git?')) return;
  try { const result = await (await jsonPost('/api/git', { action: 'push' })).json(); $('#gitOutput').textContent = [result.stdout, result.stderr].filter(Boolean).join('\n'); toast(result.returncode === 0 ? 'Push completado.' : 'Git devolvió un error.', result.returncode !== 0); }
  catch (error) { toast(error.message, true); }
});

const toolDescriptions = {
  read_file: ['Leer archivo', { path: 'Ruta relativa' }], write_file: ['Escribir archivo', { path: 'Ruta relativa', content: 'Contenido nuevo' }], delete_file: ['Eliminar archivo', { path: 'Ruta relativa' }], list_files: ['Listar archivos', { path: '.' }], create_folder: ['Crear carpeta', { path: 'Ruta relativa' }], run_terminal: ['Comando seguro', { command: 'git status' }], git_status: ['Estado Git', {}], git_commit: ['Crear commit', { message: 'Mensaje' }], git_push: ['Git push', {}], pm2_list: ['Lista PM2', {}], pm2_restart: ['Reiniciar PM2', { name: 'nombre' }], pm2_logs: ['Logs PM2', { name: 'nombre' }], docker_ps: ['Lista Docker', {}], docker_restart: ['Reiniciar Docker', { container: 'contenedor' }], system_info: ['Sistema', {}], ollama_models: ['Modelos disponibles', {}], ollama_chat: ['Chat Ollama', { model: 'qwen2.5:7b', messages: [{ role: 'user', content: 'Hola' }] }], tts_generate: ['Generar audio', { text: 'Hola desde el Codespace', engine: 'kokoro', format: 'wav', path: 'assets/speech.wav' }], write_binary: ['Guardar archivo binario', { path: 'assets/media.png', content: 'base64' }], generate_image: ['Generar imagen', { prompt: 'Descripción', model: 'flux', path: 'assets/image.png' }], edit_image: ['Editar imagen', { path: 'assets/source.png', prompt: 'Descripción', model: 'flux', output_path: 'assets/edited.png' }], generate_video: ['Generar vídeo', { prompt: 'Descripción', model: 'wan', path: 'assets/video.mp4' }], upscale_image: ['Aumentar resolución', { path: 'assets/source.png', output_path: 'assets/upscaled.png' }], caption_image: ['Describir imagen', { path: 'assets/image.png' }]
};
async function loadMcp() {
  try {
    const data = await (await api('/api/mcp')).json(); state.toolNames = data.tools;
    $('#toolCount').textContent = `${data.tools.length} TOOLS`;
    const container = $('#mcpTools'); container.replaceChildren();
    for (const name of data.tools) {
      const item = document.createElement('button'); item.className = 'tool-chip'; item.textContent = name; item.title = `${toolDescriptions[name]?.[0] || name} — abrir formulario`;
      item.addEventListener('click', () => invokeMcp(name)); container.append(item);
    }
  } catch (error) { $('#mcpTools').textContent = error.message; }
}
async function invokeMcp(name) {
  const [label, defaults] = toolDescriptions[name] || [name, {}];
  const answer = Object.keys(defaults).length ? prompt(`${label}\nArgumentos JSON:`, JSON.stringify(defaults)) : '{}';
  if (answer === null) return;
  let args; try { args = JSON.parse(answer); } catch { return toast('JSON de argumentos no válido.', true); }
  if (['delete_file', 'git_commit', 'git_push', 'pm2_restart', 'docker_restart', 'write_file', 'write_binary', 'tts_generate', 'generate_image', 'edit_image', 'generate_video', 'upscale_image'].includes(name) && !confirm(`¿Confirmas la operación MCP ${name}?`)) return;
  try { const result = await (await jsonPost('/api/mcp', { tool: name, args })).json(); const output = pretty(result); toast(`${label}: ${output.slice(0, 180)}`); }
  catch (error) { toast(error.message, true); }
}

async function refreshSystem() {
  try {
    const data = await (await api('/api/system')).json();
    $('#ollamaValue').textContent = data.ollama ? 'EN LÍNEA' : 'OFFLINE';
    if (data.host?.disk) {
      const disk = data.host.disk; $('#diskValue').textContent = `${Math.round((1 - disk.free / disk.total) * 100)}%`;
      $('#cpuValue').textContent = typeof data.host.cpu_percent === 'number' ? `${Math.round(data.host.cpu_percent)}%` : '—';
      $('#ramValue').textContent = typeof data.host.ram_percent === 'number' ? `${Math.round(data.host.ram_percent)}%` : '—';
    }
    $('#systemOutput').textContent = JSON.stringify(data, null, 2); setConnection(data.bridge.connected);
  } catch (error) { $('#systemOutput').textContent = error.message; }
}
$('#refreshSystem').addEventListener('click', refreshSystem);

function updateClock() {
  $('#clock').textContent = new Intl.DateTimeFormat('es', { hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }).format(new Date()) + ' UTC';
}
updateClock(); setInterval(updateClock, 30000);

(async function boot() {
  const targetView = location.hash.slice(1);
  if ($('#view-' + targetView)) state.view = targetView;
  try {
    const session = await (await fetch('/api/session', { credentials: 'same-origin' })).json();
    if (session.authenticated) { showApp(); return; }
    window.Telegram?.WebApp?.ready(); window.Telegram?.WebApp?.expand();
    const initData = window.Telegram?.WebApp?.initData;
    if (initData) {
      try { await jsonPost('/api/login/telegram', { init_data: initData }); showApp(); return; }
      catch (error) { $('#loginError').textContent = error.message; }
    }
    showLogin();
  } catch { showLogin(); }
})();
