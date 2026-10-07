import { Button } from "@radix-ui/themes";
import { lazy, Suspense, useState } from "react";
import { ArrowDown, ArrowRight, ArrowUpRight, AudioLines, BookOpen, Check, Film, Link, Music, Pause, Play, Plus, Scissors, Sparkles, Upload, WandSparkles } from "lucide-react";
import type { CSSProperties } from "react";
import type { AppPage } from "../App";
import { Footer, SiteNavigation } from "./SiteChrome";

const ExerciseHistory = lazy(() => import("../ExerciseHistory"));
const waveHeights = [8, 15, 23, 11, 29, 38, 20, 31, 45, 26, 17, 33, 22, 40, 29, 15, 24, 10, 18, 7];
const toolCards = [
  { page: "gif", icon: Film, title: "视频转 GIF", description: "把那个忍不住重看的瞬间，做成会动的表情包。", tags: ["自由裁剪", "添加文字", "变速循环"], action: "制作 GIF", color: "purple", format: ".gif" },
  { page: "audio", icon: Music, title: "视频提取音频", description: "一段喜欢的旋律，一句想留住的话，单独存下来。", tags: ["片段试听", "音频增强", "多种格式"], action: "提取音频", color: "pink", format: ".mp3" },
  { page: "summary", icon: Sparkles, title: "视频内容总结", description: "长视频先看重点，让知识和灵感都有迹可循。", tags: ["内容概览", "时间点", "分享卡片"], action: "生成总结", color: "green", format: ".md" },
  { page: "exercises", icon: BookOpen, title: "讲课视频转练习题", description: "从听懂到真正掌握，给刚学到的知识出几道题。", tags: ["知识点提炼", "多种题型", "打印试卷"], action: "生成练习题", color: "orange", format: "A4" }
] as const;

export function LittleCharacter({ compact = false }: { compact?: boolean }) {
  return <svg className={compact ? "little-character compact" : "little-character"} viewBox="0 0 240 190" fill="none" aria-hidden="true">
    <ellipse cx="122" cy="172" rx="66" ry="8" fill="#6550A7" opacity=".12" />
    <g className="character-body">
      <path d="M71 103C49 91 39 105 49 122C54 131 61 134 70 132M173 97C195 86 207 96 194 114C190 120 184 124 176 124" fill="#D6F381" stroke="#536534" strokeWidth="3" strokeLinejoin="round" />
      <path d="M91 145L82 163C79 169 84 173 95 169L107 149M141 147L150 166C153 172 164 170 161 164L156 143" fill="#D6F381" stroke="#536534" strokeWidth="3" strokeLinejoin="round" />
      <path d="M70 58C62 42 76 26 94 34C107 18 128 22 137 35C156 26 174 40 171 58C190 72 184 92 178 105C185 128 167 148 147 148C133 160 112 156 102 148C79 154 62 136 67 116C52 100 54 77 70 58Z" fill="#D6F381" stroke="#536534" strokeWidth="3" />
      <path d="M77 74C83 48 98 43 112 42" stroke="#F1FFD0" strokeWidth="6" strokeLinecap="round" />
      <ellipse cx="103" cy="84" rx="5" ry="7" fill="#38412A" /><ellipse cx="143" cy="84" rx="5" ry="7" fill="#38412A" />
      <path d="M112 99Q123 112 135 99" stroke="#38412A" strokeWidth="3.5" strokeLinecap="round" />
      <ellipse cx="90" cy="101" rx="8" ry="4" fill="#A9CD61" /><ellipse cx="155" cy="101" rx="8" ry="4" fill="#A9CD61" />
    </g>
    <path className="character-spark" d="M200 37V51M193 44H207M34 63V73M29 68H39" stroke="#8D74CD" strokeWidth="3" strokeLinecap="round" />
  </svg>;
}

