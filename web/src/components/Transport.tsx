import { useRef, type ReactNode } from "react";
import type { Player } from "../audio";
import { useFrame, usePlayer } from "../hooks";
import { fmtTime } from "../util";

interface Props {
  player: Player;
  selection: [number, number] | null;
  labels?: [string, string];  // names for slot a / slot b; omit for a single-track player
  children?: ReactNode;
}

export function Transport({ player, selection, labels, children }: Props) {
  const state = usePlayer(player);
  const clock = useRef<HTMLSpanElement>(null);
  useFrame(() => {
    if (clock.current) clock.current.textContent = fmtTime(player.position(), 1);
  });

  const looping = state.loop !== null;
  return (
    <div className="transport">
      <button className="play" onClick={() => player.toggle()} disabled={!state.duration}
              title="Play / pause (space)">
        {state.playing ? "❚❚" : "▶"}
      </button>
      <span className="clock">
        <span ref={clock}>0:00.0</span>
        <span className="muted"> / {fmtTime(state.duration, 1)}</span>
      </span>

      {labels && (
        <div className="segmented" title="Switch what you hear without stopping (T)">
          <button className={state.active === "a" ? "on" : ""} onClick={() => player.setActive("a")}>
            {labels[0]}
          </button>
          <button className={state.active === "b" ? "on" : ""} onClick={() => player.setActive("b")}
                  disabled={!state.hasB}>
            {labels[1]}
          </button>
        </div>
      )}

      <button className={looping ? "toggle on" : "toggle"} disabled={!selection && !looping}
              onClick={() => player.setLoop(looping ? null : selection)} title="Loop the selection (L)">
        ⟲ Loop
      </button>
      {selection && (
        <span className="muted small">
          selection {fmtTime(selection[0])}–{fmtTime(selection[1])} ({(selection[1] - selection[0]).toFixed(1)}s)
        </span>
      )}
      <span className="spacer" />
      {children}
    </div>
  );
}
