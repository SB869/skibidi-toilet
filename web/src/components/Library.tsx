import { useRef, useState } from "react";
import { api, type AudioFile, type Kind, type Library as LibraryData } from "../api";
import { fmtTime } from "../util";

interface Props {
  library: LibraryData | null;
  selected: string | null;
  onSelect: (path: string) => void;
  onChanged: (select?: string) => void;
  onRecord: () => void;
  notify: (message: string, error?: boolean) => void;
}

const GROUPS: { kind: Kind; title: string; hint: string }[] = [
  { kind: "take", title: "Takes", hint: "your recordings" },
  { kind: "reference", title: "References", hint: "original vocals to compare against" },
  { kind: "render", title: "Renders", hint: "saved tuned versions" },
  { kind: "beatbox", title: "Beatbox", hint: "beatbox recordings to turn into beats" },
  { kind: "track", title: "Tracks", hint: "finished mixes, rendered from the Beat tab" },
];

export function Library({ library, selected, onSelect, onChanged, onRecord, notify }: Props) {
  const [dropKind, setDropKind] = useState<Kind | null>(null);
  const [busy, setBusy] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const pickKind = useRef<Kind>("take");

  async function importFiles(files: FileList | File[], kind: Kind) {
    setBusy(true);
    let last: AudioFile | null = null;
    for (const file of Array.from(files)) {
      try {
        last = await api.upload(file, file.name, kind);
      } catch (e) {
        notify(`${file.name}: ${(e as Error).message}`, true);
      }
    }
    setBusy(false);
    if (last) {
      notify(`Imported ${last.name}`);
      onChanged(last.path);
    }
  }

  const files = library?.files ?? [];
  return (
    <aside className="library">
      <div className="library-actions">
        <button className="record" onClick={onRecord}>● Record</button>
        <button onClick={() => { pickKind.current = "take"; picker.current?.click(); }} disabled={busy}>
          {busy ? "Importing…" : "Import…"}
        </button>
        <input ref={picker} type="file" multiple hidden accept="audio/*,video/*,.m4a,.mp3,.wav,.flac,.ogg,.opus,.aac"
               onChange={(e) => { if (e.target.files) void importFiles(e.target.files, pickKind.current); e.target.value = ""; }} />
      </div>

      {GROUPS.map(({ kind, title, hint }) => {
        const group = files.filter((f) => f.kind === kind);
        return (
          <section
            key={kind}
            className={dropKind === kind ? "group dropping" : "group"}
            onDragOver={(e) => { if (kind !== "render") { e.preventDefault(); setDropKind(kind); } }}
            onDragLeave={() => setDropKind(null)}
            onDrop={(e) => {
              e.preventDefault();
              setDropKind(null);
              if (kind !== "render" && e.dataTransfer.files.length) void importFiles(e.dataTransfer.files, kind);
            }}
          >
            <header>
              <span>{title}</span>
              {kind !== "render" && (
                <button className="link" title={`Import ${title.toLowerCase()}`}
                        onClick={() => { pickKind.current = kind; picker.current?.click(); }}>+</button>
              )}
            </header>
            {group.length === 0 && <p className="empty">{kind === "render" ? hint : `Drop ${hint} here`}</p>}
            <ul>
              {group.map((f) => (
                <li key={f.path} className={f.path === selected ? "selected" : ""} title={f.name}
                    onClick={() => onSelect(f.path)}>
                  <span className="name">{f.name.replace(/\.[^.]+$/, "")}</span>
                  <span className="dur">{fmtTime(f.duration, 0)}</span>
                </li>
              ))}
            </ul>
          </section>
        );
      })}

      {library && <p className="root" title={library.root}>{library.root}</p>}
    </aside>
  );
}
