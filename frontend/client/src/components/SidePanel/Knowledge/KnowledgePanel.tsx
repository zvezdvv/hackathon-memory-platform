/**
 * Smart City: "Memory" tab as a knowledge file system.
 *
 * Replaces the built-in Memories panel. Data comes from the knowledge server (service
 * "knowledge"): real folders, automatic RAG index, viewer with highlighted passages and
 * three inputs (files, speech, interview).
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useQuery } from '@tanstack/react-query';
import { QueryKeys, getTokenHeader } from 'librechat-data-provider';
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
  Search,
  Square,
  Trash2,
  Upload,
  X,
} from 'lucide-react';
import type { ReactNode } from 'react';
import { DEFAULT_PANEL, MEMORY_PANE_ID, useActivePanel } from '~/Providers';
import OpenSidebar from '~/components/Chat/Menus/OpenSidebar';
import useSidebarState from '~/hooks/Nav/useSidebarState';
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
  path: string;
  type: 'dir' | 'file';
  children?: TreeNode[];
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

function folderPaths(node: TreeNode): string[] {
  if (node.type === 'file') return [];
  return [...(node.path ? [node.path] : []), ...(node.children ?? []).flatMap(folderPaths)];
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
const listSelectCls =
  'rounded-md border border-border-light bg-surface-secondary py-1 ps-2 pe-7 text-xs text-text-primary';
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

type SpeechResult = { path: string; transcript: string; structured: boolean; warning: string };

function fmtSeconds(s: number) {
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

const LIVE_INTERVAL_MS = 3000;

function recordingName(type: string) {
  const ext = type.includes('mp4') ? 'm4a' : type.includes('ogg') ? 'ogg' : 'webm';
  return `recording.${ext}`;
}

/**
 * Voice note: while recording, the audio is transcribed live with ElevenLabs Scribe v2 so the
 * speaker sees the text. After stopping, a final transcript can be corrected. "Save" passes it
 * to the LLM, which structures it and fills in the metadata → saved under speech/ (active).
 */
