import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { Player } from "./audio";

/** Re-render when the player starts, stops, switches slot or changes loop. */
export function usePlayer(player: Player) {
  const snapshot = useRef({ playing: false, active: player.active, loop: player.loop, hasB: false, duration: 0 });
  return useSyncExternalStore(
    (fn) => player.subscribe(fn),
    () => {
      const s = snapshot.current;
      const hasB = player.has("b");
      if (s.playing !== player.playing || s.active !== player.active || s.loop !== player.loop ||
          s.hasB !== hasB || s.duration !== player.duration) {
        snapshot.current = { playing: player.playing, active: player.active, loop: player.loop, hasB,
                             duration: player.duration };
      }
      return snapshot.current;
    },
  );
}

/** Element size, kept up to date with a ResizeObserver. */
export function useSize<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setSize({ width: Math.round(width), height: Math.round(height) });
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, size] as const;
}

/** Run fn on every animation frame while mounted. */
export function useFrame(fn: () => void) {
  const latest = useRef(fn);
  latest.current = fn;
  useEffect(() => {
    let id = requestAnimationFrame(function tick() {
      latest.current();
      id = requestAnimationFrame(tick);
    });
    return () => cancelAnimationFrame(id);
  }, []);
}

/** A value kept in localStorage (falls back to in-memory if storage is unavailable). */
export function useStored<T>(key: string, initial: T) {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key);
      if (raw === null) return initial;
      const parsed = JSON.parse(raw);
      // Merge objects so settings added later still get their defaults.
      return typeof initial === "object" && initial !== null && !Array.isArray(initial)
        ? { ...initial, ...parsed }
        : parsed;
    } catch {
      return initial;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      // storage blocked: settings just won't persist
    }
  }, [key, value]);
  return [value, setValue] as const;
}
