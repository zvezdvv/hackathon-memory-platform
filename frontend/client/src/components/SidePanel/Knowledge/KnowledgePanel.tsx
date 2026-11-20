/**
 * Smart City: "Memory" tab as a knowledge file system.
 *
 * Replaces the built-in Memories panel. Data comes from the knowledge server (service
 * "knowledge"): real folders, automatic RAG index, viewer with highlighted passages and
 * three inputs (files, speech, interview).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { ReactNode } from 'react';
import {
  AlertTriangle,
  ArrowLeft,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  FileText,
  Folder,
  FolderOpen,
  FolderPlus,
  History,
  Loader2,
  Maximize2,
  PanelLeft,
  MessagesSquare,
  Mic,
  RefreshCw,
  Search,
  Square,
  Trash2,
  Upload,
  X,
} from 'lucide-react';
import { getTokenHeader } from 'librechat-data-provider';
import OpenSidebar from '~/components/Chat/Menus/OpenSidebar';
import useSidebarState from '~/hooks/Nav/useSidebarState';
import { DEFAULT_PANEL, MEMORY_PANE_ID, useActivePanel } from '~/Providers';
import { useLocalize } from '~/hooks';
import { cn } from '~/utils';

/* ------------------------------------------------------------------ API */

/**
 * Behind the HTTPS proxy (Caddy) the knowledge server lives under /knowledge on the same domain.
 * Locally without the proxy (LibreChat directly on :3080) it is reached on port 8100.
 */
const KB_URL =
  window.location.port === '3080'
    ? `${window.location.protocol}//${window.location.hostname}:8100`
    : `${window.location.origin}/knowledge`;
const KB_ORIGIN = new URL(KB_URL).origin;
const FILE_ACCEPT = '.pdf,.md,.markdown,.txt';
const FILE_EXTENSION = /\.(pdf|md|markdown|txt)$/i;
const DOCUMENT_STATUS = {
  active: 'com_ui_memory_status_active',
  inactive: 'com_ui_memory_status_inactive',
  draft: 'com_ui_memory_status_draft',
} as const;
type DocumentStatus = keyof typeof DOCUMENT_STATUS;
type PendingUpload = { file: File; status: DocumentStatus };

type TreeNode = {
  name: string;
  path: string;
  type: 'dir' | 'file';
  children?: TreeNode[];
  status?: 'indexed' | 'text_only' | 'pending' | 'error';
  error?: string;
};
type SearchHit = {
  path: string;
  title: string;
  snippet: string;
  mode: string;
  page?: number;
  doc_id?: string;
  updated_by?: { path: string; id: string; datum: string }[];
};
type Doc = {
  path: string;
  folder: string;
  name: string;
  ext: string;
  size: number;
  mtime: number;
  status: 'indexed' | 'text_only' | 'pending' | 'error' | 'excluded';
  document_status: DocumentStatus;
  id: string;
  title: string;
  typ: string;
  person: string;
  rolle: string;
  datum: string;
  date_source: 'meta' | 'file';
  ort: string;
  tags: string[];
  thema: string;
  updated_by: string[];
  updates: string[];
};
type Person = { slug: string; name: string; sessions: number };
type Guide = { path: string; title: string; beschreibung: string };

async function kb<T>(path: string, init?: RequestInit): Promise<T> {
  // The knowledge server checks write access against the LibreChat login (access token)
  const headers = new Headers(init?.headers);
  const token = getTokenHeader();
  if (token) headers.set('Authorization', token);
  const res = await fetch(`${KB_URL}${path}`, { ...init, headers });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new Error(String(detail));
  }
  return res.json() as Promise<T>;
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

const TOP = ['files', 'speech', 'interviews', '_guides'];
const FOLDER_LABEL: Record<string, string> = {
  files: 'Files',
  speech: 'Voice notes',
  interviews: 'Interviews',
  _guides: 'Interview guides',
};

const STATUS: Record<string, { cls: string; label: string }> = {
  indexed: { cls: 'bg-green-500', label: 'Indexed in RAG' },
  text_only: { cls: 'bg-amber-400', label: 'Keyword search only (no embedding)' },
  pending: { cls: 'bg-blue-400 animate-pulse', label: 'Indexing …' },
  error: { cls: 'bg-red-500', label: 'Indexing failed' },
  excluded: { cls: 'bg-border-heavy', label: 'Excluded from search' },
};

function countFiles(n?: TreeNode): { total: number; indexed: number; pending: number } {
  const acc = { total: 0, indexed: 0, pending: 0 };
  const walk = (x: TreeNode) => {
    if (x.type === 'file') {
      acc.total += 1;
      if (x.status === 'indexed') acc.indexed += 1;
      if (x.status === 'pending') acc.pending += 1;
    }
    x.children?.forEach(walk);
  };
  if (n) walk(n);
  return acc;
}

/* ------------------------------------------------------------------ UI helpers */