function SpeechDialog({ onClose, onSaved }: { onClose: () => void; onSaved: (path: string, warning: string) => void }) {
  const [title, setTitle] = useState('');
  const [person, setPerson] = useState('');
  const [text, setText] = useState('');
  const [recording, setRecording] = useState(false);
  const [requestingMic, setRequestingMic] = useState(false);
  const [finalizing, setFinalizing] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [clip, setClip] = useState<Blob | null>(null);
  const [clipUrl, setClipUrl] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const recRef = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const timer = useRef<number | undefined>(undefined);
  const live = useRef<number | undefined>(undefined);
  const inflight = useRef(false);
  const mounted = useRef(false);

  const supported = typeof window.MediaRecorder !== 'undefined' && !!navigator.mediaDevices?.getUserMedia;

  useEffect(() => {
    const url = clip ? URL.createObjectURL(clip) : '';
    setClipUrl(url);
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [clip]);

  const transcribe = async (blob: Blob): Promise<string> => {
    const fd = new FormData();
    fd.append('audio', blob, recordingName(blob.type));
    const res = await kb<{ text: string }>('/api/transcribe', { method: 'POST', body: fd });
    return res.text;
  };

  // Live transcript: re-transcribe everything recorded so far every few seconds
  const liveTick = async (type: string) => {
    if (inflight.current || !chunks.current.length) return;
    inflight.current = true;
    try {
      const textSoFar = await transcribe(new Blob(chunks.current, { type }));
      if (mounted.current && recRef.current?.state === 'recording') setText(textSoFar);
    } catch {
      /* live preview is best effort – the final transcript runs after stopping */
    } finally {
      inflight.current = false;
    }
  };

  const start = async () => {
    setError('');
    setRequestingMic(true);
    let stream: MediaStream | undefined;
    try {
      const input = await navigator.mediaDevices.getUserMedia({ audio: true });
      stream = input;
      if (!mounted.current) {
        input.getTracks().forEach((track) => track.stop());
        return;
      }
      const rec = new MediaRecorder(input);
      chunks.current = [];
      rec.ondataavailable = (e) => e.data.size && chunks.current.push(e.data);
      rec.onstop = async () => {
        input.getTracks().forEach((t) => t.stop());
        window.clearInterval(timer.current);
        window.clearInterval(live.current);
        setRecording(false);
        const full = new Blob(chunks.current, { type: rec.mimeType || 'audio/webm' });
        setClip(full);
        if (!full.size) return;
        setFinalizing(true);
        try {
          const finalText = await transcribe(full);
          if (mounted.current) setText(finalText);
        } catch (e) {
          if (mounted.current) setError(`Transcription failed: ${(e as Error).message}`);
        } finally {
          if (mounted.current) setFinalizing(false);
        }
      };
      recRef.current = rec;
      rec.start(1000);
      setClip(null);
      setText('');
      setSeconds(0);
      timer.current = window.setInterval(() => setSeconds((x) => x + 1), 1000);
      live.current = window.setInterval(() => liveTick(rec.mimeType || 'audio/webm'), LIVE_INTERVAL_MS);
      setRecording(true);
    } catch {
      stream?.getTracks().forEach((track) => track.stop());
      if (mounted.current) setError('Microphone access was denied or no microphone is available.');
    } finally {
      if (mounted.current) setRequestingMic(false);
    }
  };
  const stop = () => recRef.current?.state === 'recording' && recRef.current.stop();

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      window.clearInterval(timer.current);
      window.clearInterval(live.current);
      const recorder = recRef.current;
      if (recorder) {
        recorder.onstop = null;
        if (recorder.state === 'recording') recorder.stop();
        recorder.stream.getTracks().forEach((track) => track.stop());
      }
    };
  }, []);

  const save = async () => {
    setError('');
    const fd = new FormData();
    fd.append('title', title);
    fd.append('person', person);
    fd.append('document_status', 'active');
    fd.append('text', text);
    if (clip?.size) fd.append('audio', clip, recordingName(clip.type));
    setBusy('Structuring the note and filling in metadata …');
    try {
      const res = await kb<SpeechResult>('/api/speech', { method: 'POST', body: fd });
      onSaved(res.path, res.warning);
    } catch (e) {
      setError(String((e as Error).message));
    } finally {
      setBusy('');
    }
  };

  const ready = !!text.trim() && !recording && !requestingMic && !finalizing;

  return (
    <Modal title="Voice note" onClose={busy ? () => undefined : onClose}>
      <div className="flex flex-col gap-3">
        <div className="flex items-center gap-4">
          {recording ? (
            <button type="button" onClick={stop} className="flex size-14 shrink-0 items-center justify-center rounded-full bg-red-500 text-white shadow-lg hover:bg-red-600" aria-label="Stop recording">
              <Square className="size-5 fill-current" />
            </button>
          ) : (
            <button type="button" onClick={start} disabled={!supported || requestingMic || finalizing || !!busy} className="flex size-14 shrink-0 items-center justify-center rounded-full bg-green-600 text-white shadow-lg hover:bg-green-700 disabled:opacity-50" aria-label="Start recording">
              <Mic className="size-6" />
            </button>
          )}
          <div className="min-w-0 text-sm">
            {recording ? (
              <span className="flex items-center gap-1.5 font-medium text-red-500">
                <span className="size-2 animate-pulse rounded-full bg-red-500" /> Recording {fmtSeconds(seconds)} · live transcript
              </span>
            ) : finalizing ? (
              <span className="flex items-center gap-1.5 text-text-secondary"><Loader2 className="size-3.5 animate-spin" /> Finalizing transcript …</span>
            ) : clip?.size ? (
              <span className="text-text-secondary">Done ({fmtSeconds(seconds)}) – check the text, then save.</span>
            ) : (
              <span className="text-text-secondary">
                {supported ? 'Press the microphone and just talk – you will see the text appear.' : 'Recording is not supported in this browser – type your note instead.'}
              </span>
            )}
          </div>
        </div>

        <textarea
          className={cn(inputCls, 'min-h-44 resize-y leading-relaxed', recording && 'border-red-500/50')}
          placeholder={recording ? 'Listening …' : 'The transcript appears here while you speak. You can also type or correct it.'}
          value={text}
          onChange={(e) => setText(e.target.value)}
          readOnly={recording || finalizing}
          aria-label="Transcript"
        />
        {clipUrl && !recording && <audio controls src={clipUrl} className="w-full" />}

        <div className="grid grid-cols-2 gap-2">
          <input className={inputCls} placeholder="Title (optional)" value={title} onChange={(e) => setTitle(e.target.value)} />
          <input className={inputCls} placeholder="Your name (optional)" value={person} onChange={(e) => setPerson(e.target.value)} />
        </div>
        {busy && (
          <p className="flex items-center gap-1.5 text-xs text-text-secondary">
            <Loader2 className="size-3.5 animate-spin" /> {busy}
          </p>
        )}
        {error && <p className="text-xs text-red-500">{error}</p>}
        <div className="flex items-center justify-between gap-2">
          <span className="text-[11px] text-text-tertiary">Saved as active · structured by an LLM</span>
          <div className="flex gap-2">
            <button type="button" className={btnCls} onClick={onClose} disabled={!!busy}>Cancel</button>
            <button type="button" className={primaryCls} onClick={save} disabled={!ready || !!busy}>
              {busy && <Loader2 className="size-4 animate-spin" />} Save note
            </button>
          </div>
        </div>
      </div>
    </Modal>
  );
}

