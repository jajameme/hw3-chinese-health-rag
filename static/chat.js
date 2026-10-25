const messages = document.querySelector('#messages');
const form = document.querySelector('#chat-form');
const question = document.querySelector('#question');
const retrieve = document.querySelector('#retrieve');
const send = document.querySelector('#send');
const statusText = document.querySelector('#request-status');
let busy = false;
const stageLabels = {starting:'準備中', install_torch:'安裝運算套件', install_packages:'安裝應用套件', runtime:'準備地端引擎', model_download:'下載本機模型', crawl_list:'整理新聞列表', crawl_articles:'下載新聞內容', summarize:'逐篇產生摘要', make_index:'建立搜尋索引', tests:'檢查資料流程', evaluate:'產生問答展示', web:'啟動網頁', completed:'資料已就緒'};

function addTurn(user) {
  const turn = document.createElement('article'); turn.className = 'turn';
  const userEl = document.createElement('div'); userEl.className = 'user-message';
  const text = document.createElement('span'); text.textContent = user; userEl.append(text);
  const label = document.createElement('div'); label.className = 'assistant-label';
  const icon = document.createElement('b'); icon.textContent = '＋';
  label.append(icon, document.createTextNode('好好讀 · 新聞小幫手'));
  const answer = document.createElement('div'); answer.className = 'answer';
  turn.append(userEl, label, answer); messages.append(turn);
  return {turn, answer};
}
function finish(block, data) {
  block.answer.textContent = data.answer;
  const sources = document.createElement('div'); sources.className = 'sources';
  const title = document.createElement('div'); title.className = 'sources-title';
  title.textContent = data.used_retrieval ? '本輪檢索來源' : '本輪未重新檢索 · 以下為先前對話來源'; sources.append(title);
  if (!data.sources.length) { const p = document.createElement('p'); p.textContent = '無可引用的新聞來源'; sources.append(p); }
  data.sources.forEach((item, i) => {
    const url = new URL(item.link);
    if (url.protocol !== 'https:' || url.hostname !== 'www.healthnews.com.tw') return;
    const card = document.createElement('div'); card.className = 'source';
    const number = document.createElement('span'); number.textContent = String(i + 1).padStart(2, '0');
    const body = document.createElement('div'); const link = document.createElement('a');
    link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer'; link.textContent = item.title + ' ↗';
    const date = document.createElement('small'); date.textContent = '健康醫療網 · 文章新增時間 ' + item.published_at;
    body.append(link, date); card.append(number, body); sources.append(card);
  });
  const notice = document.createElement('p'); notice.className = 'disclaimer'; notice.textContent = data.disclaimer;
  block.turn.append(sources, notice);
}
async function refreshHealth() {
  try {
    const r = await fetch('/api/health'); if (!r.ok) throw new Error('狀態暫時無法讀取');
    const data = await r.json(); document.querySelector('#article-count').textContent = data.articles;
    document.querySelector('#db-status').textContent = data.ready ? '索引已就緒' : '資料準備中 · ' + (stageLabels[data.stage] || '尚未建立索引');
    if (!busy) statusText.textContent = data.ready ? '準備就緒' : '請待資料流程完成後提問';
    if (!busy) send.disabled = !data.ready;
  } catch (_) { document.querySelector('#db-status').textContent = '連線暫時中斷'; }
}
form.addEventListener('submit', async (event) => {
  event.preventDefault(); const user = question.value.trim(); if (!user || busy) return;
  busy = true; send.disabled = true; document.querySelector('#clear').disabled = true;
  const useRetrieval = retrieve.checked; const block = addTurn(user); question.value = '';
  let finished = false;
  try {
    const r = await fetch('/api/chat/stream', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({message:user, use_retrieval:useRetrieval})});
    if (!r.ok) { const error = await r.json(); throw new Error(error.error || '請求失敗'); }
    const reader = r.body.getReader(); const decoder = new TextDecoder(); let buffer = '';
    while (true) {
      const {value, done} = await reader.read(); buffer += decoder.decode(value || new Uint8Array(), {stream:!done});
      let end;
      while ((end = buffer.indexOf('\n\n')) !== -1) {
        const packet = buffer.slice(0, end); buffer = buffer.slice(end + 2);
        const kind = packet.split('\n').find(x => x.startsWith('event: '))?.slice(7);
        const raw = packet.split('\n').find(x => x.startsWith('data: '))?.slice(6); if (!raw) continue;
        const data = JSON.parse(raw);
        if (kind === 'status') statusText.textContent = data.message;
        if (kind === 'token') { block.answer.textContent += data.text; statusText.textContent = '正在生成回答…'; }
        if (kind === 'done') { finish(block, data); finished = true; statusText.textContent = '回答完成 · 來源已附上'; }
        if (kind === 'error') throw new Error(data.message);
      }
      if (done) break;
    }
    if (!finished) throw new Error('連線提前結束；這輪未儲存，請重試。');
  } catch (error) { const p = document.createElement('p'); p.className = 'error'; p.textContent = error.message; block.turn.append(p); statusText.textContent = '未完成，請重試'; }
  finally { busy = false; send.disabled = false; document.querySelector('#clear').disabled = false; refreshHealth(); }
});
retrieve.addEventListener('change', () => { document.querySelector('#mode-hint').textContent = retrieve.checked ? '從新聞找資料，附上來源' : '整理先前對話，沿用先前來源'; });
document.querySelectorAll('[data-question]').forEach(button => button.addEventListener('click', () => { question.value = button.dataset.question; question.focus(); }));
document.querySelector('#clear').addEventListener('click', async () => {
  if (busy) return;
  if (location.pathname === '/demo') { location.href = '/'; return; }
  try { const r = await fetch('/api/clear', {method:'POST'}); if (!r.ok) throw new Error(); messages.replaceChildren(); statusText.textContent = '已開始新對話'; }
  catch (_) { statusText.textContent = '清除失敗，請重試'; }
});
question.addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); if (!send.disabled) form.requestSubmit(); } });
async function restore() { try { const r = await fetch(location.pathname === '/demo' ? '/api/demo' : '/api/history'); if (!r.ok) return; (await r.json()).forEach(data => finish(addTurn(data.user), data)); } catch (_) {} }
restore(); refreshHealth(); setInterval(refreshHealth, 15000);