function Modal({
  title,
  onClose,
  children,
  wide = false,
  actions,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
  actions?: ReactNode;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title}
      className="fixed inset-0 z-[200] flex items-center justify-center bg-black/50 p-2 sm:p-6"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        className={cn(
          'flex max-h-full w-full flex-col overflow-hidden rounded-xl border border-border-medium bg-surface-primary shadow-2xl',
          wide ? 'h-full max-w-6xl' : 'max-w-lg',
        )}
      >
        <div className="flex items-center gap-2 border-b border-border-light px-4 py-2.5">
          <h2 className="min-w-0 flex-1 truncate text-sm font-semibold text-text-primary">{title}</h2>
          {actions}
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1 text-text-secondary hover:bg-surface-hover"
            aria-label="Close"
          >
            <X className="size-4" />
          </button>
        </div>
        <div className={cn('min-h-0 flex-1', wide ? '' : 'overflow-y-auto p-4')}>{children}</div>
      </div>
    </div>,
    document.body,
  );
}

const inputCls =
  'w-full rounded-lg border border-border-medium bg-surface-secondary px-3 py-2 text-sm text-text-primary placeholder:text-text-tertiary focus:border-border-heavy focus:outline-none';
const btnCls =
  'inline-flex items-center justify-center gap-1.5 rounded-lg border border-border-medium bg-surface-secondary px-3 py-1.5 text-sm text-text-primary hover:bg-surface-hover disabled:opacity-50';
const primaryCls =
  'inline-flex items-center justify-center gap-1.5 rounded-lg bg-green-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-green-700 disabled:opacity-50';

function StatusSelect({
  value,
  onChange,
  label,
  disabled,
}: {
  value: DocumentStatus;
  onChange: (status: DocumentStatus) => void;
  label?: string;
  disabled?: boolean;
}) {
  const localize = useLocalize();
  return (
    <label className="flex flex-col gap-1 text-xs font-medium text-text-secondary">
      {!label && <span>{localize('com_ui_memory_document_status')}</span>}
      <select
        aria-label={label}
        className={cn(inputCls, 'py-1.5')}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value as DocumentStatus)}
      >
        {Object.entries(DOCUMENT_STATUS).map(([status, key]) => (
          <option key={status} value={status}>{localize(key)}</option>
        ))}
      </select>
    </label>
  );
}

/* ------------------------------------------------------------------ Viewer */

function Viewer({ path, hl, onClose }: { path: string; hl?: string; onClose: () => void }) {
  const src = `${KB_URL}/view?path=${encodeURIComponent(path)}&embed=1${
    hl ? `&hl=${encodeURIComponent(hl.slice(0, 300))}` : ''
  }`;
  return (
    <Modal
      title={path}
      onClose={onClose}
      wide
      actions={
        <a
          href={src.replace('&embed=1', '')}
          target="_blank"
          rel="noreferrer"
          className="rounded-md p-1 text-text-secondary hover:bg-surface-hover"
          title="Open in new tab"
        >
          <ExternalLink className="size-4" />
        </a>
      }
    >
      <iframe title={path} src={src} className="size-full border-0 bg-surface-primary" />
    </Modal>
  );
}

/* ------------------------------------------------------------------ Speech */

type SpeechRec = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  start: () => void;
  stop: () => void;
  onresult: ((e: { resultIndex: number; results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }> }) => void) | null;
  onend: (() => void) | null;
  onerror: ((e: { error: string }) => void) | null;
};

function SpeechDialog({ onClose, onSaved }: { onClose: () => void; onSaved: (path: string) => void }) {
  const [title, setTitle] = useState('');
  const [person, setPerson] = useState('');
  const [status, setStatus] = useState<DocumentStatus>('active');
  const [text, setText] = useState('');
  const [interim, setInterim] = useState('');
  const [recording, setRecording] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const recRef = useRef<SpeechRec | null>(null);

  const Ctor = (window as unknown as Record<string, unknown>).SpeechRecognition ??
    (window as unknown as Record<string, unknown>).webkitSpeechRecognition;
  const supported = typeof Ctor === 'function';

  const start = () => {
    if (!supported) return;
    setError('');
    const rec = new (Ctor as new () => SpeechRec)();
    rec.lang = navigator.language || 'en-US';
    rec.continuous = true;
    rec.interimResults = true;
    rec.onresult = (e) => {
      let finalText = '';
      let interimText = '';
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) finalText += r[0].transcript;
        else interimText += r[0].transcript;
      }
      if (finalText) setText((t) => (t ? `${t.trimEnd()} ${finalText.trim()}` : finalText.trim()));
      setInterim(interimText);
    };
    rec.onerror = (e) => setError(`Speech recognition: ${e.error}`);
    rec.onend = () => {
      setRecording(false);
      setInterim('');
    };
    recRef.current = rec;
    rec.start();
    setRecording(true);
  };
  const stop = () => recRef.current?.stop();
  useEffect(() => () => recRef.current?.stop(), []);

  const save = async () => {
    setSaving(true);
    setError('');
    try {
      const res = await kb<{ path: string }>('/api/note', json('POST', { title, person, text, document_status: status }));
      onSaved(res.path);
    } catch (e) {
      setError(String((e as Error).message));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title="Record a voice note" onClose={onClose}>
      <div className="flex flex-col gap-3">
        <input className={inputCls} placeholder="Title, e.g. “Gritting plan Moritzberg”" value={title} onChange={(e) => setTitle(e.target.value)} />
        <input className={inputCls} placeholder="Person (optional)" value={person} onChange={(e) => setPerson(e.target.value)} />
        <StatusSelect value={status} onChange={setStatus} disabled={saving} />
        <div className="flex items-center gap-2">
          {recording ? (
            <button type="button" className={cn(btnCls, 'border-red-500 text-red-500')} onClick={stop}>
              <Square className="size-4 fill-current" /> Stop recording
            </button>
          ) : (
            <button type="button" className={btnCls} onClick={start} disabled={!supported}>
              <Mic className="size-4" /> Start recording
            </button>
          )}
          {recording && <span className="flex items-center gap-1.5 text-xs text-red-500"><span className="size-2 animate-pulse rounded-full bg-red-500" />listening …</span>}
        </div>
        {!supported && (
          <p className="text-xs text-text-secondary">
            This browser does not support speech recognition (use Chrome or Edge). You can also type or paste the text.
          </p>
        )}
        <textarea
          className={cn(inputCls, 'min-h-40 resize-y')}
          placeholder="The transcript appears here – you can correct it before saving."
          value={interim ? `${text} ${interim}` : text}
          onChange={(e) => setText(e.target.value)}
          readOnly={recording}
        />
        {error && <p className="text-xs text-red-500">{error}</p>}
        <div className="flex justify-end gap-2">
          <button type="button" className={btnCls} onClick={onClose}>Cancel</button>
          <button type="button" className={primaryCls} onClick={save} disabled={saving || recording || !text.trim()}>
            {saving && <Loader2 className="size-4 animate-spin" />} Save to speech/
          </button>
        </div>
      </div>
    </Modal>
  );
}