function HeroDemo() {
  const [playing, setPlaying] = useState(true);
  return <div className={`hero-demo${playing ? " is-playing" : ""}`} aria-label="视频转换效果示意">
    <div className="demo-orbit orbit-one" /><div className="demo-orbit orbit-two" />
    <span className="demo-spark spark-one"><Plus size={20} /></span><span className="demo-spark spark-two"><Plus size={13} /></span>
    <div className="demo-video">
      <div className="demo-window-bar"><span className="window-dots"><i /><i /><i /></span><span>little-moment.mp4</span><span className="demo-video-label">VIDEO</span></div>
      <div className="demo-video-screen"><span className="demo-frame-corner top-left" /><span className="demo-frame-corner bottom-right" /><LittleCharacter /><span className="demo-caption">快乐就是这么简单 :)</span></div>
      <div className="demo-player"><Play size={12} fill="currentColor" aria-hidden="true" /><div className="demo-player-track"><span /></div><span>00:03</span><span className="demo-player-duration">/ 00:12</span></div>
    </div>
    <div className="demo-output demo-gif"><div className="demo-output-heading"><Film size={14} /><span>一张表情包</span><span className="file-tag">GIF</span></div><div className="demo-gif-screen"><LittleCharacter compact /><span>好耶！</span></div><div className="demo-gif-meta"><span className="live-dot" />无限循环的快乐</div></div>
    <div className="demo-output demo-audio"><div className="demo-output-heading"><Music size={14} /><span>一段好声音</span><span className="file-tag">MP3</span></div><div className="demo-wave"><span className="wave-play"><Play size={12} fill="currentColor" /></span>{waveHeights.map((height, index) => <i key={index} style={{ "--bar-height": `${height}px`, "--bar-delay": `${index * .07}s` } as CSSProperties} />)}</div></div>
    <div className="demo-output demo-note"><span className="note-icon"><Sparkles size={15} /></span><div><strong>一份知识笔记</strong><span>重点，帮你记下来了。</span></div><span className="note-check"><Check size={13} /></span></div>
    <Button className="demo-toggle" type="button" onClick={() => setPlaying(value => !value)} aria-label={playing ? "暂停效果演示" : "播放效果演示"}>{playing ? <Pause size={12} /> : <Play size={12} />}{playing ? "暂停演示" : "播放演示"}</Button>
    <span className="demo-handnote">一个视频，更多可能 <svg viewBox="0 0 58 32" fill="none"><path d="M3 5C21 27 41 23 52 9M42 9L53 7L52 19" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg></span>
  </div>;
}

export default function HomePage({ navigateTo }: { navigateTo: (page: AppPage) => void }) {
  return <main className="app-shell home-shell">
    <header className="topbar"><SiteNavigation currentPage="home" navigateTo={navigateTo} /></header>
    <div className="home-board">
      <section className="home-hero" aria-labelledby="home-title">
        <div className="hero-copy"><span className="eyebrow"><span className="eyebrow-dot" />给视频一点新玩法</span>
          <h1 id="home-title">好片段，<br />换个方式<span className="headline-accent">留下<span className="accent-underline" /></span>。</h1>
          <p>做张表情包，留段好声音，整理一份笔记。<br className="desktop-break" />把视频里的喜欢，变成随手可用的小作品。</p>
          <div className="hero-actions"><Button className="primary-button" type="button" onClick={() => navigateTo("gif")}><Scissors size={18} />开始制作 GIF<ArrowRight size={17} /></Button><Button className="text-button" type="button" onClick={() => document.getElementById("tools")?.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth", block: "start" })}>看看全部工具<ArrowDown size={16} /></Button></div>
          <div className="hero-source-note"><span><Upload size={14} />本地视频</span><span className="source-note-plus">+</span><span><Link size={14} />Bilibili 链接</span><span className="source-note-divider" /><span>灵感来了，就动手</span></div>
        </div>
        <HeroDemo />
      </section>
      <section className="tools-section" id="tools" aria-labelledby="tools-title">
        <div className="tools-heading"><div><span className="section-eyebrow">THE TOOLBOX</span><h2 id="tools-title">你的视频，想变成什么？</h2></div><span className="tools-heading-note"><WandSparkles size={15} />四个小工具，刚好够用</span></div>
        <div className="tool-grid">{toolCards.map(({ page, icon: Icon, title, description, tags, action, color, format }) => <button className={`tool-card tool-card-${color}`} type="button" key={page} onClick={() => navigateTo(page)}>
          <span className="tool-card-top"><span className="tool-card-icon"><Icon size={25} strokeWidth={1.6} /></span><span className="tool-format">{format}</span></span>
          <span className="tool-card-title">{title}</span><span className="tool-card-copy">{description}</span>
          <span className="tool-tags">{tags.map(tag => <span key={tag}>{tag}</span>)}</span>
          <span className="tool-card-action">{action}<ArrowUpRight size={18} /></span>
        </button>)}</div>
      </section>
      <section className="how-it-works" aria-labelledby="how-title"><div className="how-intro"><span className="how-icon"><AudioLines size={22} /></span><h2 id="how-title">小工具，<br />小小的使用门槛。</h2></div>
        <ol><li><span className="step-number">01</span><div><strong>带来一个视频</strong><p>上传文件，或粘贴 B 站链接。</p></div></li><li><span className="step-number">02</span><div><strong>调成喜欢的样子</strong><p>选取片段，按需要调整内容。</p></div></li><li><span className="step-number">03</span><div><strong>留住，或者分享</strong><p>下载作品，随时使用和分享。</p></div></li></ol>
      </section>
    </div>
    <Suspense fallback={null}><ExerciseHistory compact /></Suspense>
    <Footer currentPage="home" navigateTo={navigateTo} />
  </main>;
}
