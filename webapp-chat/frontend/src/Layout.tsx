import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { ActivityIcon, BotIcon, ChevronDownIcon, LayersIcon, LockIcon, MoonIcon, PanelRightIcon, PlusIcon, ShieldIcon, SlidersIcon, SparkIcon, StageIcon, SunIcon, UserIcon } from "./UiIcons";
import { demoRoutes, demos } from "./routes";
import type { ChatConfig, Demo } from "./types";

const scales = [.8, .9, 1, 1.1, 1.25, 1.4, 1.6];
const demoIcons: Record<Demo, ReactNode> = {
  "multi-fonte": <LayersIcon />,
  acl: <ShieldIcon />,
  agenti: <BotIcon />,
};

export function Header({ page, demo = null, user, source, tokenOk, onProfile, onReset, testsOpen, onTests }: { page: "chat" | "trace"; demo?: Demo | null; user?: string | null; source?: string; tokenOk?: boolean; onProfile?: () => void; onReset?: () => void; testsOpen?: boolean; onTests?: () => void }) {
  const [theme, setTheme] = useState(() => localStorage.getItem("wpc-theme") || "light");
  const [stage, setStage] = useState(() => {
    const param = new URLSearchParams(location.search).get("stage");
    return param === null ? localStorage.getItem("wpc-stage") === "on" : param !== "0";
  });
  const [scale, setScale] = useState(() => {
    const value = Number(localStorage.getItem("wpc-scale"));
    return localStorage.getItem("wpc-scale") !== null && Number.isInteger(value) && scales[value] ? value : 2;
  });
  const [menuOpen, setMenuOpen] = useState(false);
  const [viewOpen, setViewOpen] = useState(false);
  const header = useRef<HTMLElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const viewRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const root = document.documentElement;
    root.dataset.theme = theme;
    if (stage) root.dataset.stage = ""; else delete root.dataset.stage;
    root.style.setProperty("--ui-scale", String(scales[scale]));
    localStorage.setItem("wpc-theme", theme);
    localStorage.setItem("wpc-stage", stage ? "on" : "off");
    localStorage.setItem("wpc-scale", String(scale));
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "light" ? "#f5f7ff" : "#060918");
  }, [theme, stage, scale]);
  useEffect(() => {
    if (!header.current) return;
    const element = header.current;
    const observer = new ResizeObserver(() => document.documentElement.style.setProperty("--header-h", `${element.offsetHeight}px`));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    const closeOnOutside = (event: PointerEvent) => {
      const target = event.target as Node;
      if (menuRef.current && !menuRef.current.contains(target)) setMenuOpen(false);
      if (viewRef.current && !viewRef.current.contains(target)) setViewOpen(false);
    };
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setMenuOpen(false); setViewOpen(false); return; }
      if (!event.altKey || event.ctrlKey || event.metaKey) return;
      const actions: Record<string, () => void> = {
        KeyP: () => setStage((value) => !value), Equal: () => setScale((n) => Math.min(n + 1, 6)),
        NumpadAdd: () => setScale((n) => Math.min(n + 1, 6)), Minus: () => setScale((n) => Math.max(n - 1, 0)),
        NumpadSubtract: () => setScale((n) => Math.max(n - 1, 0)), Digit0: () => setScale(2),
        Numpad0: () => setScale(2), ...(onTests ? { KeyT: onTests } : {}),
      };
      if (actions[event.code]) { event.preventDefault(); actions[event.code](); }
    };
    document.addEventListener("pointerdown", closeOnOutside);
    document.addEventListener("keydown", keydown);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutside);
      document.removeEventListener("keydown", keydown);
    };
  }, [onTests]);
  const userLabel = user ? `Visualizza profilo e gruppi di ${user}${source ? ` (${source})` : ""}` : "Visualizza profilo e gruppi dell'utente collegato";
  return <header ref={header}>
    <div className="header-left">
      <a href="/" className="brand">
        <div className="logo" aria-hidden="true">
          <SparkIcon className="logo-spark" />
          <span>WPC<small>2026</small></span>
        </div>
        <div className="brand-copy">
          <h1><span className="grad">Foundry IQ</span> {page === "chat" ? "Studio" : "Trace"}</h1>
        </div>
      </a>
      <nav className="nav top-menu" aria-label="Navigazione principale">
        <div ref={menuRef} className={`menu-group ${menuOpen ? "open" : ""}`} onMouseEnter={() => { if (matchMedia("(hover: hover)").matches) setMenuOpen(true); }} onMouseLeave={() => { if (matchMedia("(hover: hover)").matches) setMenuOpen(false); }}>
          <button type="button" className={`menu-trigger ${page === "chat" ? "active" : ""}`} aria-expanded={menuOpen} aria-haspopup="true" onClick={() => setMenuOpen((v) => !v)}>
            <SparkIcon />
            <span>Chat</span>
            {page === "chat" && demo && <span className="menu-active-tag">{demoRoutes[demo].label}</span>}
            <ChevronDownIcon className="menu-chevron" />
          </button>
          <div className="demo-nav submenu" role="group" aria-label="Demo dedicate">
            <a href="/" aria-current={page === "chat" && demo === null ? "page" : undefined} onClick={() => setMenuOpen(false)}><SparkIcon />Panoramica Chat</a>
            <div className="submenu-sep" aria-hidden="true">Scenari demo</div>
            {demos.map((id) => <a key={id} href={demoRoutes[id].path} aria-current={demo === id ? "page" : undefined} onClick={() => setMenuOpen(false)}>{demoIcons[id]}{demoRoutes[id].label}</a>)}
          </div>
        </div>
        <a href="/trace" className="menu-link" aria-current={page === "trace" ? "page" : undefined}><ActivityIcon /><span>Trace</span></a>
      </nav>
    </div>
    <div className="user-nav">
      {onReset && <button type="button" className="new-chat-btn" id="reset" onClick={onReset}><PlusIcon /><span>Nuova chat</span></button>}
      {onTests && <button id="tests-toggle" type="button" className="secondary top-util-btn" aria-label={testsOpen ? "Comprimi query di test" : "Apri query di test"} aria-controls="tests" aria-expanded={testsOpen} aria-pressed={testsOpen} title="Apri o comprimi la sidebar delle query di test (Alt+T)" onClick={onTests}><PanelRightIcon /><span>Query di test</span></button>}
      <div ref={viewRef} className={`menu-group view-group ${viewOpen ? "open" : ""}`}>
        <button type="button" className="icon-button" aria-expanded={viewOpen} aria-haspopup="true" aria-label="Opzioni di visualizzazione e tema" title="Tema, dimensione testo e modalità presentazione" onClick={() => setViewOpen((v) => !v)}>
          <SlidersIcon />
        </button>
        <div className="submenu view-popover" role="group" aria-label="Presentazione">
          <div className="view-row">
            <span>Tema</span>
            <button type="button" className="secondary view-chip" aria-label={theme === "light" ? "Tema scuro" : "Tema chiaro"} onClick={() => setTheme((v) => v === "light" ? "dark" : "light")}>{theme === "light" ? <><MoonIcon />Scuro</> : <><SunIcon />Chiaro</>}</button>
          </div>
          <div className="view-row">
            <span>Testo</span>
            <div className="zoom-controls">
              <button type="button" className="icon-button" aria-label="Riduci testo" onClick={() => setScale((n) => Math.max(n - 1, 0))}>A−</button>
              <button type="button" className="icon-button scale-label" aria-label="Ripristina dimensione" onClick={() => setScale(2)}>{Math.round(scales[scale] * 100)}%</button>
              <button type="button" className="icon-button" aria-label="Ingrandisci testo" onClick={() => setScale((n) => Math.min(n + 1, 6))}>A+</button>
            </div>
          </div>
          <button id="stage-toggle" type="button" className={`secondary view-stage-btn ${stage ? "active" : ""}`} aria-pressed={stage} aria-label={stage ? "Esci dalla modalità presentazione" : "Modalità presentazione"} onClick={() => setStage((v) => !v)}><StageIcon /><span>{stage ? "Esci da Presentazione" : "Modalità Presentazione"}</span></button>
        </div>
      </div>
      {onProfile && <button type="button" className={`user icon-button user-icon-btn ${tokenOk ? "token-ok" : ""}`} aria-haspopup="dialog" aria-label={userLabel} title={user ? `${user}${source ? ` · ${source}` : ""}` : "Profilo utente"} onClick={onProfile}>
        <UserIcon />
      </button>}
    </div>
  </header>;
}