/* ------------------------------------------------------------------ Interview */

function InterviewDialog({ onClose }: { onClose: () => void }) {
  const [people, setPeople] = useState<Person[]>([]);
  const [guides, setGuides] = useState<Guide[]>([]);
  const [person, setPerson] = useState('');
  const [topic, setTopic] = useState('');
  const [guide, setGuide] = useState('');
  const [status, setStatus] = useState<DocumentStatus>('active');

  useEffect(() => {
    kb<Person[]>('/api/people').then(setPeople).catch(() => setPeople([]));
    kb<Guide[]>('/api/guides')
      .then((g) => {
        const usable = g.filter((x) => !x.path.includes('00_'));
        setGuides(usable);
        setGuide(usable[0]?.path ?? '');
      })
      .catch(() => setGuides([]));
  }, []);

  const start = () => {
    const prompt = [
      'Start the knowledge interview.',
      `Person: ${person.trim()}`,
      `Topic: ${topic.trim()}`,
      guide ? `Guide: ${guide}` : '',
      `Document status: ${status}`,
    ]
      .filter(Boolean)
      .join('\n');
    window.location.assign(
      `/c/new?spec=wissens-interview&prompt=${encodeURIComponent(prompt)}&submit=true`,
    );
  };

  return (
    <Modal title="Start a knowledge interview" onClose={onClose}>
      <div className="flex flex-col gap-3">
        <p className="text-xs text-text-secondary">
          The interview assistant reads the guide, checks what the knowledge base already holds about the person and asks targeted questions about the gaps. The protocol is saved under <code>interviews/&lt;person&gt;/</code>.
        </p>
        <label className="text-xs font-medium text-text-secondary">Person</label>
        <input className={inputCls} list="kb-people" placeholder="e.g. Klaus Behrens" value={person} onChange={(e) => setPerson(e.target.value)} />
        <datalist id="kb-people">
          {people.map((p) => <option key={p.slug} value={p.name}>{`${p.sessions} session(s)`}</option>)}
        </datalist>
        <label className="text-xs font-medium text-text-secondary">Topic</label>
        <input className={inputCls} placeholder="e.g. Black ice operations at night" value={topic} onChange={(e) => setTopic(e.target.value)} />
        <label className="text-xs font-medium text-text-secondary">Guide</label>
        <select className={inputCls} value={guide} onChange={(e) => setGuide(e.target.value)}>
          {guides.map((g) => <option key={g.path} value={g.path}>{g.title}</option>)}
          <option value="">Basic rules only</option>
        </select>
        <StatusSelect value={status} onChange={setStatus} />
        <div className="flex justify-end gap-2 pt-1">
          <button type="button" className={btnCls} onClick={onClose}>Cancel</button>
          <button type="button" className={primaryCls} onClick={start} disabled={!person.trim() || !topic.trim()}>
            <MessagesSquare className="size-4" /> Start interview
          </button>
        </div>
      </div>
    </Modal>
  );
}

/* ------------------------------------------------------------------ Overlay */

const TYP_STYLE: Record<string, string> = {
  Interview: 'bg-purple-500/15 text-purple-600 dark:text-purple-300',
  Note: 'bg-amber-500/15 text-amber-700 dark:text-amber-300',
  Law: 'bg-blue-500/15 text-blue-700 dark:text-blue-300',
  PDF: 'bg-red-500/15 text-red-700 dark:text-red-300',
  Document: 'bg-slate-500/15 text-text-secondary',
  'Voice note': 'bg-green-500/15 text-green-700 dark:text-green-300',
  Guide: 'bg-teal-500/15 text-teal-700 dark:text-teal-300',
};

type SortKey = 'newest' | 'oldest' | 'title' | 'typ' | 'person';
type GroupKey = 'none' | 'typ' | 'person' | 'folder' | 'month';

