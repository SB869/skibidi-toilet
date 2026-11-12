import { api, type AudioFile } from "../api";

function clock(seconds: number): string {
  return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
}

/** A finished mix from the Tracks group: play it, seek it, download it. */
export function TrackView({ file, autoplay = false }: { file: AudioFile; autoplay?: boolean }) {
  return (
    <div className="track-view">
      <section className="card">
        <h3>{file.name}</h3>
        <dl className="facts">
          <dt>Length</dt><dd>{clock(file.duration)}</dd>
          <dt>Audio</dt><dd>{file.sample_rate} Hz, {file.channels === 1 ? "mono" : `${file.channels} channels`}</dd>
        </dl>
        <audio className="track-player" controls preload="metadata" autoPlay={autoplay} src={api.audioUrl(file.path)} />
        <p className="muted small">
          Rendered from the Beat tab's song form. To change it, pick the beatbox in the Beat tab, switch Form to song, edit, and ask for a new render.
          {" "}<a href={api.audioUrl(file.path)} download={file.name}>Download</a>
        </p>
      </section>
    </div>
  );
}
