import { useCallback, useEffect, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { ArrowLeft, Check, ChevronRight, Clock3, FileUp, KeyRound, Loader2, LogOut, RefreshCw, Save, Settings2, ShieldCheck, SlidersHorizontal, Video } from "lucide-react";
import { BrandMark } from "./components/SiteChrome";
import "./admin.css";

type Session = { username: string; csrf_token: string; expires_at: number };
type BilibiliSettings = { enabled: boolean; check_interval_seconds: number; retry_interval_seconds: number; rate_limit_seconds: number };
type LLMSettings = {
  base_url: string; model: string; api_key_configured: boolean; timeout: number;
  reasoning_effort: string; thinking_type: string; reasoning_max_tokens: number;
  json_mode: boolean; summary_max_input_chars: number;
};
type Config = { bilibili: BilibiliSettings; llm: LLMSettings; site: { site_url: string } };
type BilibiliStatus = {
  status: string; message: string; has_cookie: boolean; has_refresh_token: boolean; account_id: string | null;
  last_checked_at: number | null; last_refreshed_at: number | null; next_check_at: number | null; cookie_expires_at: number | null;
};
type Section = "bilibili" | "llm" | "site" | "security";

class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); }
}

async function request<T>(path: string, csrf?: string, method = "GET", body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/api/_admin${path}`, {
    method, credentials: "same-origin", cache: "no-store", signal,
    headers: { ...(body !== undefined ? { "Content-Type": "application/json" } : {}), ...(csrf ? { "X-Admin-CSRF": csrf } : {}) },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {})
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(typeof result.detail === "string" ? result.detail : "请求失败，请稍后重试", response.status);
  return result as T;
}

function timeLabel(value?: number | null) {
  return value ? new Date(value * 1000).toLocaleString("zh-CN", { hour12: false }) : "尚无记录";
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return <label className="admin-field"><span>{label}</span>{children}{hint ? <small>{hint}</small> : null}</label>;
}

function Action({ busy, children, ...props }: { busy?: boolean; children: ReactNode } & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button {...props} disabled={props.disabled || busy} className={`admin-button ${props.className ?? ""}`}>
    {busy ? <Loader2 size={16} className="admin-spin" /> : null}{children}
  </button>;
}

export default function AdminPage() {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    request<Session>("/me", undefined, "GET", undefined, controller.signal)
      .then(setSession).catch(err => {
        if (err.name !== "AbortError" && (!(err instanceof ApiError) || err.status !== 401)) setError(err.message);
      }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    const previousTitle = document.title;
    document.title = "管理后台 · Video to Any";
    const robots = document.createElement("meta");
    robots.name = "robots"; robots.content = "noindex, nofollow";
    document.head.append(robots);
    return () => { controller.abort(); robots.remove(); document.title = previousTitle; };
  }, []);

  async function login(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(""); setNotice("");
    try {
      setSession(await request<Session>("/login", undefined, "POST", { username, password }));
      setPassword("");
    } catch (err) { setError((err as Error).message); setPassword(""); }
    finally { setBusy(false); }
  }

  const signedOut = useCallback((message: string) => { setSession(null); setNotice(message); setError(""); }, []);

  if (loading) return <main className="admin-page admin-loading" aria-busy="true"><Loader2 className="admin-spin" size={24} /><p>正在检查登录状态…</p></main>;
  if (session) return <Dashboard session={session} signedOut={signedOut} />;
  return <main className="admin-page admin-login">
    <a className="admin-back" href="#/"><ArrowLeft size={16} />回到工具箱</a>
    <div className="admin-login-board">
      <aside className="admin-login-intro"><BrandMark /><span className="admin-kicker">VIDEO TO ANY / 管理</span>
        <h1>让工具<br />持续就绪。</h1><p>在一个地方管理账号、<br />连接与运行设置。</p><div className="admin-private"><ShieldCheck size={17} />仅限管理员访问</div></aside>
      <section className="admin-login-form" aria-labelledby="admin-login-title">
        <span className="admin-icon"><KeyRound size={23} /></span><h2 id="admin-login-title">登录管理后台</h2><p>使用管理员账号继续。</p>
        {notice ? <div className="admin-notice" role="status"><Check size={16} />{notice}</div> : null}
        {error ? <div className="admin-error" role="alert">{error}</div> : null}
        <form onSubmit={login}>
          <Field label="用户名"><input required autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} maxLength={64} /></Field>
          <Field label="密码"><input required type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} maxLength={256} /></Field>
          <Action type="submit" busy={busy}>登录<ChevronRight size={17} /></Action>
        </form><p className="admin-login-help">首次使用请先在服务器创建管理员。</p>
      </section>
    </div>
  </main>;
}

const sections = [
  { id: "bilibili", label: "Bilibili 账号", icon: Video },
  { id: "llm", label: "大模型连接", icon: SlidersHorizontal },
  { id: "site", label: "站点设置", icon: Settings2 },
  { id: "security", label: "账号安全", icon: ShieldCheck }
] as const;

function Dashboard({ session, signedOut }: { session: Session; signedOut: (message: string) => void }) {
  const [section, setSection] = useState<Section>("bilibili");
  const [config, setConfig] = useState<Config | null>(null);
  const [status, setStatus] = useState<BilibiliStatus | null>(null);
  const [cookieText, setCookieText] = useState("");
  const [refreshToken, setRefreshToken] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const failed = useCallback((err: unknown) => {
    if (err instanceof ApiError && err.status === 401) signedOut("登录已过期，请重新登录");
    else if ((err as Error).name !== "AbortError") setError((err as Error).message);
  }, [signedOut]);

  const [loadRevision, setLoadRevision] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      request<Config>("/settings", session.csrf_token, "GET", undefined, controller.signal),
      request<BilibiliStatus>("/bilibili", session.csrf_token, "GET", undefined, controller.signal)
    ]).then(([nextConfig, nextStatus]) => { setConfig(nextConfig); setStatus(nextStatus); }).catch(failed);
    return () => controller.abort();
  }, [session.csrf_token, failed, loadRevision]);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setInterval(() => {
      if (!document.hidden) request<BilibiliStatus>("/bilibili", session.csrf_token, "GET", undefined, controller.signal).then(setStatus).catch(failed);
    }, 60_000);
    return () => { window.clearInterval(timer); controller.abort(); };
  }, [session.csrf_token, failed]);

  async function perform(name: string, action: () => Promise<void>, message?: string) {
    setBusy(name); setError(""); setNotice("");
    try { await action(); if (message) setNotice(message); } catch (err) { failed(err); }
    finally { setBusy(""); }
  }

  function changeSection(next: Section) { setSection(next); setNotice(""); setError(""); }

  async function saveCredentials(event: FormEvent) {
    event.preventDefault();
    if (!cookieText.trim() && !refreshToken.trim()) { setError("请先导入 Cookie 或填写刷新令牌"); return; }
    await perform("credentials", async () => {
      setStatus(await request<BilibiliStatus>("/bilibili/credentials", session.csrf_token, "PUT", {
        ...(cookieText.trim() ? { cookie_text: cookieText.trim() } : {}),
        ...(refreshToken.trim() ? { refresh_token: refreshToken.trim() } : {})
      }));
      setCookieText(""); setRefreshToken("");
    }, "登录凭据已保存。可以检查一次以确认账号状态。");
  }

  async function importFile(file?: File) {
    if (!file) return;
    if (file.size > 100_000) { setError("Cookie 文件不能超过 100 KB"); return; }
    try { setCookieText(await file.text()); setError(""); setNotice("Cookie 文件已读入，保存后生效。"); }
    catch { setError("无法读取文件，请重新选择"); }
  }

  const healthy = status?.status === "healthy";
  const statusNames: Record<string, string> = {
    healthy: "账号已就绪", unchecked: "等待检查", unconfigured: "尚未连接", disabled: "自动续期已关闭",
    missing_refresh_token: "需要刷新令牌", refresh_required: "等待续期", expired: "需要重新登录",
    refresh_token_invalid: "请更新登录凭据", retrying: "等待重试", configuration_error: "凭据保存异常"
  };

  return <main className="admin-page admin-dashboard">
    <header className="admin-header"><a href="#/" className="admin-brand"><BrandMark /><strong>Video to Any<span>管理后台</span></strong></a>
      <div className="admin-header-actions"><span><ShieldCheck size={15} />{session.username}</span>
        <Action type="button" className="secondary" disabled={Boolean(busy)} onClick={() => perform("logout", async () => {
          await request("/logout", session.csrf_token, "POST"); signedOut("已退出管理后台");
        })}><LogOut size={15} />退出登录</Action></div>
    </header>
    <div className="admin-workspace"><aside className="admin-sidebar"><span className="admin-kicker">运行管理</span>
      <nav aria-label="管理菜单">{sections.map(({ id, label, icon: Icon }) => <button key={id} type="button" disabled={Boolean(busy)}
        aria-current={section === id ? "page" : undefined} className={section === id ? "active" : ""} onClick={() => changeSection(id)}><Icon size={18} />{label}<ChevronRight size={15} /></button>)}</nav>
      <div className="admin-sidebar-note"><ShieldCheck size={20} /><p>配置保存在服务器。<br />保存后即可使用。</p></div>
      <a href="#/" className="admin-back"><ArrowLeft size={15} />回到工具箱</a>
    </aside>
    <section className="admin-content" aria-busy={Boolean(busy)}>
      {error ? <div className="admin-error" role="alert">{error}</div> : null}
      {notice ? <div className="admin-notice" role="status"><Check size={17} />{notice}</div> : null}
      {!config ? <div className="admin-empty">{error ? <Action type="button" className="secondary" onClick={() => { setError(""); setLoadRevision(value => value + 1); }}><RefreshCw size={15} />重新读取设置</Action> : <><Loader2 className="admin-spin" />正在读取设置…</>}</div> : null}
      {config && section === "bilibili" ? <>
        <div className="admin-title"><span className="admin-kicker">BILIBILI</span><h1>账号与自动续期</h1><p>视频下载和字幕提取共用这份登录凭据。</p></div>
        <section className="admin-status" aria-label="Bilibili 账号状态"><div className="admin-status-heading"><div><span className={`admin-status-dot ${healthy ? "healthy" : ""}`} /><strong>{status ? statusNames[status.status] ?? "正在检查" : "读取状态中"}</strong>
          {status?.account_id ? <span className="admin-account-id">UID {status.account_id}</span> : null}</div>
          <Action type="button" className="secondary" busy={busy === "check"} disabled={Boolean(busy)} onClick={() => perform("check", async () => {
            setStatus(await request<BilibiliStatus>("/bilibili/check", session.csrf_token, "POST"));
          })}><RefreshCw size={15} />检查并续期</Action></div>
          <p>{status?.message ?? "正在读取账号状态"}</p>
          <div className="admin-status-times"><div><Clock3 size={14} /><span>上次检查<strong>{timeLabel(status?.last_checked_at)}</strong></span></div>
            <div><span>上次续期<strong>{timeLabel(status?.last_refreshed_at)}</strong></span></div>
            <div><span>下次检查<strong>{config.bilibili.enabled ? timeLabel(status?.next_check_at) : "自动续期已关闭"}</strong></span></div></div>
        </section>
        <section className="admin-panel"><div className="admin-panel-heading"><div><h2>登录凭据</h2><p>已保存的凭据不会回显，更换账号时请同时更新刷新令牌。</p></div>
          <span className="admin-saved">{status?.has_cookie ? "Cookie 已保存" : "Cookie 未配置"} · {status?.has_refresh_token ? "令牌已保存" : "令牌未配置"}</span></div>
          <form onSubmit={saveCredentials}><Field label="Bilibili Cookie" hint="支持浏览器 Cookie、Netscape 文件或 JSON 导出。留空保留现有 Cookie。">
            <textarea rows={4} spellCheck={false} autoComplete="off" value={cookieText} onChange={e => setCookieText(e.target.value)} placeholder="SESSDATA=…; bili_jct=…; DedeUserID=…" maxLength={100000} /></Field>
            <label className="admin-file"><FileUp size={16} />从文件导入<input type="file" accept=".txt,.json,text/plain,application/json" aria-label="导入 Cookie 文件" onChange={e => {
              void importFile(e.target.files?.[0]); e.target.value = "";
            }} /></label>
            <Field label="刷新令牌" hint="复制同一次登录的 bilibili.com 本地存储 ac_time_value。仅补充令牌时可保留上面的 Cookie。">
              <input type="password" autoComplete="new-password" value={refreshToken} onChange={e => setRefreshToken(e.target.value)} placeholder={status?.has_refresh_token ? "已配置，留空保持" : "填写 ac_time_value"} maxLength={4096} /></Field>
            <details className="admin-help"><summary>从哪里获取登录凭据？</summary><p>登录 Bilibili 后，使用浏览器开发者工具获取 Cookie。打开「应用 / Application → 本地存储 / Local Storage → https://www.bilibili.com」，复制 ac_time_value。Cookie 与令牌应来自同一次登录。</p></details>
            <div className="admin-form-actions"><Action type="submit" busy={busy === "credentials"} disabled={Boolean(busy)}><Save size={16} />保存登录凭据</Action>
              <Action type="button" className="danger secondary" disabled={Boolean(busy) || !status?.has_cookie} onClick={() => {
                if (window.confirm("移除当前 Bilibili 账号？之后下载和字幕提取将不再使用这份登录态。")) void perform("clear", async () => {
                  setStatus(await request<BilibiliStatus>("/bilibili/credentials", session.csrf_token, "DELETE")); setCookieText(""); setRefreshToken("");
                }, "Bilibili 账号已移除");
              }}>移除账号</Action></div>
          </form>
        </section>
        <section className="admin-panel"><div className="admin-panel-heading"><div><h2>自动续期</h2><p>低频检查，平台要求刷新时才更新登录态。</p></div></div>
          <form onSubmit={e => { e.preventDefault(); void perform("keepalive", async () => {
            const bilibili = await request<BilibiliSettings>("/settings/bilibili", session.csrf_token, "PUT", config.bilibili);
            setConfig(c => c ? { ...c, bilibili } : c); setStatus(await request<BilibiliStatus>("/bilibili", session.csrf_token));
          }, "自动续期设置已保存"); }}>
            <label className="admin-toggle"><input type="checkbox" checked={config.bilibili.enabled} onChange={e => setConfig({ ...config, bilibili: { ...config.bilibili, enabled: e.target.checked } })} /><span>启用自动续期<small>关闭后仍可手动检查和续期。</small></span></label>
            <div className="admin-fields-three"><Field label="检查间隔（小时）"><input type="number" min={1} max={168} step={1} required value={config.bilibili.check_interval_seconds / 3600} onChange={e => setConfig({ ...config, bilibili: { ...config.bilibili, check_interval_seconds: Number(e.target.value) * 3600 } })} /></Field>
              <Field label="失败重试（分钟）"><input type="number" min={1} max={1440} required value={config.bilibili.retry_interval_seconds / 60} onChange={e => setConfig({ ...config, bilibili: { ...config.bilibili, retry_interval_seconds: Number(e.target.value) * 60 } })} /></Field>
              <Field label="接口请求间隔（秒）"><input type="number" min={0.5} max={60} step={0.1} required value={config.bilibili.rate_limit_seconds} onChange={e => setConfig({ ...config, bilibili: { ...config.bilibili, rate_limit_seconds: Number(e.target.value) } })} /></Field></div>
            <div className="admin-form-actions"><Action type="submit" busy={busy === "keepalive"} disabled={Boolean(busy)}><Save size={16} />保存续期设置</Action></div>
          </form></section>
      </> : null}
      {config && section === "llm" ? <>
        <div className="admin-title"><span className="admin-kicker">MODEL CONNECTION</span><h1>大模型连接</h1><p>视频总结与练习题使用这里的模型设置。</p></div>
        <section className="admin-panel"><form onSubmit={e => { e.preventDefault(); void perform("llm", async () => {
          const { api_key_configured: _configured, ...values } = config.llm;
          const llm = await request<LLMSettings>("/settings/llm", session.csrf_token, "PUT", { ...values, ...(clearKey ? { api_key: "" } : apiKey.trim() ? { api_key: apiKey.trim() } : {}) });
          setConfig(c => c ? { ...c, llm } : c); setApiKey(""); setClearKey(false);
        }, "大模型设置已保存，后续调用将使用新配置"); }}>
          <Field label="服务地址" hint="填写提供商的 OpenAI 兼容接口地址。"><input required type="url" value={config.llm.base_url} onChange={e => setConfig({ ...config, llm: { ...config.llm, base_url: e.target.value } })} placeholder="https://api.deepseek.com" /></Field>
          <Field label="模型名称"><input required value={config.llm.model} onChange={e => setConfig({ ...config, llm: { ...config.llm, model: e.target.value } })} placeholder="deepseek-chat" /></Field>
          <Field label="API Key" hint={config.llm.api_key_configured ? "已配置，留空保留原密钥。" : "尚未配置，填写后可以调用大模型。"}><input type="password" autoComplete="new-password" disabled={clearKey} value={apiKey} onChange={e => setApiKey(e.target.value)} maxLength={4096} /></Field>
          <label className="admin-toggle"><input type="checkbox" checked={clearKey} onChange={e => setClearKey(e.target.checked)} /><span>清除已保存的 API Key<small>清除后暂停大模型调用。</small></span></label>
          <div className="admin-fields-two"><Field label="响应等待时间（秒）"><input type="number" min={10} max={1800} required value={config.llm.timeout} onChange={e => setConfig({ ...config, llm: { ...config.llm, timeout: Number(e.target.value) } })} /></Field>
            <Field label="总结分段字数"><input type="number" min={1000} max={100000} required value={config.llm.summary_max_input_chars} onChange={e => setConfig({ ...config, llm: { ...config.llm, summary_max_input_chars: Number(e.target.value) } })} /></Field></div>
          <details className="admin-help"><summary>思考与输出选项</summary><div className="admin-fields-two"><Field label="思考深度"><select value={config.llm.reasoning_effort} onChange={e => setConfig({ ...config, llm: { ...config.llm, reasoning_effort: e.target.value } })}>
            <option value="">自动</option>{["none", "minimal", "low", "medium", "high", "max"].map(value => <option key={value} value={value}>{value}</option>)}</select></Field>
            <Field label="思考模式"><select value={config.llm.thinking_type} onChange={e => setConfig({ ...config, llm: { ...config.llm, thinking_type: e.target.value } })}><option value="">跟随提供商</option><option value="enabled">启用</option><option value="disabled">关闭</option></select></Field>
            <Field label="思考与输出预算（token）"><input type="number" min={1} max={131072} required value={config.llm.reasoning_max_tokens} onChange={e => setConfig({ ...config, llm: { ...config.llm, reasoning_max_tokens: Number(e.target.value) } })} /></Field></div>
            <label className="admin-toggle"><input type="checkbox" checked={config.llm.json_mode} onChange={e => setConfig({ ...config, llm: { ...config.llm, json_mode: e.target.checked } })} /><span>请求结构化 JSON 输出</span></label>
          </details><div className="admin-form-actions"><Action type="submit" busy={busy === "llm"} disabled={Boolean(busy)}><Save size={16} />保存大模型设置</Action></div>
        </form></section>
      </> : null}
      {config && section === "site" ? <>
        <div className="admin-title"><span className="admin-kicker">SITE</span><h1>站点设置</h1><p>管理对外分享时使用的站点地址。</p></div>
        <section className="admin-panel"><form onSubmit={e => { e.preventDefault(); void perform("site", async () => {
          const site = await request<Config["site"]>("/settings/site", session.csrf_token, "PUT", config.site);
          setConfig(c => c ? { ...c, site } : c);
        }, "站点设置已保存"); }}><Field label="站点地址" hint="用于分享图片中的二维码。留空使用访问页面时的地址。">
          <input type="url" value={config.site.site_url} onChange={e => setConfig({ ...config, site: { site_url: e.target.value } })} placeholder="https://video.example.com" /></Field>
          <div className="admin-form-actions"><Action type="submit" busy={busy === "site"} disabled={Boolean(busy)}><Save size={16} />保存站点设置</Action></div>
        </form></section>
      </> : null}
      {section === "security" ? <>
        <div className="admin-title"><span className="admin-kicker">ACCESS</span><h1>账号安全</h1><p>密码更新后，所有管理会话都会退出。</p></div>
        <section className="admin-panel"><div className="admin-session-info"><ShieldCheck size={18} /><span>当前管理员<strong>{session.username}</strong></span><span>本次登录有效至<strong>{timeLabel(session.expires_at)}</strong></span></div>
          <form onSubmit={e => { e.preventDefault(); if (newPassword !== confirmPassword) { setError("两次输入的新密码不一致"); return; }
            void perform("password", async () => { await request("/password", session.csrf_token, "POST", { current_password: currentPassword, new_password: newPassword }); signedOut("密码已更新，请重新登录"); }); }}>
            <Field label="当前密码"><input required type="password" autoComplete="current-password" value={currentPassword} onChange={e => setCurrentPassword(e.target.value)} maxLength={256} /></Field>
            <Field label="新密码" hint="使用至少 12 个字符。"><input required type="password" autoComplete="new-password" minLength={12} maxLength={256} value={newPassword} onChange={e => setNewPassword(e.target.value)} /></Field>
            <Field label="确认新密码"><input required type="password" autoComplete="new-password" minLength={12} maxLength={256} value={confirmPassword} onChange={e => setConfirmPassword(e.target.value)} /></Field>
            <div className="admin-form-actions"><Action type="submit" busy={busy === "password"} disabled={Boolean(busy)}><KeyRound size={16} />更新密码并退出</Action></div>
          </form></section>
      </> : null}
    </section></div>
  </main>;
}
