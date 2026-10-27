import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type Library as LibraryData } from "./api";
import { Player } from "./audio";
import { useStored } from "./hooks";
import { BeatView } from "./components/BeatView";
import { CompareView } from "./components/CompareView";
import { Library } from "./components/Library";
import { RecordView } from "./components/RecordView";
import { TrackView } from "./components/TrackView";
import { TuneView } from "./components/TuneView";

type Tab = "tune" | "record" | "compare" | "beat" | "track";
interface Toast { id: number; text: string; error: boolean }

export function App() {
  const player = useMemo(() => new Player(), []);
  const [library, setLibrary] = useState<LibraryData | null>(null);
  const [selected, setSelected] = useStored<{ path: string | null }>("sound.selected", { path: null });
  const [tab, setTab] = useStored<{ tab: Tab }>("sound.tab", { tab: "tune" });
  const [currentRender, setCurrentRender] = useState<{ id: string; name: string } | null>(null);
  const [compareRef, setCompareRef] = useState<{ path: string; start?: number; end?: number } | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [offline, setOffline] = useState(false);

  const notify = useCallback((text: string, error = false) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, text, error }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), error ? 9000 : 3500);
  }, []);

  const refresh = useCallback(async (select?: string) => {
    try {
      setLibrary(await api.files());
      setOffline(false);
      if (select) {
        setSelected({ path: select });
        setTab({ tab: "tune" });
      }
    } catch {
      setOffline(true);
    }
  }, [setSelected, setTab]);

  useEffect(() => { void refresh(); }, [refresh]);

  // ?ardour opens the Record tab following the session open in Ardour (RecordView picks it up).
  useEffect(() => {
    if (new URLSearchParams(window.location.search).has("ardour")) setTab({ tab: "record" });
  }, []);

  // Space plays/pauses anywhere; T flips between original and tuned.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      if (el.closest("input, textarea, select, [contenteditable]")) return;
      if (e.code === "Space") {
        e.preventDefault();
        (document.activeElement as HTMLElement | null)?.blur();
        player.toggle();
      } else if ((e.key === "t" || e.key === "T") && player.has("b")) {
        player.setActive(player.active === "a" ? "b" : "a");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [player]);

  const file = library?.files.find((f) => f.path === selected.path) ?? null;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand"><img src="/favicon.svg" alt="" /> sound</div>
        <nav>
          <button className={tab.tab === "tune" ? "on" : ""} onClick={() => setTab({ tab: "tune" })}>Tune</button>
          <button className={tab.tab === "record" ? "on" : ""} onClick={() => setTab({ tab: "record" })}>Record</button>
          <button className={tab.tab === "compare" ? "on" : ""} onClick={() => setTab({ tab: "compare" })}>Compare</button>
          <button className={tab.tab === "beat" ? "on" : ""} onClick={() => setTab({ tab: "beat" })}>Beat</button>
          <button className={tab.tab === "track" ? "on" : ""} onClick={() => setTab({ tab: "track" })}>Track</button>
        </nav>
        <span className="spacer" />
        {offline && <span className="error small">Can't reach the server: is <code>sound serve</code> running?</span>}
      </header>

      <div className="body">
        <Library library={library} selected={selected.path} notify={notify} onChanged={refresh}
                 onSelect={(path) => {
                   setSelected({ path });
                   const kind = library?.files.find((f) => f.path === path)?.kind;
                   setTab({ tab: kind === "beatbox" ? "beat" : kind === "track" ? "track"
                                 : tab.tab === "beat" || tab.tab === "track" || tab.tab === "record" ? "tune" : tab.tab });
                 }}
                 onRecord={() => setTab({ tab: "record" })} />
        <main>
          {library && tab.tab === "tune" && (file ? (
            <TuneView key={file.path} file={file} library={library} player={player} notify={notify}
                      onLibraryChanged={refresh} onRender={setCurrentRender}
                      onUseAsReference={(ref) => { setCompareRef(ref); setTab({ tab: "compare" }); }} />
          ) : (
            <div className="empty-state">
              {library.files.length
                ? "Pick a recording from the library."
                : "Record a take, or drop audio files on the library to get started."}
            </div>
          ))}
          {library && tab.tab === "record" && (
            <RecordView library={library} notify={notify} onLibraryChanged={() => refresh()}
                        onOpen={(path) => { setSelected({ path }); setTab({ tab: "tune" }); }} />
          )}
          {library && tab.tab === "beat" && (file ? (
            <BeatView key={file.path} file={file} notify={notify} />
          ) : (
            <div className="empty-state">Pick a beatbox recording from the library (drop one on the Beatbox group).</div>
          ))}
          {library && tab.tab === "track" && (file?.kind === "track" ? (
            <TrackView key={file.path} file={file} />
          ) : (
            <div className="empty-state">Pick a track from the library's Tracks group: a finished mix, rendered from the Beat tab's song form.</div>
          ))}
          {library && tab.tab === "compare" && (
            <CompareView key={compareRef ? `${compareRef.path}@${compareRef.start}` : "compare"}
                         library={library} player={player} currentRender={currentRender}
                         initialRef={compareRef} notify={notify} />
          )}
        </main>
      </div>

      <div className="toasts">
        {toasts.map((t) => <div key={t.id} className={t.error ? "toast error" : "toast"}>{t.text}</div>)}
      </div>
    </div>
  );
}