const SORTS: { key: SortKey; label: string }[] = [
  { key: 'newest', label: 'Newest first' },
  { key: 'oldest', label: 'Oldest first' },
  { key: 'title', label: 'Title A–Z' },
  { key: 'typ', label: 'Type' },
  { key: 'person', label: 'Person' },
];
const GROUPS: { key: GroupKey; label: string }[] = [
  { key: 'none', label: 'No grouping' },
  { key: 'typ', label: 'Group by type' },
  { key: 'person', label: 'Group by person' },
  { key: 'folder', label: 'Group by folder' },
  { key: 'month', label: 'Group by month' },
];

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];

function fmtDate(d: string) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(d);
  return m ? `${m[1]}-${m[2]}-${m[3]}` : d;
}

function folderLabel(path: string) {
  if (!path) return 'All documents';
  const top = path.split('/')[0];
  return path === top ? FOLDER_LABEL[top] ?? top : path.split('/').slice(-1)[0];
}

function groupOf(d: Doc, g: GroupKey): string {
  if (g === 'typ') return d.typ;
  if (g === 'person') return d.person || 'No person';
  if (g === 'folder') return d.folder || '/';
  if (g === 'month') {
    const m = /^(\d{4})-(\d{2})/.exec(d.datum);
    return m ? `${MONTHS[Number(m[2]) - 1]} ${m[1]}` : 'No date';
  }
  return '';
}

function sortDocs(list: Doc[], key: SortKey): Doc[] {
  const out = [...list];
  const byDate = (a: Doc, b: Doc) => a.datum.localeCompare(b.datum) || a.mtime - b.mtime;
  if (key === 'newest') out.sort((a, b) => byDate(b, a));
  if (key === 'oldest') out.sort(byDate);
  if (key === 'title') out.sort((a, b) => a.title.localeCompare(b.title, 'de'));
  if (key === 'typ') out.sort((a, b) => a.typ.localeCompare(b.typ, 'de') || byDate(b, a));
  if (key === 'person') out.sort((a, b) => (a.person || '~').localeCompare(b.person || '~', 'de') || byDate(b, a));
  return out;
}

function Chip({
  active,
  onClick,
  children,
  count,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
  count?: number;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'flex w-full items-center gap-2 rounded-md px-2 py-1 text-left text-sm',
        active ? 'bg-surface-active-alt font-medium text-text-primary' : 'text-text-secondary hover:bg-surface-hover',
      )}
    >
      <span className="min-w-0 flex-1 truncate">{children}</span>
      {count != null && <span className="text-xs text-text-tertiary">{count}</span>}
    </button>
  );
}

