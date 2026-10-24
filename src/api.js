let token;
export async function api(path, data, method) {
  if (!token) token = (await (await fetch('/api/session')).json()).token;
  const multipart = data instanceof FormData;
  const response = await fetch('/api' + path, {
    method: method || (data === undefined ? 'GET' : 'POST'),
    headers: { 'x-learnloop-token': token, ...(!multipart && data !== undefined ? {'Content-Type':'application/json'} : {}) },
    body: data === undefined ? undefined : multipart ? data : JSON.stringify(data)
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const error = new Error(typeof body.detail === 'string' ? body.detail : body.detail?.message || JSON.stringify(body.detail || `请求失败 ${response.status}`));
    error.status = response.status; error.current = body.current || body.detail?.current;
    throw error;
  }
  return response.json();
}
export const requestId = () => crypto.randomUUID();
export const date = value => value ? new Date(value).toLocaleString('zh-CN', {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}) : '—';
export const labels = {queued:'排队中',running:'处理中',completed:'完成',cancelled:'已取消',failed:'失败',uploaded:'已上传',parsing:'解析中',indexing:'索引中',ready:'就绪',pending_analysis:'待分析',pending_practice:'待补练',corrected_pending_review:'已纠正 · 待复习',review_passed:'复习通过',concept:'概念',application:'应用',syntax:'语法',assertion:'断言',design:'测试设计',environment:'环境',unknown:'待分类',decompose:'资料拆解',explain:'概念精讲',review:'错题复盘',prerequisite:'前置',contains:'组成',contrast:'对比',application:'应用'};
