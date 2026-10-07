import { Button, Dialog, IconButton } from "@radix-ui/themes";
import { ArrowUpRight, BookOpen, CircleHelp, Film, Home, Music, Sparkles, X } from "lucide-react";
import type { AppPage } from "../App";

type NavigationProps = { currentPage: AppPage; navigateTo: (page: AppPage) => void };

const tools = [
  { page: "home", label: "工具箱", icon: Home },
  { page: "gif", label: "GIF 制作", icon: Film },
  { page: "audio", label: "音频提取", icon: Music },
  { page: "summary", label: "视频总结", icon: Sparkles },
  { page: "exercises", label: "练习题", icon: BookOpen }
] as const;

export function BrandMark({ small = false }: { small?: boolean }) {
  return <span className={`brand-mark${small ? " small" : ""}`} aria-hidden="true">
    <svg viewBox="0 0 40 40" fill="none">
      <path d="M12 11.5C12 10.3 13.4 9.6 14.4 10.2L29 18.7C30 19.3 30 20.7 29 21.3L14.4 29.8C13.4 30.4 12 29.7 12 28.5V11.5Z" fill="currentColor" />
      <path d="M6 11V29" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  </span>;
}

export function ToolNavigation({ currentPage, navigateTo }: NavigationProps) {
  return <nav className="tool-nav" aria-label="工具导航">
    {tools.map(({ page, label, icon: Icon }) => <button key={page} type="button"
      className={currentPage === page ? "active" : ""}
      aria-current={currentPage === page ? "page" : undefined}
      onClick={() => navigateTo(page)}><Icon size={16} strokeWidth={1.8} />{label}</button>)}
  </nav>;
}

function HelpDialog() {
  return <Dialog.Content className="help-dialog" maxWidth="570px">
    <div className="help-heading"><span className="eyebrow"><CircleHelp size={15} />使用指南</span>
      <Dialog.Close><IconButton variant="soft" aria-label="关闭使用指南"><X size={20} /></IconButton></Dialog.Close></div>
    <Dialog.Title>从一个视频开始。</Dialog.Title>
    <Dialog.Description className="help-intro">选好工具，带上你喜欢的视频，剩下的跟着页面走就好。</Dialog.Description>
    <div className="help-items">
      <article><Film size={21} /><div><h3>制作 GIF</h3><p>上传本地视频或导入 B 站视频，选择片段、拖动裁剪框，还可以添加文字。预览满意后点击「导出 GIF」。</p></div></article>
      <article><Music size={21} /><div><h3>提取音频</h3><p>粘贴 B 站视频链接或 BV 号，下载视频后设置起止时间，试听片段，再选择 MP3、M4A 或 WAV 导出。</p></div></article>
      <article><Sparkles size={21} /><div><h3>生成视频总结</h3><p>导入带 CC 字幕的 B 站视频，生成内容概览、关键时间点和金句。结果可复制、保存为分享图。</p></div></article>
      <article><BookOpen size={21} /><div><h3>生成练习题</h3><p>上传讲课视频或导入 B 站课程，确认转写文字与知识点，选择题型和难度。生成的试题会自动保存，也可以打印。</p></div></article>
    </div>
    <div className="help-tip">B 站链接支持分 P。粘贴带 <code>?p=2</code> 的链接，可以直接选中对应章节。</div>
  </Dialog.Content>;
}

export function SiteNavigation({ currentPage, navigateTo }: NavigationProps) {
  return <Dialog.Root>
    <div className="site-navigation">
      <button className="brand-link" type="button" onClick={() => navigateTo("home")} aria-label="Video to Any，回到工具箱">
        <BrandMark /><span className="brand-wordmark">Video<span className="brand-to"> to </span>Any<span className="brand-dot">.</span></span>
      </button>
      <ToolNavigation currentPage={currentPage} navigateTo={navigateTo} />
      <Dialog.Trigger><Button variant="surface" className="help-button"><CircleHelp size={17} /><span>使用指南</span></Button></Dialog.Trigger>
    </div>
    <HelpDialog />
  </Dialog.Root>;
}

export function Footer({ currentPage, navigateTo }: NavigationProps) {
  return <footer className="site-footer">
    <div className="footer-brand"><BrandMark small /><div><strong>Video to Any</strong><span>把喜欢的片段，变成想要的样子。</span></div></div>
    <a className="footer-tool-link" href="#/" onClick={event => { event.preventDefault(); navigateTo("home"); }} aria-current={currentPage === "home" ? "page" : undefined}>探索工具箱 <ArrowUpRight size={15} /></a>
    <div className="footer-meta">一个小工具 · Made by <span>ZUOAJ</span></div>
  </footer>;
}