/* ------------------------------------------------------------------ Interview */

function InterviewDialog({ onClose }: { onClose: () => void }) {
  const localize = useLocalize();
  const [people, setPeople] = useState<Person[]>([]);
  const [person, setPerson] = useState('');
  const [topic, setTopic] = useState('');
  const [status, setStatus] = useState<DocumentStatus>('active');

  useEffect(() => {
    kb<Person[]>('/api/people').then(setPeople).catch(() => setPeople([]));
  }, []);

  const start = () => {
    const prompt = [
      'Start the knowledge interview.',
      `Person: ${person.trim()}`,
      `Topic: ${topic.trim()}`,
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
          Each interview is a short session of about 5–7 questions. Sessions build on each other automatically: first a typical operation step by step, then exceptions and incidents, then a check of the documents against real practice. All protocols are saved together under Interviews.
        </p>
        <label className="text-xs font-medium text-text-secondary">Person</label>
        <input className={inputCls} list="kb-people" placeholder="e.g. Klaus Behrens" value={person} onChange={(e) => setPerson(e.target.value)} />
        <datalist id="kb-people">
          {people.map((p) => <option key={p.slug} value={p.name}>{`${p.sessions} session(s)`}</option>)}
        </datalist>
        <label className="text-xs font-medium text-text-secondary">Topic</label>
        <input className={inputCls} placeholder="e.g. Black ice operations at night" value={topic} onChange={(e) => setTopic(e.target.value)} />
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

/** Topic folders are slugs (e.g. "uebergabe-winterdienst") – show them as readable names. */
function topicLabel(slug: string) {
  const text = slug.replace(/[-_]+/g, ' ').trim();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function folderLabel(path: string) {
  if (!path) return 'All documents';
  const top = path.split('/')[0];
  return path === top ? FOLDER_LABEL[top] ?? top : topicLabel(path.split('/').slice(-1)[0]);
}

function folderPathLabel(path: string) {
  const [top, ...children] = path.split('/');
  return [folderLabel(top), ...children].join(' › ');
}

function acceptsUploads(path: string) {
  return path !== '_guides' && !path.startsWith('_guides/');
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
  const [hits, setHits] = useState<SearchHit[] | 'error' | null>(null);
  const [selected, setSelected] = useState<{ path: string; hl?: string } | null>(null);
  const [dialog, setDialog] = useState<'upload' | 'speech' | 'interview' | null>(null);
  const [uploads, setUploads] = useState<PendingUpload[]>([]);
  const [uploadError, setUploadError] = useState('');
  const [statusSaving, setStatusSaving] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [showFilters, setShowFilters] = useState(!isSmallScreen);
  const fileInput = useRef<HTMLInputElement>(null);
  const viewer = useRef<HTMLIFrameElement>(null);
  const listScroll = useRef<HTMLDivElement>(null);
  const { data: directories, refetch: loadFolders } = useQuery(
    [QueryKeys.memoryFolders, KB_URL],
    () => kb<TreeNode>('/api/tree'),
    { select: folderPaths, retry: false },
  );

  useLayoutEffect(() => {
    if (listScroll.current) listScroll.current.scrollTop = 0;
  }, [folder, typ, person, sort, group]);

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
      if (e.origin !== KB_ORIGIN || e.source !== viewer.current?.contentWindow || e.data?.type !== 'kb-token-request') return;
      (e.source as Window | null)?.postMessage({ type: 'kb-token', token: getTokenHeader() ?? '' }, e.origin);
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, []);

  // Search (debounced)
  const searchQuery = query.trim();
  useEffect(() => {
    setHits(null);
    if (!searchQuery) return;

    const controller = new AbortController();
    const params = new URLSearchParams({ q: searchQuery, k: '12', folder, typ, person });
    const t = window.setTimeout(async () => {
      try {
        const res = await kb<{ results: SearchHit[] }>(
          `/api/search?${params}`,
          { signal: controller.signal },
        );
        if (!controller.signal.aborted) setHits(res.results);
      } catch {
        if (!controller.signal.aborted) setHits('error');
      }
    }, 350);
    return () => {
      window.clearTimeout(t);
      controller.abort();
    };
  }, [searchQuery, folder, typ, person]);

  const all = docs ?? [];
  const byPath = useMemo(() => new Map(all.map((d) => [d.path, d])), [all]);
  const visibleHits = Array.isArray(hits)
    ? hits.filter((hit) => (byPath.get(hit.path)?.document_status ?? 'active') === 'active')
    : [];

  const folders = useMemo(() => {
    const set = new Map<string, number>();
    for (const path of directories ?? []) set.set(path, 0);
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
  }, [all, directories]);

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
  const target = folder && acceptsUploads(folder) ? folder : 'files';

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

  const upload = async (fd: FormData) => {
    if (busy || !fd.get('folder') || !uploads.length) return;
    setBusy(localize('com_ui_memory_uploading'));
    setUploadError('');
    try {
      uploads.forEach(({ file, status }) => {
        fd.append('files', file);
        fd.append('document_statuses', status);
      });
      const res = await kb<{ saved: string[] }>('/api/upload', { method: 'POST', body: fd });
      await load();
      setDialog(null);
      setUploads([]);
      const [path] = res.saved;
      if (path) {
        setFolder(path.slice(0, path.lastIndexOf('/')));
        setTyp('');
        setPerson('');
        setQuery('');
        setHits(null);
        setSelected({ path });
        listScroll.current?.scrollTo({ top: 0 });
      }
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

  const removeFolder = async (path: string, count: number) => {
    const what = count
      ? `the folder “${folderPathLabel(path)}” and its ${count} document(s)`
      : `the empty folder “${folderPathLabel(path)}”`;
    if (!window.confirm(`Delete ${what}? This cannot be undone.`)) return;
    try {
      await kb(`/api/item?path=${encodeURIComponent(path)}`, { method: 'DELETE' });
      if (folder === path || folder.startsWith(`${path}/`)) setFolder('');
      await Promise.all([loadFolders(), load()]);
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
          {d.folder && <span className="truncate">{folderPathLabel(d.folder)}</span>}
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
      <header className="flex shrink-0 flex-wrap items-center gap-3 border-b border-border-light px-4 py-2.5">
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
        <img src="assets/stadtgedaechtnis.svg" className="size-9 shrink-0" alt="" />
        <div className="mr-2">
          <h1 id="memory-overlay-title" className="text-base font-semibold text-text-primary">
            Memory <span className="font-normal text-text-secondary">· Stadtgedächtnis</span>
          </h1>
          <p className="text-xs text-text-secondary">
            {docs ? `${stats.total} documents · ${stats.indexed} indexed in RAG` : 'loading …'}
          </p>
        </div>
        <div className="relative min-w-48 flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 size-4 text-text-tertiary" />
          <input
            className={cn(inputCls, 'pl-8 pr-8')}
            placeholder={folder ? `Search in “${folderPathLabel(folder)}” …` : 'Search all knowledge …'}
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
            <div className="mb-1 px-2">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary">Folders</h3>
            </div>
            <Chip active={!folder} onClick={() => setFolder('')} count={all.length}>All documents</Chip>
            {folders.map(([f, n]) => (
              <div key={f} className="group relative" style={{ paddingLeft: `${(f.split('/').length - 1) * 12}px` }}>
                <Chip active={folder === f} onClick={() => setFolder(f)} count={n}>
                  <span className="inline-flex items-center gap-1.5">
                    <Folder className="size-3.5 text-amber-500" /> {folderLabel(f)}
                  </span>
                </Chip>
                {!TOP.includes(f) && (
                  <button
                    type="button"
                    onClick={() => removeFolder(f, n)}
                    className="absolute right-1 top-1/2 hidden -translate-y-1/2 rounded bg-surface-primary p-1 text-text-tertiary hover:text-red-500 group-hover:block"
                    title="Delete folder"
                    aria-label={`Delete folder ${folderLabel(f)}`}
                  >
                    <Trash2 className="size-3" />
                  </button>
                )}
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
        <main className={cn('min-h-0 min-w-0 flex-col overflow-hidden', selected ? 'hidden w-[22rem] flex-none md:flex' : 'flex flex-1')}>
          {!searchQuery ? (
            <>
              <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border-light px-4 py-2 text-xs text-text-secondary">
                <span className="mr-auto">
                  {filtered.length} document{filtered.length === 1 ? '' : 's'}
                  {folder && ` in “${folderPathLabel(folder)}”`}
                  {typ && ` · ${typ}`}
                  {person && ` · ${person}`}
                </span>
                <select className={listSelectCls} value={sort} onChange={(e) => setSort(e.target.value as SortKey)} aria-label="Sort">
                  {SORTS.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
                </select>
                <select className={listSelectCls} value={group} onChange={(e) => setGroup(e.target.value as GroupKey)} aria-label="Group">
                  {GROUPS.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
                </select>
              </div>
              <div
                data-testid="memory-column-header"
                className={cn('hidden shrink-0 grid-cols-[auto_minmax(0,1fr)_6rem_6rem_8rem_6rem] gap-x-3 border-b border-border-light bg-surface-primary px-4 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-text-tertiary', !compact && 'md:grid')}
              >
                <span className="w-2" />
                <span>Title</span>
                <span>Type</span>
                <span>{localize('com_ui_memory_document_status')}</span>
                <span>Person</span>
                <span>Date</span>
              </div>
              <div
                ref={listScroll}
                data-testid="memory-document-list"
                className="isolate min-h-0 flex-1 overflow-x-hidden overflow-y-auto"
              >
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
              <p role="status" className="mb-3 text-xs text-text-secondary">
                {hits === null && localize('com_ui_memory_searching')}
                {hits === 'error' && localize('com_ui_memory_search_error')}
                {Array.isArray(hits) && (visibleHits.length
                  ? localize('com_ui_memory_search_passages', { 0: String(visibleHits.length), 1: searchQuery })
                  : localize('com_ui_no_search_results'))}
              </p>
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
          <section className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden border-l border-border-light">
            <div className="flex shrink-0 flex-col gap-1.5 border-b border-border-light px-3 py-2">
              <div className="flex min-w-0 items-center gap-2">
              <button type="button" onClick={() => setSelected(null)} className="rounded-md p-1 text-text-secondary hover:bg-surface-hover" title="Back to list" aria-label="Back to list">
                <ArrowLeft className="size-4" />
              </button>
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-semibold text-text-primary">{sel?.title ?? selected.path}</div>
                <div className="truncate text-[11px] text-text-tertiary">
                  {[sel?.folder && folderPathLabel(sel.folder), sel?.typ, sel?.person && `${sel.person}${sel.rolle ? ` (${sel.rolle})` : ''}`, sel?.datum && fmtDate(sel.datum), sel?.ort].filter(Boolean).join(' · ')}
                </div>
              </div>
              </div>
              <div className="flex flex-wrap items-center gap-1.5 pl-7">
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
            </div>
            <iframe ref={viewer} key={`${viewSrc}:${sel?.status ?? ''}`} title={selected.path} src={viewSrc} className="min-h-0 w-full flex-1 border-0 bg-surface-primary" />
          </section>
        )}
      </div>

      {dragging && (
        <div className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center bg-black/40">
          <div className="rounded-xl border-2 border-dashed border-white px-8 py-6 text-lg font-medium text-white">
            Drop files here → {folderPathLabel(target)}
          </div>
        </div>
      )}

      {dialog === 'upload' && (
        <Modal title={localize('com_ui_upload_files')} onClose={() => !busy && setDialog(null)}>
          <form
            className="flex flex-col gap-3"
            onSubmit={(event) => {
              event.preventDefault();
              void upload(new FormData(event.currentTarget));
            }}
          >
            <label className="flex flex-col gap-1 text-xs font-medium text-text-secondary">
              <span>{localize('com_ui_memory_upload_folder')}</span>
              <select name="folder" className={inputCls} defaultValue={target} disabled={!!busy}>
                {folders.filter(([path]) => acceptsUploads(path)).map(([path]) => (
                  <option key={path} value={path}>{folderPathLabel(path)}</option>
                ))}
              </select>
            </label>
            <p className="text-xs text-text-secondary">{localize('com_ui_memory_upload_constraints')}</p>
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
          onSaved={(p, warning) => {
            setDialog(null);
            void load().then(() => { if (warning) setError(warning); });
            setSelected({ path: p });
          }}
        />
      )}
      {dialog === 'interview' && <InterviewDialog onClose={() => setDialog(null)} />}
    </div>,
    pane,
  );
}