export default function KnowledgePanel() {
  const localize = useLocalize();
  const { isSmallScreen, expanded } = useSidebarState();
  const { setActive } = useActivePanel();
  const onClose = useCallback(() => setActive(DEFAULT_PANEL), [setActive]);
  const [docs, setDocs] = useState<Doc[] | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const [folder, setFolder] = useState('');
  const [typ, setTyp] = useState('');
  const [person, setPerson] = useState('');
  const [sort, setSort] = useState<SortKey>('newest');
  const [group, setGroup] = useState<GroupKey>('none');
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [selected, setSelected] = useState<{ path: string; hl?: string } | null>(null);
  const [dialog, setDialog] = useState<'upload' | 'speech' | 'interview' | null>(null);
  const [uploads, setUploads] = useState<PendingUpload[]>([]);
  const [uploadError, setUploadError] = useState('');
  const [statusSaving, setStatusSaving] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [showFilters, setShowFilters] = useState(!isSmallScreen);
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      setDocs(await kb<Doc[]>('/api/documents'));
      setError('');
    } catch {
      setError(`Knowledge base not reachable (${KB_URL}).`);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const pending = docs?.some((d) => d.status === 'pending') ?? false;
  useEffect(() => {
    const t = window.setInterval(load, pending ? 3000 : 20000);
    return () => window.clearInterval(t);
  }, [load, pending]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || (isSmallScreen && expanded)) return;
      e.preventDefault();
      if (!dialog) onClose();
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [onClose, dialog, isSmallScreen, expanded]);

  // The embedded viewer asks for the login token when saving
  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      if (e.origin !== KB_ORIGIN || e.data?.type !== 'kb-token-request') return;
      (e.source as Window | null)?.postMessage({ type: 'kb-token', token: getTokenHeader() ?? '' }, e.origin);
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, []);

  // Search (debounced)
  useEffect(() => {
    const q = query.trim();
    if (!q) {
      setHits(null);
      return;
    }
    setSearching(true);
    const t = window.setTimeout(async () => {
      try {
        const res = await kb<{ results: SearchHit[] }>(
          `/api/search?q=${encodeURIComponent(q)}&k=12${folder ? `&folder=${encodeURIComponent(folder)}` : ''}`,
        );
        setHits(res.results);
      } catch {
        setHits([]);
      } finally {
        setSearching(false);
      }
    }, 350);
    return () => window.clearTimeout(t);
  }, [query, folder]);

  const all = docs ?? [];
  const byPath = useMemo(() => new Map(all.map((d) => [d.path, d])), [all]);
  const visibleHits = hits?.filter((hit) => (byPath.get(hit.path)?.document_status ?? 'active') === 'active');

  const folders = useMemo(() => {
    const set = new Map<string, number>();
    for (const d of all) {
      const parts = d.folder.split('/');
      for (let i = 1; i <= parts.length; i++) {
        const f = parts.slice(0, i).join('/');
        if (f) set.set(f, (set.get(f) ?? 0) + 1);
      }
    }
    for (const t of TOP) if (!set.has(t)) set.set(t, 0);
    const order = (f: string) => TOP.indexOf(f.split('/')[0]);
    return [...set.entries()].sort((a, b) => order(a[0]) - order(b[0]) || a[0].localeCompare(b[0]));
  }, [all]);

  const inFolder = useMemo(
    () => all.filter((d) => !folder || d.folder === folder || d.folder.startsWith(`${folder}/`)),
    [all, folder],
  );
  const count = (key: 'typ' | 'person') => {
    const m = new Map<string, number>();
    for (const d of inFolder) {
      const v = key === 'typ' ? d.typ : d.person;
      if (v) m.set(v, (m.get(v) ?? 0) + 1);
    }
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  };
  const types = count('typ');
  const people = count('person');

  const filtered = useMemo(
    () => sortDocs(inFolder.filter((d) => (!typ || d.typ === typ) && (!person || d.person === person)), sort),
    [inFolder, typ, person, sort],
  );
  const grouped = useMemo(() => {
    if (group === 'none') return [['', filtered]] as [string, Doc[]][];
    const m = new Map<string, Doc[]>();
    for (const d of filtered) {
      const g = groupOf(d, group);
      m.set(g, [...(m.get(g) ?? []), d]);
    }
    return [...m.entries()];
  }, [filtered, group]);

  const stats = {
    total: all.length,
    indexed: all.filter((d) => d.status === 'indexed').length,
  };
  const target = folder && !folder.startsWith('_guides') ? folder : 'files';

  const chooseFiles = (files: FileList) => {
    const chosen = Array.from(files);
    if (!chosen.length || busy) return;
    if (chosen.some((file) => !FILE_EXTENSION.test(file.name))) {
      setError(localize('com_ui_memory_file_types'));
      return;
    }
    setError('');
    setUploadError('');
    setUploads(chosen.map((file) => ({ file, status: 'active' })));
    setDialog('upload');
  };

  const upload = async () => {
    setBusy(localize('com_ui_memory_uploading'));
    setUploadError('');
    try {
      const fd = new FormData();
      fd.append('folder', target);
      uploads.forEach(({ file, status }) => {
        fd.append('files', file);
        fd.append('document_statuses', status);
      });
      const res = await kb<{ saved: string[] }>('/api/upload', { method: 'POST', body: fd });
      await load();
      setDialog(null);
      setUploads([]);
      if (res.saved[0]) setSelected({ path: res.saved[0] });
    } catch (e) {
      setUploadError((e as Error).message);
    } finally {
      setBusy('');
    }
  };

  const changeStatus = async (path: string, status: DocumentStatus) => {
    setStatusSaving(true);
    try {
      await kb('/api/document-status', json('PATCH', { path, document_status: status }));
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setStatusSaving(false);
    }
  };

  const remove = async (path: string) => {
    if (!window.confirm(`Delete “${path}”? This cannot be undone.`)) return;
    try {
      await kb(`/api/item?path=${encodeURIComponent(path)}`, { method: 'DELETE' });
      setSelected(null);
      await load();
    } catch (e) {
      setError(String((e as Error).message));
    }
  };

  const newFolder = async () => {
    const parent = folder || 'files';
    const name = window.prompt(`New folder in “${parent}”:`);
    if (!name?.trim()) return;
    try {
      const res = await kb<{ path: string }>('/api/folder', json('POST', { path: `${parent}/${name.trim()}` }));
      setFolder(res.path);
      await load();
    } catch (e) {
      setError(String((e as Error).message));
    }
  };

  const sel = selected ? byPath.get(selected.path) : undefined;
  const viewSrc = selected
    ? `${KB_URL}/view?path=${encodeURIComponent(selected.path)}&embed=1${
        selected.hl ? `&hl=${encodeURIComponent(selected.hl.slice(0, 300))}` : ''
      }`
    : '';

  // Compact rows (type/person/date under the title) on small screens and while reading
  const compact = selected != null;
  const Row = ({ d }: { d: Doc }) => (
    <button
      type="button"
      onClick={() => setSelected({ path: d.path })}
      className={cn(
        'grid w-full grid-cols-[auto_1fr] gap-x-3 border-b border-border-light px-4 py-2.5 text-left hover:bg-surface-hover',
        !compact && 'md:grid-cols-[auto_minmax(0,1fr)_6rem_6rem_8rem_6rem]',
        selected?.path === d.path && 'bg-surface-active-alt',
      )}
    >
      <span className={cn('mt-1.5 size-2 rounded-full', STATUS[d.status]?.cls)} title={STATUS[d.status]?.label} />
      <span className="min-w-0">
        <span className="flex items-center gap-1.5">
          {d.id && <span className="rounded bg-surface-tertiary px-1 text-[11px] font-semibold text-text-secondary">{d.id}</span>}
          <span className="truncate text-sm font-medium text-text-primary">{d.title}</span>
        </span>
        <span className={cn('mt-0.5 flex flex-wrap items-center gap-1 text-[11px] text-text-secondary', !compact && 'md:hidden')}>
          <span className={cn('rounded-full px-1.5', TYP_STYLE[d.typ] ?? TYP_STYLE.Document)}>{d.typ}</span>
          <span>{localize(DOCUMENT_STATUS[d.document_status ?? 'active'])}</span>
          {d.person && <span>{d.person}</span>}
          <span>· {fmtDate(d.datum)}</span>
        </span>
        <span className="mt-0.5 flex flex-wrap items-center gap-1 text-[11px] text-text-tertiary">
          <span className="truncate">{d.folder}</span>
          {d.tags.slice(0, 3).map((t) => (
            <span key={t} className="rounded-full border border-border-light px-1.5">#{t}</span>
          ))}
          {d.updated_by.length > 0 && (
            <span className="flex items-center gap-0.5 rounded-full bg-amber-500/15 px-1.5 text-amber-700 dark:text-amber-300" title={`Updated by ${d.updated_by.join(', ')}`}>
              <AlertTriangle className="size-3" /> updated by {d.updated_by.map((p) => byPath.get(p)?.id || p.split('/').pop()).join(', ')}
            </span>
          )}
          {d.updates.length > 0 && (
            <span className="flex items-center gap-0.5 rounded-full bg-green-500/15 px-1.5 text-green-700 dark:text-green-300">
              <History className="size-3" /> newer version of {d.updates.join(', ')}
            </span>
          )}
        </span>
      </span>
      <span className={cn('hidden', !compact && 'md:block')}>
        <span className={cn('rounded-full px-2 py-0.5 text-[11px] font-medium', TYP_STYLE[d.typ] ?? TYP_STYLE.Document)}>{d.typ}</span>
      </span>
      <span className={cn('hidden truncate text-xs text-text-secondary', !compact && 'md:block')}>
        {localize(DOCUMENT_STATUS[d.document_status ?? 'active'])}
      </span>
      <span className={cn('hidden truncate text-xs text-text-secondary', !compact && 'md:block')} title={d.rolle}>{d.person || '–'}</span>
      <span className={cn('hidden text-xs', !compact && 'md:block', d.date_source === 'file' ? 'text-text-tertiary' : 'text-text-secondary')} title={d.date_source === 'file' ? 'File date (no date in the document)' : 'Date stated in the document'}>
        {fmtDate(d.datum)}
      </span>
    </button>
  );

  const pane = document.getElementById(MEMORY_PANE_ID);
  if (!pane) return null;

  return createPortal(
    <div
      role="region"
      aria-labelledby="memory-overlay-title"
      className="relative flex size-full flex-col bg-surface-primary"
      onDragOver={(e) => {
        if (!e.dataTransfer.types.includes('Files')) return;
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={(e) => e.currentTarget === e.target && setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        if (e.dataTransfer.files.length) chooseFiles(e.dataTransfer.files);
      }}
    >
      {/* Kopfzeile */}
      <header className="flex flex-wrap items-center gap-3 border-b border-border-light px-4 py-2.5">
        {isSmallScreen && <OpenSidebar testId="memory-open-sidebar" />}
        <button
          type="button"
          onClick={() => setShowFilters((v) => !v)}
          className={cn('rounded-md p-1.5 text-text-secondary hover:bg-surface-hover', showFilters && 'bg-surface-active-alt')}
          title={localize('com_ui_memory_toggle_filters')}
          aria-label={localize('com_ui_memory_toggle_filters')}
          aria-pressed={showFilters}
        >
          <PanelLeft className="size-5" />
        </button>
        <div className="mr-2">
          <h1 id="memory-overlay-title" className="text-base font-semibold text-text-primary">
            Memory
          </h1>
          <p className="text-xs text-text-secondary">
            {docs ? `${stats.total} documents · ${stats.indexed} indexed in RAG` : 'loading …'}
          </p>
        </div>
        <div className="relative min-w-48 flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 size-4 text-text-tertiary" />
          <input
            className={cn(inputCls, 'pl-8 pr-8')}
            placeholder={folder ? `Search in “${folderLabel(folder)}” …` : 'Search all knowledge …'}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            autoFocus
          />
          {query && (
            <button type="button" className="absolute right-2 top-2.5 text-text-tertiary hover:text-text-primary" onClick={() => setQuery('')} aria-label="Clear search">
              <X className="size-4" />
            </button>
          )}
        </div>
        <div className="flex gap-1.5">
          <button type="button" className={btnCls} onClick={() => fileInput.current?.click()}>
            <Upload className="size-4" /> Files
          </button>
          <button type="button" className={btnCls} onClick={() => setDialog('speech')}>
            <Mic className="size-4" /> Speech
          </button>
          <button type="button" className={btnCls} onClick={() => setDialog('interview')}>
            <MessagesSquare className="size-4" /> Interview
          </button>
        </div>
        <button type="button" onClick={load} className="rounded-md p-1.5 text-text-secondary hover:bg-surface-hover" title="Refresh">
          <RefreshCw className="size-4" />
        </button>
        <button type="button" onClick={onClose} className="rounded-md p-1.5 text-text-secondary hover:bg-surface-hover" title="Close (Esc)" aria-label="Close Memory">
          <X className="size-5" />
        </button>
        <input
          ref={fileInput}
          type="file"
          accept={FILE_ACCEPT}
          multiple
          className="hidden"
          onChange={(e) => {
            if (e.target.files) chooseFiles(e.target.files);
            e.target.value = '';
          }}
        />
      </header>

      {(busy || error) && (
        <div className={cn('px-4 py-1.5 text-xs', error ? 'bg-red-500/10 text-red-500' : 'bg-surface-secondary text-text-secondary')}>
          {busy && <Loader2 className="mr-1 inline size-3 animate-spin" />}
          {error || busy}
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        {/* Filter */}
        <aside className={cn('w-60 shrink-0 flex-col gap-4 overflow-y-auto border-r border-border-light p-3', showFilters ? 'flex' : 'hidden')}>
          <section>
            <div className="mb-1 flex items-center justify-between px-2">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary">Folders</h3>
              <button type="button" onClick={newFolder} className="text-text-tertiary hover:text-text-primary" title="New folder" aria-label="New folder">
                <FolderPlus className="size-3.5" />
              </button>
            </div>
            <Chip active={!folder} onClick={() => setFolder('')} count={all.length}>All documents</Chip>
            {folders.map(([f, n]) => (
              <div key={f} style={{ paddingLeft: `${(f.split('/').length - 1) * 12}px` }}>
                <Chip active={folder === f} onClick={() => setFolder(f)} count={n}>
                  <span className="inline-flex items-center gap-1.5">
                    <Folder className="size-3.5 text-amber-500" /> {folderLabel(f)}
                  </span>
                </Chip>
              </div>
            ))}
          </section>
          <section>
            <h3 className="mb-1 px-2 text-xs font-semibold uppercase tracking-wide text-text-tertiary">Type</h3>
            <Chip active={!typ} onClick={() => setTyp('')}>All types</Chip>
            {types.map(([t, n]) => (
              <Chip key={t} active={typ === t} onClick={() => setTyp(typ === t ? '' : t)} count={n}>{t}</Chip>
            ))}
          </section>
          {people.length > 0 && (
            <section>
              <h3 className="mb-1 px-2 text-xs font-semibold uppercase tracking-wide text-text-tertiary">Person</h3>
              <Chip active={!person} onClick={() => setPerson('')}>All people</Chip>
              {people.map(([p, n]) => (
                <Chip key={p} active={person === p} onClick={() => setPerson(person === p ? '' : p)} count={n}>{p}</Chip>
              ))}
            </section>
          )}
        </aside>

        {/* Liste */}
        <main className={cn('min-w-0 flex-col', selected ? 'hidden w-[22rem] flex-none md:flex' : 'flex flex-1')}>
          {!visibleHits ? (
            <>
              <div className="flex flex-wrap items-center gap-2 border-b border-border-light px-4 py-2 text-xs text-text-secondary">
                <span className="mr-auto">
                  {filtered.length} document{filtered.length === 1 ? '' : 's'}
                  {folder && ` in “${folderLabel(folder)}”`}
                  {typ && ` · ${typ}`}
                  {person && ` · ${person}`}
                </span>
                <select className="rounded-md border border-border-light bg-surface-secondary px-2 py-1 text-xs text-text-primary" value={sort} onChange={(e) => setSort(e.target.value as SortKey)} aria-label="Sort">
                  {SORTS.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
                </select>
                <select className="rounded-md border border-border-light bg-surface-secondary px-2 py-1 text-xs text-text-primary" value={group} onChange={(e) => setGroup(e.target.value as GroupKey)} aria-label="Group">
                  {GROUPS.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
                </select>
              </div>
              <div className={cn('hidden grid-cols-[auto_minmax(0,1fr)_6rem_6rem_8rem_6rem] gap-x-3 border-b border-border-light px-4 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-text-tertiary', !compact && 'md:grid')}>
                <span className="w-2" />
                <span>Title</span>
                <span>Type</span>
                <span>{localize('com_ui_memory_document_status')}</span>
                <span>Person</span>
                <span>Date</span>
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto">
                {grouped.map(([g, list]) => (
                  <div key={g || 'all'}>
                    {g && (
                      <div className="sticky top-0 z-10 border-b border-border-light bg-surface-secondary px-4 py-1 text-xs font-semibold text-text-secondary">
                        {g} <span className="font-normal text-text-tertiary">({list.length})</span>
                      </div>
                    )}
                    {list.map((d) => <Row key={d.path} d={d} />)}
                  </div>
                ))}
                {docs && filtered.length === 0 && (
                  <p className="p-6 text-center text-sm text-text-secondary">No documents match this selection.</p>
                )}
              </div>
            </>
          ) : (
            <div className="min-h-0 flex-1 overflow-y-auto p-4">
              <p className="mb-3 text-xs text-text-secondary">{searching ? 'Searching …' : `${visibleHits.length} passages for “${query}”`}</p>
              <div className="flex flex-col gap-2">
                {visibleHits.map((h, i) => {
                  const d = byPath.get(h.path);
                  return (
                    <button
                      key={`${h.path}-${i}`}
                      type="button"
                      onClick={() => setSelected({ path: h.path, hl: h.snippet })}
                      className={cn('rounded-lg border border-border-light bg-surface-secondary p-3 text-left hover:border-border-heavy', selected?.path === h.path && 'border-border-heavy')}
                    >
                      <div className="flex items-center gap-1.5 text-sm font-medium text-text-primary">
                        {d?.id && <span className="rounded bg-surface-tertiary px-1 text-[11px] font-semibold text-text-secondary">{d.id}</span>}
                        <span className="truncate">{d?.title ?? h.path}</span>
                        {d && <span className={cn('ml-auto shrink-0 rounded-full px-2 py-0.5 text-[11px]', TYP_STYLE[d.typ] ?? TYP_STYLE.Document)}>{d.typ}</span>}
                      </div>
                      <div className="mt-0.5 text-[11px] text-text-tertiary">
                        {h.path}
                        {d?.datum && ` · ${fmtDate(d.datum)}`}
                        {d?.person && ` · ${d.person}`}
                      </div>
                      {h.updated_by?.length ? (
                        <div className="mt-1 flex items-center gap-1 text-[11px] text-amber-700 dark:text-amber-300">
                          <AlertTriangle className="size-3" /> Newer version: {h.updated_by.map((u) => u.id || u.path).join(', ')}
                        </div>
                      ) : null}
                      <p className="mt-1.5 line-clamp-4 text-xs text-text-secondary">{h.snippet}</p>
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </main>

        {/* Lesebereich */}
        {selected && (
          <section className="flex min-w-0 flex-1 flex-col border-l border-border-light">
            <div className="flex items-center gap-2 border-b border-border-light px-3 py-2">
              <button type="button" onClick={() => setSelected(null)} className="rounded-md p-1 text-text-secondary hover:bg-surface-hover" title="Back to list" aria-label="Back to list">
                <ArrowLeft className="size-4" />
              </button>
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-semibold text-text-primary">{sel?.title ?? selected.path}</div>
                <div className="truncate text-[11px] text-text-tertiary">
                  {[sel?.typ, sel?.person && `${sel.person}${sel.rolle ? ` (${sel.rolle})` : ''}`, sel?.datum && fmtDate(sel.datum), sel?.ort].filter(Boolean).join(' · ')}
                </div>
              </div>
              {sel && (
                <StatusSelect
                  value={sel.document_status ?? 'active'}
                  onChange={(status) => changeStatus(sel.path, status)}
                  label={localize('com_ui_memory_document_status')}
                  disabled={statusSaving}
                />
              )}
              <a href={viewSrc.replace('&embed=1', '')} target="_blank" rel="noreferrer" className="rounded-md p-1.5 text-text-secondary hover:bg-surface-hover" title="Open in new tab">
                <Maximize2 className="size-4" />
              </a>
              <button type="button" onClick={() => remove(selected.path)} className="rounded-md p-1.5 text-text-secondary hover:bg-surface-hover hover:text-red-500" title="Delete" aria-label="Delete document">
                <Trash2 className="size-4" />
              </button>
            </div>
            <iframe key={`${viewSrc}:${sel?.status ?? ''}`} title={selected.path} src={viewSrc} className="min-h-0 w-full flex-1 border-0 bg-surface-primary" />
          </section>
        )}
      </div>

      {dragging && (
        <div className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center bg-black/40">
          <div className="rounded-xl border-2 border-dashed border-white px-8 py-6 text-lg font-medium text-white">
            Drop files here → {target}/
          </div>
        </div>
      )}

      {dialog === 'upload' && (
        <Modal title={localize('com_ui_upload_files')} onClose={() => !busy && setDialog(null)}>
          <form className="flex flex-col gap-3" onSubmit={(event) => { event.preventDefault(); void upload(); }}>
            <div className="grid grid-cols-[minmax(0,1fr)_8rem] gap-3 text-xs font-semibold text-text-secondary">
              <span>{localize('com_ui_memory_file_name')}</span>
              <span>{localize('com_ui_memory_document_status')}</span>
            </div>
            {uploads.map(({ file, status }, index) => (
              <div key={`${file.name}-${index}`} className="grid grid-cols-[minmax(0,1fr)_8rem] items-center gap-3">
                <span className="truncate text-sm" title={file.name}>{file.name}</span>
                <StatusSelect
                  value={status}
                  label={`${localize('com_ui_memory_document_status')} · ${file.name}`}
                  disabled={!!busy}
                  onChange={(next) => setUploads((current) => current.map((item, i) => i === index ? { ...item, status: next } : item))}
                />
              </div>
            ))}
            {uploadError && <p role="alert" className="text-sm text-red-500">{uploadError}</p>}
            <div className="flex justify-end gap-2">
              <button type="button" className={btnCls} disabled={!!busy} onClick={() => setDialog(null)}>
                {localize('com_ui_cancel')}
              </button>
              <button type="submit" className={primaryCls} disabled={!!busy || uploads.length === 0}>
                {busy ? localize('com_ui_memory_uploading') : localize('com_ui_upload')}
              </button>
            </div>
          </form>
        </Modal>
      )}
      {dialog === 'speech' && (
        <SpeechDialog
          onClose={() => setDialog(null)}
          onSaved={(p) => {
            setDialog(null);
            load();
            setSelected({ path: p });
          }}
        />
      )}
      {dialog === 'interview' && <InterviewDialog onClose={() => setDialog(null)} />}
    </div>,
    pane,
  );
}