export function Dialog({ open, onClose, title, className = "", children, actions }: { open: boolean; onClose: () => void; title: string; className?: string; children?: ReactNode; actions?: ReactNode }) {
  const dialog = useRef<HTMLDialogElement | null>(null);
  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    if (open && !element.open) element.showModal();
    else if (!open && element.open) element.close();
  }, [open]);
  return <dialog ref={dialog} className={className} aria-labelledby={`${className}-title`} onClose={onClose}>
    <div className="dialog-head"><h2 id={`${className}-title`}>{title}</h2><div className="dialog-actions">{actions}<button type="button" className="secondary" onClick={onClose}>Chiudi</button></div></div>
    {children}
  </dialog>;
}

export function Profile({ config, open, onClose }: { config: ChatConfig | null; open: boolean; onClose: () => void }) {
  const p = config?.user_profile;
  const expiry = typeof p?.token_expires_on === "number" ? new Intl.DateTimeFormat("it", { dateStyle: "medium", timeStyle: "short" }).format(new Date(p.token_expires_on * 1000)) : "Non disponibile";
  const fields = p && [["Nome", p.display_name], ["Account", p.username || config.user], ["ID utente Entra", p.object_id], ["ID tenant", p.tenant_id], ["Autenticazione", p.source], ["Token Search", config.user_token_available ? "Disponibile" : "Non disponibile"], ["Scadenza token Search", expiry]];
  const hint = p?.groups_status === "overage" ? "Elenco incompleto: Entra segnala troppi gruppi per il token. Serve una lettura dedicata da Microsoft Graph; questa app non la esegue."
    : p?.groups_status === "unavailable" ? "Gruppi non disponibili nei claim: non significa che l'utente non appartenga a gruppi."
      : p?.groups?.length ? "Gruppi riportati nei claim, identificati normalmente da Object ID. I nomi non vengono risolti tramite Graph."
        : "Il claim dei gruppi è presente ma vuoto. Non equivale a una verifica completa nella directory.";
  return <Dialog open={open} onClose={onClose} className="profile-dialog" title="Identità e sessione utente">
    {!p ? <p>Profilo non disponibile: {config?.user_source || "accedi di nuovo"}.</p> : <>
      <div className="profile-banner">
        <span className="avatar avatar-lg" aria-hidden="true">{((p.display_name || config.user || "?").trim()[0] || "?").toUpperCase()}</span>
        <div className="profile-summary">
          <strong>{p.display_name || config.user || "Utente collegato"}</strong>
          <span>{p.username || config.user || "Account non disponibile"}</span>
          <div className="profile-pills">
            <span className={`badge ${config.user_token_available ? "acl" : "classic"}`}>{config.user_token_available ? "Token Search attivo" : "Token Search assente"}</span>
            {p.source && <span className="badge noacl">{p.source}</span>}
          </div>
        </div>
      </div>
      <dl className="profile-data">{fields?.map(([label, value]) => <div key={label} className="profile-field"><dt>{label}</dt><dd>{value || "Non disponibile"}</dd></div>)}</dl>
      <div className="profile-section">
        <h3><ShieldIcon />Gruppi Entra</h3>
        <p className="meta">{hint}</p>
        {p.groups.length > 0 && <ul className="profile-groups">{p.groups.map((group) => <li key={group}><ShieldIcon /><code>{group}</code></li>)}</ul>}
      </div>
      {p.roles.length > 0 && <div className="profile-section"><h3><LockIcon />Ruoli applicativi (non gruppi)</h3><ul className="profile-groups">{p.roles.map((role) => <li key={role}><code>{role}</code></li>)}</ul></div>}
      <p className="meta profile-footnote">Dati informativi della sessione: non autorizzano l'accesso ai documenti. Le ACL sono verificate dal servizio con il token utente.</p>
    </>}
  </Dialog>;
}
